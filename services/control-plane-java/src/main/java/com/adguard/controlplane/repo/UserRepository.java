package com.adguard.controlplane.repo;

import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/** app_user persistence (M8 / FR-10 identity). */
@Repository
public class UserRepository {

    public record UserRow(String id, String tenantId, String username, String email, String passwordHash,
                          String role, String status, Instant lastLoginAt, Instant createdAt) {
        public boolean active() {
            return "active".equals(status);
        }
    }

    private static final RowMapper<UserRow> MAPPER = (rs, rowNum) -> new UserRow(
            rs.getString("id"),
            rs.getString("tenant_id"),
            rs.getString("username"),
            rs.getString("email"),
            rs.getString("password_hash"),
            rs.getString("role"),
            rs.getString("status"),
            toInstant(rs.getTimestamp("last_login_at")),
            toInstant(rs.getTimestamp("created_at")));

    private final JdbcTemplate jdbc;

    public UserRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<UserRow> findByUsername(String username) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(
                    "SELECT * FROM app_user WHERE username = ?", MAPPER, username));
        } catch (EmptyResultDataAccessException e) {
            return Optional.empty();
        }
    }

    public Optional<UserRow> findById(String id) {
        try {
            return Optional.ofNullable(jdbc.queryForObject(
                    "SELECT * FROM app_user WHERE id = ?", MAPPER, id));
        } catch (EmptyResultDataAccessException e) {
            return Optional.empty();
        }
    }

    public boolean existsByUsername(String username) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM app_user WHERE username = ?",
                Integer.class, username);
        return count != null && count > 0;
    }

    public boolean existsAdmin() {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM app_user WHERE role = 'admin' AND status = 'active'", Integer.class);
        return count != null && count > 0;
    }

    public long countActiveAdmins() {
        Long count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM app_user WHERE role = 'admin' AND status = 'active'", Long.class);
        return count == null ? 0 : count;
    }

    public void createAdmin(String username, String passwordHash, String email) {
        jdbc.update("INSERT INTO app_user (id, tenant_id, username, email, password_hash, role, status) "
                        + "VALUES (?, NULL, ?, NULLIF(?, ''), ?, 'admin', 'active')",
                UUID.randomUUID().toString(), username, email, passwordHash);
    }

    public UserRow create(String tenantId, String username, String passwordHash, String role, String email) {
        String id = UUID.randomUUID().toString();
        jdbc.update("INSERT INTO app_user (id, tenant_id, username, email, password_hash, role, status) "
                        + "VALUES (?, ?, ?, NULLIF(?, ''), ?, ?, 'active')",
                id, tenantId, username, email, passwordHash, role);
        return findById(id).orElseThrow();
    }

    public void touchLastLogin(String id) {
        jdbc.update("UPDATE app_user SET last_login_at = CURRENT_TIMESTAMP(3) WHERE id = ?", id);
    }

    public void updatePassword(String id, String passwordHash) {
        jdbc.update("UPDATE app_user SET password_hash = ? WHERE id = ?", passwordHash, id);
    }

    public void updateRoleStatus(String id, String role, String status) {
        jdbc.update("UPDATE app_user SET role = COALESCE(?, role), status = COALESCE(?, status) WHERE id = ?",
                blankToNull(role), blankToNull(status), id);
    }

    public List<UserRow> list(String tenantId, int limit) {
        if (tenantId == null || tenantId.isBlank()) {
            return jdbc.query("SELECT * FROM app_user ORDER BY created_at DESC LIMIT ?", MAPPER, limit);
        }
        return jdbc.query("SELECT * FROM app_user WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                MAPPER, tenantId, limit);
    }

    public long countByTenant(String tenantId) {
        Long count = jdbc.queryForObject("SELECT COUNT(*) FROM app_user WHERE tenant_id = ?", Long.class, tenantId);
        return count == null ? 0 : count;
    }

    private static Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
