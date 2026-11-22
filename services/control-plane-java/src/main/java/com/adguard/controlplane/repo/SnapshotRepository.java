package com.adguard.controlplane.repo;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.util.List;
import java.util.Optional;

/** Persistence for immutable {@code rule_snapshot} rows. */
@Repository
public class SnapshotRepository {

    private static final RowMapper<SnapshotRow> MAPPER = (rs, i) -> new SnapshotRow(
            rs.getString("id"),
            rs.getString("tenant_id"),
            rs.getInt("seq"),
            rs.getString("payload"),
            rs.getString("checksum"),
            rs.getInt("rule_count"),
            rs.getTimestamp("created_at") == null ? null
                    : rs.getTimestamp("created_at").toLocalDateTime());

    private final JdbcTemplate jdbc;

    public SnapshotRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<SnapshotRow> latest(String tenantId) {
        List<SnapshotRow> rows = jdbc.query(
                "SELECT * FROM rule_snapshot WHERE tenant_id = ? ORDER BY seq DESC LIMIT 1",
                MAPPER, tenantId);
        return rows.stream().findFirst();
    }

    public Optional<SnapshotRow> findById(String snapshotId) {
        List<SnapshotRow> rows = jdbc.query(
                "SELECT * FROM rule_snapshot WHERE id = ?",
                MAPPER, snapshotId);
        return rows.stream().findFirst();
    }

    /** Must be called while holding the per-tenant row lock. */
    public int nextSeq(String tenantId) {
        Integer max = jdbc.queryForObject(
                "SELECT COALESCE(MAX(seq), 0) FROM rule_snapshot WHERE tenant_id = ?",
                Integer.class, tenantId);
        return (max == null ? 0 : max) + 1;
    }

    public void insert(String id, String tenantId, int seq, String payloadJson, String checksum,
                       int ruleCount, String createdBy) {
        jdbc.update("""
                        INSERT INTO rule_snapshot (id, tenant_id, seq, payload, checksum, rule_count, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                id, tenantId, seq, payloadJson, checksum, ruleCount, createdBy);
    }

    public long count(String tenantId) {
        Long n = jdbc.queryForObject(
                "SELECT COUNT(*) FROM rule_snapshot WHERE tenant_id = ?", Long.class, tenantId);
        return n == null ? 0 : n;
    }

    static Timestamp now() {
        return new Timestamp(System.currentTimeMillis());
    }
}
