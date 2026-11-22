package com.adguard.controlplane.repo;

import com.adguard.controlplane.domain.RuleDefinition;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Persistence for {@code rule} / {@code rule_version}.
 *
 * <p>Deliberately plain JDBC (no ORM): the queries are trivial, the schema is
 * explicit, and every statement is easy to explain in a design review.
 */
@Repository
public class RuleRepository {

    private static final RowMapper<RuleRow> RULE_MAPPER = (rs, i) -> new RuleRow(
            rs.getString("rule_id"),
            rs.getString("tenant_id"),
            rs.getString("law"),
            rs.getString("article"),
            rs.getString("item"),
            rs.getString("family"),
            rs.getString("severity"),
            rs.getString("status"),
            rs.getInt("current_version"),
            nullableInt(rs.getObject("active_version")),
            toLocalDateTime(rs.getTimestamp("updated_at")));

    private static final RowMapper<RuleVersionRow> VERSION_MAPPER = (rs, i) -> new RuleVersionRow(
            rs.getInt("version"),
            rs.getString("status"),
            rs.getString("created_by"),
            toLocalDateTime(rs.getTimestamp("created_at")),
            rs.getString("payload"));

    private final JdbcTemplate jdbc;

    public RuleRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** Serializes snapshot creation per tenant (row lock on the tenant row). */
    public boolean lockTenant(String tenantId) {
        Boolean locked = jdbc.query(
                "SELECT id FROM tenant WHERE id = ? FOR UPDATE",
                rs -> rs.next() ? Boolean.TRUE : Boolean.FALSE,
                tenantId);
        return Boolean.TRUE.equals(locked);
    }

    public void upsertTenant(String tenantId) {
        jdbc.update("INSERT INTO tenant (id, name) VALUES (?, ?) "
                + "ON DUPLICATE KEY UPDATE name = VALUES(name)", tenantId, tenantId);
    }

    public boolean exists(String tenantId, String ruleId) {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM rule WHERE tenant_id = ? AND rule_id = ?",
                Integer.class, tenantId, ruleId);
        return count != null && count > 0;
    }

    public void insertRule(String tenantId, RuleDefinition def) {
        Integer penaltyMin = def.penalty() == null ? null : def.penalty().min();
        Integer penaltyMax = def.penalty() == null ? null : def.penalty().max();
        jdbc.update("""
                        INSERT INTO rule (rule_id, tenant_id, law, article, item, family, severity,
                                          penalty_min, penalty_max, effective_from, effective_to,
                                          source_url, current_version, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'draft')
                        """,
                def.ruleId(), tenantId, def.law(), def.article(), def.item(), def.family(),
                def.severity(), penaltyMin, penaltyMax, def.effectiveFrom(), def.effectiveTo(),
                def.sourceUrl());
    }

    public void insertVersion(String tenantId, String ruleId, int version, String payloadJson,
                              String status, String createdBy) {
        jdbc.update("""
                        INSERT INTO rule_version (id, rule_id, tenant_id, version, payload, status, created_by)
                        VALUES (UUID(), ?, ?, ?, ?, ?, ?)
                        """,
                ruleId, tenantId, version, payloadJson, status, createdBy);
    }

    public void bumpCurrentVersion(String tenantId, String ruleId, int version) {
        jdbc.update("UPDATE rule SET current_version = ? WHERE tenant_id = ? AND rule_id = ?",
                version, tenantId, ruleId);
    }

    /** Activates a version; the previously active version is retired in the same transaction. */
    public void activate(String tenantId, String ruleId, int version) {
        jdbc.update("UPDATE rule_version SET status = 'retired' "
                        + "WHERE tenant_id = ? AND rule_id = ? AND status = 'active' AND version <> ?",
                tenantId, ruleId, version);
        jdbc.update("UPDATE rule_version SET status = 'active' "
                        + "WHERE tenant_id = ? AND rule_id = ? AND version = ?",
                tenantId, ruleId, version);
        jdbc.update("UPDATE rule SET active_version = ?, status = 'active' "
                        + "WHERE tenant_id = ? AND rule_id = ?",
                version, tenantId, ruleId);
    }

    public Optional<RuleRow> find(String tenantId, String ruleId) {
        List<RuleRow> rows = jdbc.query(
                "SELECT * FROM rule WHERE tenant_id = ? AND rule_id = ?",
                RULE_MAPPER, tenantId, ruleId);
        return rows.stream().findFirst();
    }

    public List<RuleRow> list(String tenantId, String status, String family, int limit, int offset) {
        StringBuilder sql = new StringBuilder("SELECT * FROM rule WHERE tenant_id = ?");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        if (status != null && !status.isBlank()) {
            sql.append(" AND status = ?");
            args.add(status);
        }
        if (family != null && !family.isBlank()) {
            sql.append(" AND family = ?");
            args.add(family);
        }
        sql.append(" ORDER BY updated_at DESC, rule_id ASC LIMIT ? OFFSET ?");
        args.add(limit);
        args.add(offset);
        return jdbc.query(sql.toString(), RULE_MAPPER, args.toArray());
    }

    public List<RuleVersionRow> versions(String tenantId, String ruleId) {
        return jdbc.query("""
                        SELECT version, status, created_by, created_at, payload
                        FROM rule_version
                        WHERE tenant_id = ? AND rule_id = ?
                        ORDER BY version DESC
                        """,
                VERSION_MAPPER, tenantId, ruleId);
    }

    /** Payloads of all active rules for a tenant, ordered by rule_id (stable canonical order). */
    public List<String> activePayloads(String tenantId) {
        return jdbc.queryForList("""
                        SELECT rv.payload
                        FROM rule r
                        JOIN rule_version rv
                          ON rv.tenant_id = r.tenant_id
                         AND rv.rule_id = r.rule_id
                         AND rv.version = r.active_version
                        WHERE r.tenant_id = ? AND r.status = 'active'
                        ORDER BY r.rule_id
                        """,
                String.class, tenantId);
    }

    private static Integer nullableInt(Object value) {
        return value == null ? null : ((Number) value).intValue();
    }

    private static LocalDateTime toLocalDateTime(Timestamp ts) {
        return ts == null ? null : ts.toLocalDateTime();
    }
}
