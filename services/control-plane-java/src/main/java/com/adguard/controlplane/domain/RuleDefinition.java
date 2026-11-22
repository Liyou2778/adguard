package com.adguard.controlplane.domain;

import java.time.LocalDate;
import java.util.List;

/**
 * A complete rule definition (ADR-1: a rule is data, not code).
 *
 * <p>The record is the single source of truth for the rule payload stored in
 * {@code rule_version.payload} and emitted inside {@code rule_snapshot.payload}.
 * It deliberately carries the detection configuration and penalty range so that
 * downstream services (retrieval, judging) never need to parse legal text again.
 */
public record RuleDefinition(
        String ruleId,
        String law,
        String article,
        String item,
        String family,
        String severity,
        String text,
        List<String> canonicalTerms,
        Detection detection,
        Penalty penalty,
        LocalDate effectiveFrom,
        LocalDate effectiveTo,
        String sourceUrl) {

    /** Lexical / semantic detection configuration. */
    public record Detection(
            List<String> lexical,
            String semantic,
            List<String> contextExemptions) {
    }

    /** Penalty range bound to the law article (e.g. 200000 - 1000000 CNY). */
    public record Penalty(
            String lawRef,
            Integer min,
            Integer max) {
    }
}
