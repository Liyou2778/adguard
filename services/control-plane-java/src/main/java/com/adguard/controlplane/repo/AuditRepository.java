package com.adguard.controlplane.repo;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.UUID;

/** Append-only audit trail for identity, appeal and billing actions (M8). */
@Repository
public class AuditRepository {

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public AuditRepository(JdbcTemplate jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    public void record(String tenantId, String actorUserId, String actorUsername, String action,
                       String targetType, String targetId, Object detail) {
        String detailJson = null;
        if (detail != null) {
            try {
                detailJson = objectMapper.writeValueAsString(detail);
            } catch (JsonProcessingException e) {
                detailJson = null;
            }
        }
        jdbc.update("INSERT INTO audit_log (id, tenant_id, actor_user_id, actor_username, action, "
                        + "target_type, target_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID().toString(), tenantId, actorUserId, actorUsername, action,
                targetType, targetId, detailJson);
    }
}
