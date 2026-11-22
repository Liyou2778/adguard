package com.adguard.controlplane.domain;

import java.time.LocalDate;
import java.util.List;

/**
 * Payload of the M2 regulation importer, produced by the Python clause parser
 * (`adguard_pipeline.clause_parser`) and validated here before it reaches the DB.
 */
public record RegulationImportRequest(
        List<RegulationDoc> regulations,
        List<ParsedRule> rules) {

    public record RegulationDoc(
            String id,
            String title,
            String sourceUrl,
            LocalDate effectiveFrom,
            Integer charCount,
            List<ClauseDoc> clauses) {
    }

    public record ClauseDoc(
            String clauseId,
            String article,
            Integer articleNo,
            String chapter,
            String text,
            Integer charStart,
            Integer charEnd,
            List<ItemDoc> items) {
    }

    public record ItemDoc(
            String item,
            Integer index,
            String text,
            Integer charStart,
            Integer charEnd) {
    }

    public record ParsedRule(
            String ruleId,
            String family,
            String severity,
            String regulationId,
            String article,
            String item,
            String clauseId,
            String text,
            List<String> canonicalTerms,
            String penaltyLawRef,
            Integer penaltyMin,
            Integer penaltyMax,
            LocalDate effectiveFrom,
            List<String> contextExemptions,
            String sourceUrl,
            String law) {
    }
}
