package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.SnapshotView;
import com.adguard.controlplane.service.SnapshotService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Internal snapshot API consumed by the Go gateway / judging pipeline.
 *
 * <p>M1 hardening TODO (M6): restrict to the internal network and require a service
 * token / mTLS. Until then it is only exposed on the private compose network in
 * production topology.
 */
@RestController
@RequestMapping("/internal/rules")
public class InternalSnapshotController {

    private final SnapshotService snapshotService;

    public InternalSnapshotController(SnapshotService snapshotService) {
        this.snapshotService = snapshotService;
    }

    @GetMapping("/snapshot")
    public SnapshotView snapshot(@RequestParam("tenant_id") String tenantId,
                                 @RequestParam(value = "snapshot_id", required = false) String snapshotId) {
        String tenant = Tenants.require(tenantId);
        if (snapshotId == null || snapshotId.isBlank()) {
            return snapshotService.latest(tenant);
        }
        return snapshotService.byId(tenant, snapshotId);
    }
}
