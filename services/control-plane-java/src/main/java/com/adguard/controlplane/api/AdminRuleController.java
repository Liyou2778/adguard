package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.RuleDefinition;
import com.adguard.controlplane.domain.RuleDetail;
import com.adguard.controlplane.domain.RuleSummary;
import com.adguard.controlplane.domain.SnapshotView;
import com.adguard.controlplane.service.RuleService;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/** Rule library admin API (Java control plane). */
@RestController
@RequestMapping("/admin/rules")
public class AdminRuleController {

    private static final String TENANT_HEADER = "X-Tenant-Id";

    private final RuleService ruleService;

    public AdminRuleController(RuleService ruleService) {
        this.ruleService = ruleService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public RuleDetail create(@RequestHeader(TENANT_HEADER) String tenantId,
                             @RequestBody RuleDefinition definition) {
        return ruleService.create(Tenants.require(tenantId), definition);
    }

    @PutMapping("/{ruleId}")
    public RuleDetail update(@RequestHeader(TENANT_HEADER) String tenantId,
                             @PathVariable String ruleId,
                             @RequestBody RuleDefinition definition) {
        return ruleService.update(Tenants.require(tenantId), ruleId, definition);
    }

    @GetMapping
    public List<RuleSummary> list(@RequestHeader(TENANT_HEADER) String tenantId,
                                  @RequestParam(required = false) String status,
                                  @RequestParam(required = false) String family,
                                  @RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return ruleService.list(Tenants.require(tenantId), status, family, limit);
    }

    @GetMapping("/{ruleId}")
    public RuleDetail get(@RequestHeader(TENANT_HEADER) String tenantId,
                          @PathVariable String ruleId) {
        return ruleService.get(Tenants.require(tenantId), ruleId);
    }

    /** Activates the current draft version and returns the freshly materialised snapshot. */
    @PostMapping("/{ruleId}/activate")
    public SnapshotView activate(@RequestHeader(TENANT_HEADER) String tenantId,
                                 @PathVariable String ruleId) {
        return ruleService.activate(Tenants.require(tenantId), ruleId);
    }
}
