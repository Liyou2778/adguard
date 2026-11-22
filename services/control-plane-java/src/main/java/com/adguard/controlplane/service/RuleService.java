package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.RuleDefinition;
import com.adguard.controlplane.domain.RuleDetail;
import com.adguard.controlplane.domain.RuleSummary;
import com.adguard.controlplane.domain.RuleVersionView;
import com.adguard.controlplane.domain.SnapshotView;
import com.adguard.controlplane.repo.RuleRepository;
import com.adguard.controlplane.repo.RuleRow;
import com.adguard.controlplane.repo.RuleVersionRow;
import com.adguard.controlplane.validation.RuleValidator;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Rule lifecycle: create draft -> revise draft -> activate (which materialises a snapshot).
 *
 * <p>All mutations for a tenant take the tenant row lock. That keeps version numbers
 * and snapshot sequence numbers monotonic under concurrent admin requests without
 * relying on optimistic concurrency (M1 trade-off; revisit if rule editing becomes hot).
 */
@Service
public class RuleService {

    private static final Logger log = LoggerFactory.getLogger(RuleService.class);
    private static final int MAX_PAGE_SIZE = 200;

    private final RuleRepository ruleRepository;
    private final SnapshotService snapshotService;
    private final RuleValidator ruleValidator;
    private final ObjectMapper objectMapper;

    public RuleService(RuleRepository ruleRepository,
                       SnapshotService snapshotService,
                       RuleValidator ruleValidator,
                       ObjectMapper objectMapper) {
        this.ruleRepository = ruleRepository;
        this.snapshotService = snapshotService;
        this.ruleValidator = ruleValidator;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public RuleDetail create(String tenantId, RuleDefinition definition) {
        ruleValidator.validate(definition);
        ruleRepository.upsertTenant(tenantId);
        if (!ruleRepository.lockTenant(tenantId)) {
            throw new IllegalStateException("tenant disappeared during rule creation: " + tenantId);
        }
        if (ruleRepository.exists(tenantId, definition.ruleId())) {
            throw ApiException.conflict("RULE_EXISTS",
                    "rule " + definition.ruleId() + " already exists; use PUT to create a new draft version");
        }
        ruleRepository.insertRule(tenantId, definition);
        ruleRepository.insertVersion(tenantId, definition.ruleId(), 1, toJson(definition), "draft", "api");
        log.info("rule created tenant={} rule_id={} version=1", tenantId, definition.ruleId());
        return get(tenantId, definition.ruleId());
    }

    @Transactional
    public RuleDetail update(String tenantId, String ruleId, RuleDefinition definition) {
        ruleValidator.validate(definition);
        if (!ruleId.equals(definition.ruleId())) {
            throw ApiException.badRequest("RULE_ID_MISMATCH",
                    "path rule_id " + ruleId + " does not match body rule_id " + definition.ruleId());
        }
        if (!ruleRepository.lockTenant(tenantId)) {
            throw ApiException.notFound("TENANT_NOT_FOUND", "tenant not found: " + tenantId);
        }
        RuleRow row = ruleRepository.find(tenantId, ruleId)
                .orElseThrow(() -> ApiException.notFound("RULE_NOT_FOUND", "rule not found: " + ruleId));
        int nextVersion = row.currentVersion() + 1;
        ruleRepository.insertVersion(tenantId, ruleId, nextVersion, toJson(definition), "draft", "api");
        ruleRepository.bumpCurrentVersion(tenantId, ruleId, nextVersion);
        log.info("rule draft version created tenant={} rule_id={} version={}", tenantId, ruleId, nextVersion);
        return get(tenantId, ruleId);
    }

    @Transactional
    public SnapshotView activate(String tenantId, String ruleId) {
        if (!ruleRepository.lockTenant(tenantId)) {
            throw ApiException.notFound("TENANT_NOT_FOUND", "tenant not found: " + tenantId);
        }
        RuleRow row = ruleRepository.find(tenantId, ruleId)
                .orElseThrow(() -> ApiException.notFound("RULE_NOT_FOUND", "rule not found: " + ruleId));
        if (row.activeVersion() != null && row.activeVersion() == row.currentVersion()) {
            throw ApiException.conflict("RULE_ALREADY_ACTIVE",
                    "rule " + ruleId + " version " + row.currentVersion() + " is already active");
        }
        ruleRepository.activate(tenantId, ruleId, row.currentVersion());
        log.info("rule activated tenant={} rule_id={} version={}", tenantId, ruleId, row.currentVersion());
        return snapshotService.create(tenantId, "api");
    }

    @Transactional(readOnly = true)
    public RuleDetail get(String tenantId, String ruleId) {
        RuleRow row = ruleRepository.find(tenantId, ruleId)
                .orElseThrow(() -> ApiException.notFound("RULE_NOT_FOUND", "rule not found: " + ruleId));
        List<RuleVersionView> versions = ruleRepository.versions(tenantId, ruleId).stream()
                .map(this::toVersionView)
                .toList();
        return new RuleDetail(toSummary(row), versions);
    }

    @Transactional(readOnly = true)
    public List<RuleSummary> list(String tenantId, String status, String family, int limit) {
        int pageSize = Math.max(1, Math.min(limit, MAX_PAGE_SIZE));
        return ruleRepository.list(tenantId, status, family, pageSize, 0).stream()
                .map(this::toSummary)
                .toList();
    }

    private RuleSummary toSummary(RuleRow row) {
        return new RuleSummary(row.ruleId(), row.tenantId(), row.law(), row.article(), row.item(),
                row.family(), row.severity(), row.status(), row.currentVersion(),
                row.activeVersion(), row.clauseId(), row.updatedAt());
    }

    private RuleVersionView toVersionView(RuleVersionRow row) {
        return new RuleVersionView(row.version(), row.status(), row.createdBy(), row.createdAt(),
                fromJson(row.payloadJson()));
    }

    private String toJson(RuleDefinition definition) {
        try {
            return objectMapper.writeValueAsString(definition);
        } catch (JsonProcessingException e) {
            throw ApiException.badRequest("INVALID_RULE", "rule could not be serialised: " + e.getMessage());
        }
    }

    private RuleDefinition fromJson(String json) {
        try {
            return objectMapper.readValue(json, RuleDefinition.class);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("corrupted rule payload in database", e);
        }
    }
}
