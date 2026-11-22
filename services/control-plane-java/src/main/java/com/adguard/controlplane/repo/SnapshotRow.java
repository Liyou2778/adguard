package com.adguard.controlplane.repo;

import java.time.LocalDateTime;

/** Database row projection for the {@code rule_snapshot} table. */
public record SnapshotRow(
        String id,
        String tenantId,
        int seq,
        String payloadJson,
        String checksum,
        int ruleCount,
        LocalDateTime createdAt) {
}
