package com.adguard.controlplane.domain;

import java.time.LocalDateTime;

/** List-view of a rule (latest state only). */
public record RuleSummary(
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
        String clauseId,
        LocalDateTime updatedAt) {
}
