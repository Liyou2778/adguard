package com.adguard.controlplane.repo;

import java.time.LocalDateTime;

/** Database row projection for the {@code rule_version} table. */
public record RuleVersionRow(
        int version,
        String status,
        String createdBy,
        LocalDateTime createdAt,
        String payloadJson) {
}
