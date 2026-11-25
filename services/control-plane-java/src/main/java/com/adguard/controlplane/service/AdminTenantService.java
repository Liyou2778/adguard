package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.api.Tenants;
import com.adguard.controlplane.domain.AuthDtos;
import com.adguard.controlplane.repo.AuditRepository;
import com.adguard.controlplane.repo.TenantRepository;
import com.adguard.controlplane.security.AuthPrincipal;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/** Platform administrator tenant / quota management (M8 + M9). */
@Service
public class AdminTenantService {

    private static final int DEFAULT_DAILY_QUOTA = 100_000;

    private final TenantRepository tenants;
    private final AuditRepository audit;

    public AdminTenantService(TenantRepository tenants, AuditRepository audit) {
        this.tenants = tenants;
        this.audit = audit;
    }

    public List<AuthDtos.TenantView> list(int limit) {
        return tenants.listAll(limit).stream().map(AdminTenantService::toView).toList();
    }

    @Transactional
    public AuthDtos.TenantView create(AuthPrincipal actor, AuthDtos.CreateTenantRequest request) {
        String tenantId = Tenants.require(request.tenantId());
        int quota = request.dailyQuota() == null ? DEFAULT_DAILY_QUOTA : request.dailyQuota();
        if (quota < 0) {
            throw ApiException.badRequest("INVALID_QUOTA", "daily_quota must be >= 0");
        }
        if (tenants.exists(tenantId)) {
            throw ApiException.conflict("TENANT_EXISTS", "tenant already exists: " + tenantId);
        }
        try {
            tenants.create(tenantId, request.name().trim(), "active", quota);
        } catch (DuplicateKeyException e) {
            throw ApiException.conflict("TENANT_EXISTS", "tenant already exists: " + tenantId);
        }
        audit.record(tenantId, actor.userId(), actor.username(), "tenant.create", "tenant", tenantId,
                Map.of("daily_quota", quota));
        return toView(tenants.findById(tenantId).orElseThrow());
    }

    @Transactional
    public AuthDtos.TenantView update(AuthPrincipal actor, String tenantId, AuthDtos.UpdateTenantRequest request) {
        TenantRepository.TenantRow existing = tenants.findById(tenantId)
                .orElseThrow(() -> ApiException.notFound("TENANT_NOT_FOUND", "tenant not found: " + tenantId));
        Integer quota = request.dailyQuota();
        if (quota != null && quota < 0) {
            throw ApiException.badRequest("INVALID_QUOTA", "daily_quota must be >= 0");
        }
        String status = request.status() == null ? null : request.status().trim().toLowerCase();
        if (status != null && !status.equals("active") && !status.equals("disabled")) {
            throw ApiException.badRequest("INVALID_STATUS", "status must be active|disabled");
        }
        tenants.update(tenantId, request.name(), quota, status);
        audit.record(tenantId, actor.userId(), actor.username(), "tenant.update", "tenant", tenantId,
                Map.of("name", String.valueOf(request.name()), "daily_quota", String.valueOf(quota),
                        "status", String.valueOf(status)));
        return toView(tenants.findById(tenantId).orElseThrow());
    }

    private static AuthDtos.TenantView toView(TenantRepository.TenantRow row) {
        Instant created = row.createdAt();
        return new AuthDtos.TenantView(row.id(), row.name(), row.status(), row.dailyQuota(),
                created == null ? null : created.toString());
    }
}
