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

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.regex.Pattern;

/** Registration / login / self-service password (M8 identity + M12 account security). */
@Service
public class AccountService {

    private static final Pattern USERNAME = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$");
    private static final int DEFAULT_TENANT_QUOTA = 100_000;
    /** Lock the account after this many consecutive failed logins... */
    private static final int MAX_LOGIN_ATTEMPTS = 5;
    /** ...for this many minutes (also acts as login rate limiting per account). */
    private static final int LOCK_MINUTES = 15;

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
                    PasswordHasher.hash(request.password()), AuthPrincipal.ROLE_OWNER, request.email(), false);
            audit.record(tenantId, user.id(), user.username(), "tenant.register", "tenant", tenantId,
                    Map.of("tenant_name", request.tenantName()));
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
        if (user.locked()) {
            long wait = Math.max(1, Duration.between(Instant.now(), user.lockedUntil()).toMinutes() + 1);
            throw ApiException.forbidden("ACCOUNT_LOCKED",
                    "too many failed logins; account locked, try again in ~" + wait + " minutes");
        }
        if (!PasswordHasher.matches(request.password(), user.passwordHash())) {
            UserRepository.LoginFailure failure = users.recordLoginFailure(user.id(),
                    MAX_LOGIN_ATTEMPTS, LOCK_MINUTES);
            audit.record(user.tenantId(), user.id(), user.username(), "auth.login_failed", "user", user.id(),
                    Map.of("attempts", failure.attempts()));
            if (failure.lockedUntil() != null) {
                audit.record(user.tenantId(), user.id(), user.username(), "auth.account_locked", "user",
                        user.id(), Map.of("minutes", LOCK_MINUTES));
                throw ApiException.forbidden("ACCOUNT_LOCKED",
                        "too many failed logins; account locked for " + LOCK_MINUTES + " minutes");
            }
            throw ApiException.unauthorized("INVALID_CREDENTIALS", "invalid username or password");
        }
        users.resetLoginFailures(user.id());
        users.touchLastLogin(user.id());
        return tokenFor(user);
    }

    public AuthDtos.UserView me(AuthPrincipal principal) {
        if (principal.userId() == null) {
            // automation admin key: no persisted user row
            return new AuthDtos.UserView(null, principal.tenantId(), principal.username(), null,
                    principal.role(), "active", false, null, null);
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
        users.setPassword(user.id(), PasswordHasher.hash(request.newPassword()), false);
        audit.record(user.tenantId(), user.id(), user.username(), "user.change_password", "user", user.id(),
                Map.of("forced", user.mustChangePassword()));
    }

    private AuthDtos.TokenResponse tokenFor(UserRepository.UserRow user) {
        AuthPrincipal principal = new AuthPrincipal(user.id(), user.tenantId(), user.username(), user.role(),
                user.mustChangePassword());
        return new AuthDtos.TokenResponse(jwtService.issue(principal), "Bearer",
                jwtService.ttlSeconds(), user.mustChangePassword(), toView(user));
    }

    public AuthDtos.UserView toView(UserRepository.UserRow user) {
        return toViewStatic(user);
    }

    public static AuthDtos.UserView toViewStatic(UserRepository.UserRow user) {
        return new AuthDtos.UserView(user.id(), user.tenantId(), user.username(), user.email(), user.role(),
                user.status(), user.mustChangePassword(), format(user.lastLoginAt()), format(user.createdAt()));
    }

    private void requireUsername(String username) {
        if (username == null || !USERNAME.matcher(username.trim()).matches()) {
            throw ApiException.badRequest("INVALID_USERNAME",
                    "username must match ^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$");
        }
    }

    private static String format(Instant instant) {
        return instant == null ? null : instant.toString();
    }
}
