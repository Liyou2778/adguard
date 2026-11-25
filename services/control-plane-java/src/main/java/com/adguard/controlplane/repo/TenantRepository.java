package com.adguard.controlplane.repo;

import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** tenant persistence: status + daily quota (M9 / FR-13). */
@Repository
public class TenantRepository {

    public record TenantRow(String id, String name, String status, int dailyQuota, Instant createdAt) {
        public boolean active() {
            return "active".equals(status);
        }
    }

    private static final RowMapper<TenantRow> MAPPER = (rs, rowNum) -> new TenantRow(
            rs.getString("id"),
            rs.getString("name"),
            rs.getString("status"),
            rs.getInt("daily_quota"),
            rs.getTimestamp("created_at") == null ? null : rs.getTimestamp("created_at").toInstant());

    private final JdbcTemplate jdbc;

    public TenantRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<TenantRow> findById(String id) {
        try {
            return Optional.ofNullable(jdbc.queryForObject("SELECT * FROM tenant WHERE id = ?", MAPPER, id));
        } catch (EmptyResultDataAccessException e) {
            return Optional.empty();
        }
    }

    public boolean exists(String id) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM tenant WHERE id = ?", Integer.class, id);
        return count != null && count > 0;
    }

    public void create(String id, String name, String status, int dailyQuota) {
        jdbc.update("INSERT INTO tenant (id, name, status, daily_quota) VALUES (?, ?, ?, ?)",
                id, name, status, dailyQuota);
    }

    public List<TenantRow> listAll(int limit) {
        return jdbc.query("SELECT * FROM tenant ORDER BY created_at DESC LIMIT ?", MAPPER, limit);
    }

    public void update(String id, String name, Integer dailyQuota, String status) {
        jdbc.update("UPDATE tenant SET name = COALESCE(?, name), daily_quota = COALESCE(?, daily_quota), "
                        + "status = COALESCE(?, status) WHERE id = ?",
                blankToNull(name), dailyQuota, blankToNull(status), id);
    }

    public long count() {
        Long count = jdbc.queryForObject("SELECT COUNT(*) FROM tenant", Long.class);
        return count == null ? 0 : count;
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    @SuppressWarnings("unused")
    private static Instant unused(Timestamp ts) {
        return ts == null ? null : ts.toInstant();
    }
}
