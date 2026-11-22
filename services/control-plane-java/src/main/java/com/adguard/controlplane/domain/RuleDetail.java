package com.adguard.controlplane.domain;

import java.util.List;

/** Full detail of a rule: summary + all versions (newest first). */
public record RuleDetail(
        RuleSummary summary,
        List<RuleVersionView> versions) {
}
