package com.adguard.controlplane.validation;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.RuleDefinition;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/**
 * Rule payload validation.
 *
 * <p>Every rule is data; if bad data gets in, the whole judging pipeline silently
 * degrades. So validation is strict and returns *all* problems at once (better UX
 * for the rule author than failing one field at a time).
 */
@Component
public class RuleValidator {

    private static final Pattern RULE_ID = Pattern.compile("^[A-Z0-9][A-Z0-9._-]{2,63}$");
    private static final Pattern FAMILY = Pattern.compile("^[a-z][a-z0-9_]{2,63}$");
    private static final List<String> SEVERITIES = List.of("high", "mid", "low");

    public void validate(RuleDefinition def) {
        if (def == null) {
            throw ApiException.badRequest("INVALID_RULE", "request body is required");
        }
        List<String> errors = new ArrayList<>();

        require(errors, def.ruleId(), "rule_id");
        if (def.ruleId() != null && def.ruleId().length() > 64) {
            errors.add("rule_id must be <= 64 chars");
        }
        if (def.ruleId() != null && !RULE_ID.matcher(def.ruleId()).matches()) {
            errors.add("rule_id must match ^[A-Z0-9][A-Z0-9._-]{2,63}$ (e.g. ADV-LAW-ART9-3)");
        }
        require(errors, def.law(), "law");
        maxLength(errors, def.law(), 128, "law");
        require(errors, def.article(), "article");
        maxLength(errors, def.article(), 32, "article");
        maxLength(errors, def.item(), 16, "item");
        require(errors, def.family(), "family");
        if (def.family() != null && !FAMILY.matcher(def.family()).matches()) {
            errors.add("family must match ^[a-z][a-z0-9_]{2,63}$ (e.g. absolute_terms)");
        }
        require(errors, def.severity(), "severity");
        if (def.severity() != null && !SEVERITIES.contains(def.severity())) {
            errors.add("severity must be one of " + SEVERITIES);
        }
        require(errors, def.text(), "text");
        maxLength(errors, def.text(), 2000, "text");

        if (def.canonicalTerms() == null || def.canonicalTerms().isEmpty()) {
            errors.add("canonical_terms must contain at least one term");
        } else {
            if (def.canonicalTerms().size() > 200) {
                errors.add("canonical_terms must contain <= 200 terms");
            }
            for (String term : def.canonicalTerms()) {
                if (term == null || term.isBlank()) {
                    errors.add("canonical_terms must not contain blank terms");
                    break;
                }
                if (term.length() > 64) {
                    errors.add("canonical_terms entries must be <= 64 chars");
                    break;
                }
            }
        }

        if (def.detection() == null) {
            errors.add("detection is required");
        } else {
            if (def.detection().lexical() != null && def.detection().lexical().size() > 50) {
                errors.add("detection.lexical must contain <= 50 patterns");
            }
            if (def.detection().contextExemptions() != null
                    && def.detection().contextExemptions().size() > 50) {
                errors.add("detection.context_exemptions must contain <= 50 entries");
            }
        }

        if (def.penalty() != null) {
            Integer min = def.penalty().min();
            Integer max = def.penalty().max();
            if (min != null && min < 0) {
                errors.add("penalty.min must be >= 0");
            }
            if (max != null && max < 0) {
                errors.add("penalty.max must be >= 0");
            }
            if (min != null && max != null && min > max) {
                errors.add("penalty.min must be <= penalty.max");
            }
            maxLength(errors, def.penalty().lawRef(), 32, "penalty.law_ref");
        }

        if (def.effectiveFrom() != null && def.effectiveTo() != null
                && def.effectiveFrom().isAfter(def.effectiveTo())) {
            errors.add("effective_from must be <= effective_to");
        }
        maxLength(errors, def.sourceUrl(), 512, "source_url");

        if (!errors.isEmpty()) {
            throw ApiException.badRequest("INVALID_RULE", String.join("; ", errors));
        }
    }

    private static void require(List<String> errors, String value, String field) {
        if (value == null || value.isBlank()) {
            errors.add(field + " is required");
        }
    }

    private static void maxLength(List<String> errors, String value, int max, String field) {
        if (value != null && value.length() > max) {
            errors.add(field + " must be <= " + max + " chars");
        }
    }
}
