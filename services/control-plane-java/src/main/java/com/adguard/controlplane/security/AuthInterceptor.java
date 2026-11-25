package com.adguard.controlplane.security;

import com.adguard.controlplane.api.ApiException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Optional;
import java.util.Set;

/**
 * AuthN/AuthZ for the control plane (FR-10).
 *
 * <p>Accepts either a Bearer JWT (users) or the automation key {@code X-Admin-Key}
 * (smoke tests / ops tooling; on tenant endpoints it impersonates the {@code X-Tenant-Id} header).
 * In development ({@code ADGUARD_AUTH_REQUIRED=false}) missing credentials are accepted as an
 * implicit admin so existing local scripts keep working; production sets the flag to true.</p>
 */
@Component
@ConditionalOnProperty(prefix = "adguard.auth", name = "enabled", havingValue = "true", matchIfMissing = true)
public class AuthInterceptor implements HandlerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(AuthInterceptor.class);
    // /internal/* is service-to-service (gateway -> control plane) and is scoped by the host
    // firewall / security group; it is not exposed through nginx.
    private static final Set<String> PUBLIC_PREFIXES = Set.of("/actuator/", "/admin-ui/", "/public/", "/internal/");
    private static final Set<String> PUBLIC_EXACT = Set.of("/auth/login", "/auth/register", "/healthz",
            "/error", "/favicon.ico");
    private static final Set<String> ADMIN_ONLY_PREFIXES = Set.of(
            "/admin/users", "/admin/tenants", "/admin/billing", "/admin/usage",
            "/admin/rules", "/admin/regulations", "/admin/feedback-pairs");
    private static final Set<String> TENANT_API_PREFIXES = Set.of("/api/v1/appeals", "/api/v1/usage");

    private final JwtService jwtService;
    private final SecurityProperties properties;

    public AuthInterceptor(JwtService jwtService, SecurityProperties properties) {
        this.jwtService = jwtService;
        this.properties = properties;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String path = request.getRequestURI();
        if ("OPTIONS".equalsIgnoreCase(request.getMethod()) || isPublic(path)) {
            return true;
        }
        AuthPrincipal principal = resolve(request);
        if (principal == null) {
            if (properties.isRequired()) {
                throw new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHORIZED",
                        "authentication required (Bearer token or X-Admin-Key)");
            }
            // Development convenience: behave as the automation admin.
            principal = new AuthPrincipal(null, request.getHeader("X-Tenant-Id"), "dev-admin",
                    AuthPrincipal.ROLE_ADMIN);
        }
        AuthContext.set(principal);

        if (path.startsWith("/admin/")) {
            if (isAdminOnly(path)) {
                if (!principal.isAdmin()) {
                    throw new ApiException(HttpStatus.FORBIDDEN, "ADMIN_REQUIRED",
                            "administrator role required for this endpoint");
                }
            } else if (!principal.isReviewerOrAdmin()) {
                throw new ApiException(HttpStatus.FORBIDDEN, "REVIEWER_REQUIRED",
                        "reviewer or administrator role required for this endpoint");
            }
            return true;
        }
        if (path.startsWith("/api/")) {
            requireTenantApi(principal, path);
            return true;
        }
        // Static resources / internal endpoints are allowed (internal snapshot is network-scoped).
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler,
                                Exception ex) {
        AuthContext.clear();
    }

    private void requireTenantApi(AuthPrincipal principal, String path) {
        boolean tenantEndpoint = TENANT_API_PREFIXES.stream().anyMatch(path::startsWith);
        if (tenantEndpoint) {
            if (principal.isAdmin() && principal.tenantId() != null && !principal.tenantId().isBlank()) {
                return; // automation key with X-Tenant-Id
            }
            if (!principal.isTenantUser()) {
                throw new ApiException(HttpStatus.FORBIDDEN, "TENANT_REQUIRED",
                        "tenant account required for this endpoint");
            }
            if (principal.tenantId() == null || principal.tenantId().isBlank()) {
                throw new ApiException(HttpStatus.FORBIDDEN, "TENANT_REQUIRED",
                        "token is not bound to a tenant");
            }
        }
    }

    private AuthPrincipal resolve(HttpServletRequest request) {
        String authorization = request.getHeader("Authorization");
        if (authorization != null && authorization.startsWith("Bearer ")) {
            Optional<AuthPrincipal> principal = jwtService.verify(authorization.substring(7).trim());
            if (principal.isPresent()) {
                return principal.get();
            }
            log.debug("rejected invalid bearer token for {}", request.getRequestURI());
        }
        String adminKey = request.getHeader("X-Admin-Key");
        String configured = properties.getAdminKey();
        if (adminKey != null && configured != null && !configured.isBlank()
                && MessageDigest.isEqual(adminKey.getBytes(StandardCharsets.UTF_8),
                configured.getBytes(StandardCharsets.UTF_8))) {
            String tenant = request.getHeader("X-Tenant-Id");
            return new AuthPrincipal(null, tenant, "admin-key", AuthPrincipal.ROLE_ADMIN);
        }
        return null;
    }

    private static boolean isAdminOnly(String path) {
        return ADMIN_ONLY_PREFIXES.stream().anyMatch(path::startsWith);
    }

    private static boolean isPublic(String path) {
        return PUBLIC_EXACT.contains(path) || PUBLIC_PREFIXES.stream().anyMatch(path::startsWith);
    }
}
