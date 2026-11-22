package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.RegulationClauseRow;
import com.adguard.controlplane.domain.RegulationImportRequest;
import com.adguard.controlplane.domain.RegulationRow;
import com.adguard.controlplane.repo.RegulationRepository;
import com.adguard.controlplane.service.RegulationImportService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/** Regulation import + clause inspection (M2). Import is idempotent. */
@RestController
@RequestMapping("/admin/regulations")
public class AdminRegulationController {

    private static final String TENANT_HEADER = "X-Tenant-Id";

    private final RegulationImportService importService;
    private final RegulationRepository regulationRepository;

    public AdminRegulationController(RegulationImportService importService,
                                     RegulationRepository regulationRepository) {
        this.importService = importService;
        this.regulationRepository = regulationRepository;
    }

    @PostMapping("/import")
    @ResponseStatus(HttpStatus.CREATED)
    public RegulationImportService.ImportResult importRegulations(
            @RequestHeader(TENANT_HEADER) String tenantId,
            @RequestBody RegulationImportRequest request) {
        return importService.importRegulations(Tenants.require(tenantId), request);
    }

    @GetMapping
    public List<RegulationRow> list() {
        return regulationRepository.list();
    }

    @GetMapping("/{regulationId}/clauses")
    public List<RegulationClauseRow> clauses(@PathVariable String regulationId) {
        if (regulationRepository.find(regulationId).isEmpty()) {
            throw ApiException.notFound("REGULATION_NOT_FOUND", "regulation not found: " + regulationId);
        }
        return regulationRepository.clauses(regulationId);
    }
}
