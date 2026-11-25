package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.api.Tenants;
import com.adguard.controlplane.domain.AuthDtos;
import com.adguard.controlplane.repo.AuditRepository;
import com.adguard.controlplane.repo.TenantRepository;
import com.adguard.controlplane.repo.UserRepository;
import com.adguard.controlplane.security.AuthPrincipal;
import com.adguard.controlplane.security.JwtService;
import com.adguard.controlplane.security.PasswordHasher;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.regex.Pattern;

/** Registration / login / self-service password (M8 / FR-10). */
@Service
public class AccountService {

    private static final Pattern USERNAME = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$");
    private static final int DEFAULT_TENANT_QUOTA = 100_000;

    private final UserRepository users;
    private final TenantRepository tenants;
    private final JwtService jwtService;
    private final AuditRepository audit;

    public AccountService(UserRepository users, TenantRepository tenants, JwtService jwtService,
                          AuditRepository audit) {
        this.users = users;
        this.tenants = tenants;
        this.jwtService = jwtService;
        this.audit = audit;
    }

    @Transactional
    public AuthDtos.TokenResponse register(AuthDtos.RegisterRequest request) {
        String tenantId = Tenants.require(request.tenantId());
        requireUsername(request.username());
        if (tenants.exists(tenantId)) {
            throw ApiException.conflict("TENANT_EXISTS", "tenant already exists: " + tenantId);
        }
        if (users.existsByUsername(request.username())) {
            throw ApiException.conflict("USERNAME_TAKEN", "username already exists");
        }
        try {
            tenants.create(tenantId, request.tenantName().trim(), "active", DEFAULT_TENANT_QUOTA);
            UserRepository.UserRow user = users.create(tenantId, request.username().trim(),
                    PasswordHasher.hash(request.password()), AuthPrincipal.ROLE_OWNER, request.email());
            audit.record(tenantId, user.id(), user.username(), "tenant.register", "tenant", tenantId,
                    java.util.Map.of("tenant_name", request.tenantName()));
            return tokenFor(user);
        } catch (DuplicateKeyException e) {
            throw ApiException.conflict("REGISTRATION_CONFLICT", "tenant or username was created concurrently");
        }
    }

    public AuthDtos.TokenResponse login(AuthDtos.LoginRequest request) {
        UserRepository.UserRow user = users.findByUsername(request.username().trim())
                .orElseThrow(() -> ApiException.unauthorized("INVALID_CREDENTIALS",
                        "invalid username or password"));
        if (!"active".equals(user.status())) {
            throw ApiException.forbidden("ACCOUNT_DISABLED", "account is disabled");
        }
        if (!PasswordHasher.matches(request.password(), user.passwordHash())) {
            throw ApiException.unauthorized("INVALID_CREDENTIALS", "invalid username or password");
        }
        users.touchLastLogin(user.id());
        return tokenFor(user);
    }

    public AuthDtos.UserView me(AuthPrincipal principal) {
        if (principal.userId() == null) {
            // automation admin key: no persisted user row
            return new AuthDtos.UserView(null, principal.tenantId(), principal.username(), null,
                    principal.role(), "active", null, null);
        }
        UserRepository.UserRow user = users.findById(principal.userId())
                .orElseThrow(() -> ApiException.unauthorized("USER_NOT_FOUND", "user no longer exists"));
        return toView(user);
    }

    public void changePassword(AuthPrincipal principal, AuthDtos.ChangePasswordRequest request) {
        if (principal.userId() == null) {
            throw ApiException.forbidden("AUTOMATION_ACCOUNT", "admin key cannot change a password");
        }
        UserRepository.UserRow user = users.findById(principal.userId())
                .orElseThrow(() -> ApiException.unauthorized("USER_NOT_FOUND", "user no longer exists"));
        if (!PasswordHasher.matches(request.oldPassword(), user.passwordHash())) {
            throw ApiException.unauthorized("INVALID_CREDENTIALS", "old password is incorrect");
        }
        users.updatePassword(user.id(), PasswordHasher.hash(request.newPassword()));
        audit.record(user.tenantId(), user.id(), user.username(), "user.change_password", "user", user.id(), null);
    }

    private AuthDtos.TokenResponse tokenFor(UserRepository.UserRow user) {
        AuthPrincipal principal = new AuthPrincipal(user.id(), user.tenantId(), user.username(), user.role());
        return new AuthDtos.TokenResponse(jwtService.issue(principal), "Bearer",
                jwtService.ttlSeconds(), toView(user));
    }

    public AuthDtos.UserView toView(UserRepository.UserRow user) {
        return toViewStatic(user);
    }

    public static AuthDtos.UserView toViewStatic(UserRepository.UserRow user) {
        return new AuthDtos.UserView(user.id(), user.tenantId(), user.username(), user.email(), user.role(),
                user.status(), format(user.lastLoginAt()), format(user.createdAt()));
    }

    private void requireUsername(String username) {
        if (username == null || !USERNAME.matcher(username.trim()).matches()) {
            throw ApiException.badRequest("INVALID_USERNAME",
                    "username must match ^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$");
        }
    }

    private static String format(java.time.Instant instant) {
        return instant == null ? null : instant.toString();
    }
}
