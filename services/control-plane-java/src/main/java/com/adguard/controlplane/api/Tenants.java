package com.adguard.controlplane.api;

import java.util.regex.Pattern;

/** Validates the multi-tenant header before it reaches the service layer. */
public final class Tenants {

    private static final Pattern TENANT_ID = Pattern.compile("^[a-z0-9][a-z0-9_-]{2,63}$");

    private Tenants() {
    }

    public static String require(String tenantId) {
        if (tenantId == null || tenantId.isBlank()) {
            throw ApiException.badRequest("MISSING_TENANT", "X-Tenant-Id header is required");
        }
        String trimmed = tenantId.trim();
        if (!TENANT_ID.matcher(trimmed).matches()) {
            throw ApiException.badRequest("INVALID_TENANT",
                    "tenant id must match ^[a-z0-9][a-z0-9_-]{2,63}$");
        }
        return trimmed;
    }
}
