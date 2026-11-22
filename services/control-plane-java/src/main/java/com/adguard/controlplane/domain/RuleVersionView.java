package com.adguard.controlplane.domain;

import java.time.LocalDateTime;

/** One immutable version of a rule. */
public record RuleVersionView(
        int version,
        String status,
        String createdBy,
        LocalDateTime createdAt,
        RuleDefinition payload) {
}
