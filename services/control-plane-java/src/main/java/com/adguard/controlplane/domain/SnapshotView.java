package com.adguard.controlplane.domain;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.LocalDateTime;

/**
 * Immutable rule snapshot (ADR-3).
 *
 * <p>A judgment is always bound to a snapshot id, so "why was this blocked?" can
 * be replayed months later even after rules changed.
 */
public record SnapshotView(
        String id,
        String tenantId,
        int seq,
        int ruleCount,
        String checksum,
        LocalDateTime createdAt,
        JsonNode payload) {
}
