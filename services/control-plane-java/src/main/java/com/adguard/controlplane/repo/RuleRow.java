package com.adguard.controlplane.repo;

import java.time.LocalDateTime;

/** Database row projection for the {@code rule} table. */
public record RuleRow(
        String ruleId,
        String tenantId,
        String law,
        String article,
        String item,
        String family,
        String severity,
        String status,
        int currentVersion,
        Integer activeVersion,
        LocalDateTime updatedAt) {
}
