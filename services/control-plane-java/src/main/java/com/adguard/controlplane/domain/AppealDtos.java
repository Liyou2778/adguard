package com.adguard.controlplane.domain;

/** Appeal + review DTOs (M8 / FR-10). */
public final class AppealDtos {

    private AppealDtos() {
    }

    public record CreateAppealRequest(
            @jakarta.validation.constraints.NotBlank String judgmentId,
            @jakarta.validation.constraints.NotBlank String reason,
            String detail) {
    }

    public record ReviewAppealRequest(
            @jakarta.validation.constraints.NotBlank String finalDecision,
            @jakarta.validation.constraints.NotBlank
            @jakarta.validation.constraints.Size(min = 3, max = 2000) String comment) {
    }

    public record AppealView(String id, String judgmentId, String assetId, String tenantId, String reason,
                             String detail, String status, String originalDecision, String finalDecision,
                             String reviewer, String reviewComment, String createdAt, String reviewedAt) {
    }
}
