package com.adguard.controlplane.domain;

/** Request/response DTOs for identity and tenant administration (M8 / FR-10). */
public final class AuthDtos {

    private AuthDtos() {
    }

    public record LoginRequest(
            @jakarta.validation.constraints.NotBlank String username,
            @jakarta.validation.constraints.NotBlank String password) {
    }

    public record RegisterRequest(
            @jakarta.validation.constraints.NotBlank String tenantId,
            @jakarta.validation.constraints.NotBlank String tenantName,
            @jakarta.validation.constraints.NotBlank String username,
            @jakarta.validation.constraints.NotBlank
            @jakarta.validation.constraints.Size(min = 8, max = 72) String password,
            String email) {
    }

    public record UserView(String id, String tenantId, String username, String email, String role,
                           String status, String lastLoginAt, String createdAt) {
    }

    public record TokenResponse(String accessToken, String tokenType, long expiresIn, UserView user) {
    }

    public record ChangePasswordRequest(
            @jakarta.validation.constraints.NotBlank String oldPassword,
            @jakarta.validation.constraints.NotBlank
            @jakarta.validation.constraints.Size(min = 8, max = 72) String newPassword) {
    }

    public record CreateUserRequest(
            @jakarta.validation.constraints.NotBlank String username,
            @jakarta.validation.constraints.NotBlank
            @jakarta.validation.constraints.Size(min = 8, max = 72) String password,
            @jakarta.validation.constraints.NotBlank String role,
            String tenantId,
            String email) {
    }

    public record UpdateUserRequest(String role, String status) {
    }

    public record ResetPasswordRequest(String newPassword) {
    }

    public record CreateTenantRequest(
            @jakarta.validation.constraints.NotBlank String tenantId,
            @jakarta.validation.constraints.NotBlank String name,
            Integer dailyQuota) {
    }

    public record UpdateTenantRequest(String name, Integer dailyQuota, String status) {
    }

    public record TenantView(String id, String name, String status, int dailyQuota, String createdAt) {
    }
}
