package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.api.Tenants;
import com.adguard.controlplane.domain.AuthDtos;
import com.adguard.controlplane.repo.AuditRepository;
import com.adguard.controlplane.repo.TenantRepository;
import com.adguard.controlplane.repo.UserRepository;
import com.adguard.controlplane.security.AuthPrincipal;
import com.adguard.controlplane.security.PasswordHasher;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Platform administrator user management (M8 / FR-10). */
@Service
public class AdminUserService {

    public static final Set<String> ROLES = Set.of(
            AuthPrincipal.ROLE_OWNER, AuthPrincipal.ROLE_MEMBER,
            AuthPrincipal.ROLE_REVIEWER, AuthPrincipal.ROLE_ADMIN);
    public static final Set<String> STATUSES = Set.of("active", "disabled");
    private static final String CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%";

    private final UserRepository users;
    private final TenantRepository tenants;
    private final AuditRepository audit;

    public AdminUserService(UserRepository users, TenantRepository tenants, AuditRepository audit) {
        this.users = users;
        this.tenants = tenants;
        this.audit = audit;
    }

    public List<AuthDtos.UserView> list(String tenantId, int limit) {
        return users.list(tenantId, limit).stream().map(AccountService::toViewStatic).toList();
    }

    @Transactional
    public AuthDtos.UserView create(AuthPrincipal actor, AuthDtos.CreateUserRequest request) {
        String role = request.role() == null ? "" : request.role().trim().toLowerCase();
        if (!ROLES.contains(role)) {
            throw ApiException.badRequest("INVALID_ROLE",
                    "role must be one of owner|member|reviewer|admin");
        }
        if (users.existsByUsername(request.username())) {
            throw ApiException.conflict("USERNAME_TAKEN", "username already exists");
        }
        String tenantId = request.tenantId() == null || request.tenantId().isBlank()
                ? null : Tenants.require(request.tenantId());
        if ((AuthPrincipal.ROLE_OWNER.equals(role) || AuthPrincipal.ROLE_MEMBER.equals(role)) && tenantId == null) {
            throw ApiException.badRequest("TENANT_REQUIRED", "merchant roles require tenant_id");
        }
        if (tenantId != null && !tenants.exists(tenantId)) {
            throw ApiException.notFound("TENANT_NOT_FOUND", "tenant not found: " + tenantId);
        }
        try {
            UserRepository.UserRow user = users.create(tenantId, request.username().trim(),
                    PasswordHasher.hash(request.password()), role, request.email(), true);
            audit.record(tenantId, actor.userId(), actor.username(), "user.create", "user", user.id(),
                    Map.of("role", role));
            return AccountService.toViewStatic(user);
        } catch (DuplicateKeyException e) {
            throw ApiException.conflict("USERNAME_TAKEN", "username already exists");
        }
    }

    @Transactional
    public AuthDtos.UserView update(AuthPrincipal actor, String userId, AuthDtos.UpdateUserRequest request) {
        UserRepository.UserRow target = users.findById(userId)
                .orElseThrow(() -> ApiException.notFound("USER_NOT_FOUND", "user not found"));
        String role = request.role() == null ? null : request.role().trim().toLowerCase();
        String status = request.status() == null ? null : request.status().trim().toLowerCase();
        if (role != null && !ROLES.contains(role)) {
            throw ApiException.badRequest("INVALID_ROLE", "role must be one of owner|member|reviewer|admin");
        }
        if (status != null && !STATUSES.contains(status)) {
            throw ApiException.badRequest("INVALID_STATUS", "status must be active|disabled");
        }
        boolean disablingAdmin = "disabled".equals(status)
                && AuthPrincipal.ROLE_ADMIN.equals(target.role());
        if (disablingAdmin && users.countActiveAdmins() <= 1) {
            throw ApiException.conflict("LAST_ADMIN", "cannot disable the last active administrator");
        }
        users.updateRoleStatus(userId, role, status);
        audit.record(target.tenantId(), actor.userId(), actor.username(), "user.update", "user", userId,
                Map.of("role", String.valueOf(role), "status", String.valueOf(status)));
        return AccountService.toViewStatic(users.findById(userId).orElseThrow());
    }

    @Transactional
    public Map<String, Object> resetPassword(AuthPrincipal actor, String userId, String newPassword) {
        UserRepository.UserRow target = users.findById(userId)
                .orElseThrow(() -> ApiException.notFound("USER_NOT_FOUND", "user not found"));
        boolean generated = newPassword == null || newPassword.isBlank();
        String password = generated ? randomPassword(20) : newPassword;
        if (password.length() < 8 || password.length() > 72) {
            throw ApiException.badRequest("INVALID_PASSWORD", "password must be 8-72 characters");
        }
        users.setPassword(userId, PasswordHasher.hash(password), true);
        audit.record(target.tenantId(), actor.userId(), actor.username(), "user.reset_password",
                "user", userId, java.util.Map.of("must_change_password", true));
        Map<String, Object> response = new java.util.LinkedHashMap<>();
        response.put("user_id", userId);
        response.put("generated", generated);
        response.put("must_change_password", true);
        if (generated) {
            response.put("password", password);
        }
        return response;
    }

    private static String randomPassword(int length) {
        SecureRandom random = new SecureRandom();
        StringBuilder builder = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            builder.append(CHARS.charAt(random.nextInt(CHARS.length())));
        }
        return builder.toString();
    }
}
