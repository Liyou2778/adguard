package com.adguard.controlplane.repo;

import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/** Appeals, review evidence and DPO feedback pairs (M8 / FR-10). */
@Repository
public class AppealRepository {

    public record AppealRow(String id, String judgmentId, String assetId, String tenantId, String reason,
                            String detail, String status, String originalDecision, String finalDecision,
                            String reviewer, String reviewComment, String reviewedByUserId,
                            Instant createdAt, Instant reviewedAt) {
    }

    public record JudgmentRow(String id, String assetId, String tenantId, String decision, String reason,
                              String modelVersion, Integer latencyMs, Long costMicro, Instant createdAt) {
    }

    public record AssetRow(String id, String tenantId, String type, String filename, long sizeBytes,
                           String mimeType, String caption, String parseStatus) {
    }

    private static final RowMapper<AppealRow> APPEAL_MAPPER = (rs, rowNum) -> new AppealRow(
            rs.getString("id"),
            rs.getString("judgment_id"),
            rs.getString("asset_id"),
            rs.getString("tenant_id"),
            rs.getString("reason"),
            rs.getString("detail"),
            rs.getString("status"),
            rs.getString("original_decision"),
            rs.getString("final_decision"),
            rs.getString("reviewer"),
            rs.getString("reviewer_comment"),
            rs.getString("reviewed_by_user_id"),
            toInstant(rs.getTimestamp("created_at")),
            toInstant(rs.getTimestamp("reviewed_at")));

    private final JdbcTemplate jdbc;

    public AppealRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<AppealRow> findById(String id) {
        try {
            return Optional.ofNullable(jdbc.queryForObject("SELECT * FROM appeal WHERE id = ?",
                    APPEAL_MAPPER, id));
        } catch (EmptyResultDataAccessException e) {
            return Optional.empty();
        }
    }

    public Optional<AppealRow> findOpenByJudgment(String judgmentId) {
        List<AppealRow> rows = jdbc.query(
                "SELECT * FROM appeal WHERE judgment_id = ? AND status = 'open' LIMIT 1",
                APPEAL_MAPPER, judgmentId);
        return rows.isEmpty() ? Optional.empty() : Optional.of(rows.get(0));
    }

    public void insert(String id, String judgmentId, String assetId, String tenantId, String reason,
                       String detail, String originalDecision) {
        jdbc.update("INSERT INTO appeal (id, judgment_id, asset_id, tenant_id, reason, detail, status, "
                        + "original_decision) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                id, judgmentId, assetId, tenantId, reason, detail, originalDecision);
    }

    public void updateReview(String id, String status, String finalDecision, String reviewer,
                             String reviewedByUserId, String comment) {
        jdbc.update("UPDATE appeal SET status = ?, final_decision = ?, reviewer = ?, "
                        + "reviewed_by_user_id = ?, reviewer_comment = ?, reviewed_at = CURRENT_TIMESTAMP(3) "
                        + "WHERE id = ?",
                status, finalDecision, reviewer, reviewedByUserId, comment, id);
    }

    public List<AppealRow> listByTenant(String tenantId, String status, int limit) {
        if (status == null || status.isBlank()) {
            return jdbc.query("SELECT * FROM appeal WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                    APPEAL_MAPPER, tenantId, limit);
        }
        return jdbc.query("SELECT * FROM appeal WHERE tenant_id = ? AND status = ? "
                + "ORDER BY created_at DESC LIMIT ?", APPEAL_MAPPER, tenantId, status, limit);
    }

    public List<AppealRow> listForAdmin(String tenantId, String status, int limit) {
        StringBuilder sql = new StringBuilder("SELECT * FROM appeal WHERE 1=1");
        List<Object> args = new ArrayList<>();
        if (tenantId != null && !tenantId.isBlank()) {
            sql.append(" AND tenant_id = ?");
            args.add(tenantId);
        }
        if (status != null && !status.isBlank()) {
            sql.append(" AND status = ?");
            args.add(status);
        }
        sql.append(" ORDER BY created_at DESC LIMIT ?");
        args.add(limit);
        return jdbc.query(sql.toString(), APPEAL_MAPPER, args.toArray());
    }

    public Optional<JudgmentRow> findJudgment(String tenantId, String judgmentId) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(
                    "SELECT id, asset_id, tenant_id, decision, reason, model_version, latency_ms, "
                            + "cost_micro, created_at FROM judgment WHERE id = ? AND tenant_id = ?",
                    (rs, rowNum) -> new JudgmentRow(rs.getString("id"), rs.getString("asset_id"),
                            rs.getString("tenant_id"), rs.getString("decision"), rs.getString("reason"),
                            rs.getString("model_version"), (Integer) rs.getObject("latency_ms"),
                            rs.getObject("cost_micro") == null ? null : rs.getLong("cost_micro"),
                            toInstant(rs.getTimestamp("created_at"))),
                    judgmentId, tenantId));
        } catch (EmptyResultDataAccessException e) {
            return Optional.empty();
        }
    }

    public List<Map<String, Object>> listViolations(String judgmentId) {
        return jdbc.queryForList("SELECT rule_id, family, canonical, span_start, span_end, bbox, "
                + "evidence_text, confidence, clause_id, regulation_id, article, item_label, clause_text, "
                + "penalty_law_ref, penalty_min, penalty_max FROM violation WHERE judgment_id = ? "
                + "ORDER BY rule_id", judgmentId);
    }

    public Optional<AssetRow> findAsset(String tenantId, String assetId) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(
                    "SELECT id, tenant_id, type, filename, size_bytes, mime_type, caption, parse_status "
                            + "FROM asset WHERE id = ? AND tenant_id = ?",
                    (rs, rowNum) -> new AssetRow(rs.getString("id"), rs.getString("tenant_id"),
                            rs.getString("type"), rs.getString("filename"), rs.getLong("size_bytes"),
                            rs.getString("mime_type"), rs.getString("caption"),
                            rs.getString("parse_status")),
                    assetId, tenantId));
        } catch (EmptyResultDataAccessException e) {
            return Optional.empty();
        }
    }

    public void insertFeedbackPair(String id, String judgmentId, String appealId, String tenantId,
                                   String source, String family, String chosenJson, String rejectedJson,
                                   String reviewComment) {
        jdbc.update("INSERT INTO feedback_pair (id, judgment_id, appeal_id, tenant_id, source, chosen, "
                        + "rejected, family, review_comment, used_in_dpo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                id, judgmentId, appealId, tenantId, source, chosenJson, rejectedJson, family, reviewComment);
    }

    public List<Map<String, Object>> listFeedbackPairs(Boolean usedInDpo, int limit) {
        if (usedInDpo == null) {
            return jdbc.queryForList("SELECT * FROM feedback_pair ORDER BY created_at DESC LIMIT ?", limit);
        }
        return jdbc.queryForList("SELECT * FROM feedback_pair WHERE used_in_dpo = ? "
                + "ORDER BY created_at DESC LIMIT ?", usedInDpo ? 1 : 0, limit);
    }

    public int markFeedbackUsed(String id) {
        return jdbc.update("UPDATE feedback_pair SET used_in_dpo = 1, used_in_dpo_at = CURRENT_TIMESTAMP(3) "
                + "WHERE id = ? AND used_in_dpo = 0", id);
    }

    public Map<String, Object> judgmentEvidence(String judgmentId) {
        Map<String, Object> evidence = new LinkedHashMap<>();
        evidence.put("violations", listViolations(judgmentId));
        return evidence;
    }

    public String newId() {
        return UUID.randomUUID().toString();
    }

    private static Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}
