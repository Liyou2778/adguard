package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.RegulationClauseRow;
import com.adguard.controlplane.domain.RegulationImportRequest;
import com.adguard.controlplane.domain.RegulationRow;
import com.adguard.controlplane.domain.RuleDefinition;
import com.adguard.controlplane.repo.RegulationRepository;
import com.adguard.controlplane.repo.RuleRepository;
import com.adguard.controlplane.validation.RuleValidator;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Imports the Python clause parser output into the rule library.
 *
 * <p>Idempotency contract: importing the same payload twice must not duplicate
 * regulations, clauses or rules. Regulation content is compared by checksum, rules
 * by (tenant, rule_id). That lets the M2 pipeline be re-run safely.
 */
@Service
public class RegulationImportService {

    private static final Logger log = LoggerFactory.getLogger(RegulationImportService.class);
    private static final Pattern REGULATION_ID = Pattern.compile("^[A-Z][A-Z0-9-]{2,63}$");

    private final RegulationRepository regulationRepository;
    private final RuleRepository ruleRepository;
    private final RuleValidator ruleValidator;
    private final ObjectMapper objectMapper;

    public RegulationImportService(RegulationRepository regulationRepository,
                                   RuleRepository ruleRepository,
                                   RuleValidator ruleValidator,
                                   ObjectMapper objectMapper) {
        this.regulationRepository = regulationRepository;
        this.ruleRepository = ruleRepository;
        this.ruleValidator = ruleValidator;
        this.objectMapper = objectMapper;
    }

    public record ImportResult(
            int regulationsImported,
            int regulationsUpdated,
            int regulationsUnchanged,
            int clausesWritten,
            int rulesCreated,
            int rulesSkipped,
            List<String> ruleIds) {
    }

    @Transactional
    public ImportResult importRegulations(String tenantId, RegulationImportRequest request) {
        validate(request);
        ruleRepository.upsertTenant(tenantId);

        int imported = 0;
        int updated = 0;
        int unchanged = 0;
        int clausesWritten = 0;
        Set<String> knownClauses = new HashSet<>();

        for (RegulationImportRequest.RegulationDoc doc : request.regulations()) {
            String checksum = checksum(doc);
            var existing = regulationRepository.find(doc.id());
            if (existing.isPresent() && existing.get().checksum().equals(checksum)) {
                unchanged++;
                clauseIds(doc).forEach(knownClauses::add);
                continue;
            }
            regulationRepository.upsert(new RegulationRow(doc.id(), doc.title(), doc.sourceUrl(),
                    doc.effectiveFrom(), checksum, doc.charCount(), null));
            if (existing.isPresent()) {
                regulationRepository.deleteClauses(doc.id());
            }
            for (RegulationImportRequest.ClauseDoc clause : doc.clauses()) {
                String articleChecksum = sha256(clause.text());
                regulationRepository.insertClause(new RegulationClauseRow(
                        clause.clauseId(), doc.id(), clause.article(), clause.articleNo(),
                        clause.chapter(), "article", null, null, clause.text(),
                        clause.charStart(), clause.charEnd(), articleChecksum, null));
                clausesWritten++;
                knownClauses.add(clause.clauseId());
                for (RegulationImportRequest.ItemDoc item : clause.items()) {
                    String itemClauseId = clause.clauseId() + "-ITEM" + item.index();
                    regulationRepository.insertClause(new RegulationClauseRow(
                            itemClauseId, doc.id(), clause.article(), clause.articleNo(),
                            clause.chapter(), "item", clause.clauseId(), item.item(), item.text(),
                            item.charStart(), item.charEnd(), sha256(item.text()), null));
                    clausesWritten++;
                    knownClauses.add(itemClauseId);
                }
            }
            if (existing.isPresent()) {
                updated++;
            } else {
                imported++;
            }
        }

        int rulesCreated = 0;
        int rulesSkipped = 0;
        List<String> createdRuleIds = new ArrayList<>();
        for (RegulationImportRequest.ParsedRule parsed : request.rules()) {
            String clauseId = parsed.clauseId();
            if (!knownClauses.contains(clauseId) && regulationRepository.findClause(clauseId).isEmpty()) {
                throw ApiException.badRequest("UNKNOWN_CLAUSE",
                        "rule " + parsed.ruleId() + " references unknown clause " + clauseId);
            }
            if (ruleRepository.exists(tenantId, parsed.ruleId())) {
                rulesSkipped++;
                continue;
            }
            RuleDefinition definition = toDefinition(parsed);
            ruleValidator.validate(definition);
            ruleRepository.insertRule(tenantId, definition, clauseId, parsed.regulationId());
            ruleRepository.insertVersion(tenantId, parsed.ruleId(), 1, toJson(definition), "draft", "clause-parser");
            rulesCreated++;
            createdRuleIds.add(parsed.ruleId());
        }

        ImportResult result = new ImportResult(imported, updated, unchanged, clausesWritten,
                rulesCreated, rulesSkipped, createdRuleIds);
        log.info("regulations imported tenant={} imported={} updated={} unchanged={} clauses={} rules_created={} rules_skipped={}",
                tenantId, imported, updated, unchanged, clausesWritten, rulesCreated, rulesSkipped);
        return result;
    }

