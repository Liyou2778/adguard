package com.adguard.controlplane.security;

import com.adguard.controlplane.api.ApiException;
import org.springframework.http.HttpStatus;

/** Request-scoped holder for the authenticated principal (set by {@link AuthInterceptor}). */
public final class AuthContext {

    private static final ThreadLocal<AuthPrincipal> CURRENT = new ThreadLocal<>();

    private AuthContext() {
    }

    public static void set(AuthPrincipal principal) {
        CURRENT.set(principal);
    }

    public static void clear() {
        CURRENT.remove();
    }

    public static AuthPrincipal current() {
        return CURRENT.get();
    }

    public static AuthPrincipal require() {
        AuthPrincipal principal = CURRENT.get();
        if (principal == null) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHORIZED", "authentication required");
        }
        return principal;
    }

    /** Tenant scope for merchant APIs: the tenant carried by the token (never a query parameter). */
    public static String requireTenant() {
        AuthPrincipal principal = require();
        if (principal.tenantId() == null || principal.tenantId().isBlank()) {
            throw new ApiException(HttpStatus.FORBIDDEN, "TENANT_REQUIRED",
                    "this endpoint requires a tenant-scoped account");
        }
        return principal.tenantId();
    }
}
