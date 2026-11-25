package com.adguard.controlplane.security;

/** Authenticated principal resolved from a JWT or the automation admin key. */
public record AuthPrincipal(String userId, String tenantId, String username, String role) {

    public static final String ROLE_OWNER = "owner";
    public static final String ROLE_MEMBER = "member";
    public static final String ROLE_REVIEWER = "reviewer";
    public static final String ROLE_ADMIN = "admin";

    public boolean isAdmin() {
        return ROLE_ADMIN.equals(role);
    }

    public boolean isReviewerOrAdmin() {
        return isAdmin() || ROLE_REVIEWER.equals(role);
    }

    public boolean isTenantUser() {
        return isAdmin() || ROLE_OWNER.equals(role) || ROLE_MEMBER.equals(role);
    }
}