    // ------------------------------------------------------------------ validation
    private void validate(RegulationImportRequest request) {
        if (request == null || request.regulations() == null || request.regulations().isEmpty()) {
            throw ApiException.badRequest("INVALID_IMPORT", "at least one regulation is required");
        }
        if (request.rules() == null) {
            throw ApiException.badRequest("INVALID_IMPORT", "rules must be an array (may be empty)");
        }
        Set<String> clauseIds = new HashSet<>();
        for (RegulationImportRequest.RegulationDoc doc : request.regulations()) {
            if (doc.id() == null || !REGULATION_ID.matcher(doc.id()).matches()) {
                throw ApiException.badRequest("INVALID_REGULATION_ID",
                        "regulation id must match ^[A-Z][A-Z0-9-]{2,63}$: " + doc.id());
            }
            if (doc.title() == null || doc.title().isBlank()) {
                throw ApiException.badRequest("INVALID_REGULATION", "regulation " + doc.id() + " has no title");
            }
            // Guidelines / notices may be metadata-only: they carry no 第X条 clauses but
            // still provide exemptions referenced by rules from other documents.
            if (doc.clauses() == null) {
                throw ApiException.badRequest("INVALID_REGULATION",
                        "regulation " + doc.id() + " clauses must be an array (may be empty)");
            }
            for (RegulationImportRequest.ClauseDoc clause : doc.clauses()) {
                if (clause.clauseId() == null || clause.clauseId().isBlank()) {
                    throw ApiException.badRequest("INVALID_CLAUSE", "clause_id is required");
                }
                if (!clauseIds.add(clause.clauseId())) {
                    throw ApiException.badRequest("DUPLICATE_CLAUSE", "duplicate clause id: " + clause.clauseId());
                }
                if (clause.articleNo() == null || clause.articleNo() <= 0) {
                    throw ApiException.badRequest("INVALID_CLAUSE",
                            "clause " + clause.clauseId() + " has invalid article_no");
                }
                if (clause.items() != null) {
                    for (RegulationImportRequest.ItemDoc item : clause.items()) {
                        String itemClauseId = clause.clauseId() + "-ITEM" + item.index();
                        if (!clauseIds.add(itemClauseId)) {
                            throw ApiException.badRequest("DUPLICATE_CLAUSE", "duplicate clause id: " + itemClauseId);
                        }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ mapping
    private RuleDefinition toDefinition(RegulationImportRequest.ParsedRule parsed) {
        RuleDefinition.Penalty penalty = null;
        if (parsed.penaltyMin() != null || parsed.penaltyMax() != null) {
            penalty = new RuleDefinition.Penalty(parsed.penaltyLawRef(), parsed.penaltyMin(), parsed.penaltyMax());
        }
        RuleDefinition.Detection detection = new RuleDefinition.Detection(
                List.of(),
                "context-aware judging is handled by the M4 model; this rule provides the clause anchor",
                parsed.contextExemptions() == null ? List.of() : parsed.contextExemptions());
        return new RuleDefinition(
                parsed.ruleId(),
                parsed.law(),
                parsed.article(),
                parsed.item(),
                parsed.family(),
                parsed.severity(),
                parsed.text(),
                parsed.canonicalTerms(),
                detection,
                penalty,
                parsed.effectiveFrom(),
                null,
                parsed.sourceUrl());
    }

    private List<String> clauseIds(RegulationImportRequest.RegulationDoc doc) {
        List<String> ids = new ArrayList<>();
        doc.clauses().forEach(clause -> {
            ids.add(clause.clauseId());
            if (clause.items() != null) {
                clause.items().forEach(item -> ids.add(clause.clauseId() + "-ITEM" + item.index()));
            }
        });
        return ids;
    }

    private String checksum(RegulationImportRequest.RegulationDoc doc) {
        List<RegulationImportRequest.ClauseDoc> clauses = new ArrayList<>(doc.clauses());
        clauses.sort(Comparator.comparing(RegulationImportRequest.ClauseDoc::clauseId));
        try {
            return sha256(objectMapper.writeValueAsString(clauses));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("cannot canonicalise regulation clauses", e);
        }
    }

    private String toJson(RuleDefinition definition) {
        try {
            return objectMapper.writeValueAsString(definition);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("cannot serialise rule definition", e);
        }
    }

    static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
