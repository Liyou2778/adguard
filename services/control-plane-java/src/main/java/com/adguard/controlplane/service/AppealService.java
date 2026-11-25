package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.AppealDtos;
import com.adguard.controlplane.repo.AppealRepository;
import com.adguard.controlplane.repo.AuditRepository;
import com.adguard.controlplane.security.AuthPrincipal;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Merchant appeals, reviewer decisions and DPO feedback pairs (M8 / FR-10). */
@Service
public class AppealService {

    public static final Set<String> REASONS = Set.of("too_strict", "false_positive", "evidence_error", "other");
    public static final Set<String> FINAL_DECISIONS = Set.of("pass", "block", "manual");
    public static final Set<String> APPEALABLE_DECISIONS = Set.of("block", "manual");

    private final AppealRepository repository;
    private final AuditRepository audit;
    private final ObjectMapper objectMapper;

    public AppealService(AppealRepository repository, AuditRepository audit, ObjectMapper objectMapper) {
        this.repository = repository;
        this.audit = audit;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public AppealDtos.AppealView create(String tenantId, AuthPrincipal actor,
                                        AppealDtos.CreateAppealRequest request) {
        String reason = request.reason() == null ? "" : request.reason().trim().toLowerCase();
        if (!REASONS.contains(reason)) {
            throw ApiException.badRequest("INVALID_REASON",
                    "reason must be one of too_strict|false_positive|evidence_error|other");
        }
        AppealRepository.JudgmentRow judgment = repository.findJudgment(tenantId, request.judgmentId())
                .orElseThrow(() -> ApiException.notFound("JUDGMENT_NOT_FOUND",
                        "judgment not found for this tenant"));
        if (!APPEALABLE_DECISIONS.contains(judgment.decision())) {
            throw ApiException.conflict("APPEAL_NOT_ALLOWED",
                    "only block/manual judgments can be appealed");
        }
        if (repository.findOpenByJudgment(judgment.id()).isPresent()) {
            throw ApiException.conflict("APPEAL_ALREADY_OPEN",
                    "an open appeal already exists for this judgment");
        }
        String id = repository.newId();
        repository.insert(id, judgment.id(), judgment.assetId(), tenantId, reason, request.detail(),
                judgment.decision());
        audit.record(tenantId, actor.userId(), actor.username(), "appeal.create", "appeal", id,
                Map.of("judgment_id", judgment.id(), "reason", reason));
        return toView(repository.findById(id).orElseThrow());
    }

    public List<AppealDtos.AppealView> listMine(String tenantId, String status, int limit) {
        return repository.listByTenant(tenantId, status, limit).stream().map(AppealService::toView).toList();
    }

    public AppealDtos.AppealView getMine(String tenantId, String appealId) {
        AppealRepository.AppealRow row = repository.findById(appealId)
                .orElseThrow(() -> ApiException.notFound("APPEAL_NOT_FOUND", "appeal not found"));
        if (!tenantId.equals(row.tenantId())) {
            // Do not leak existence across tenants.
            throw ApiException.notFound("APPEAL_NOT_FOUND", "appeal not found");
        }
        return toView(row);
    }

    public List<AppealDtos.AppealView> listForAdmin(String tenantId, String status, int limit) {
        return repository.listForAdmin(tenantId, status, limit).stream().map(AppealService::toView).toList();
    }

    /** Reviewer view: appeal + judgment + violations + asset evidence. */
    public Map<String, Object> adminDetail(String appealId) {
        AppealRepository.AppealRow appeal = repository.findById(appealId)
                .orElseThrow(() -> ApiException.notFound("APPEAL_NOT_FOUND", "appeal not found"));
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("appeal", toView(appeal));
        repository.findJudgment(appeal.tenantId(), appeal.judgmentId()).ifPresent(judgment -> {
            Map<String, Object> judgmentMap = new LinkedHashMap<>();
            judgmentMap.put("id", judgment.id());
            judgmentMap.put("decision", judgment.decision());
            judgmentMap.put("reason", judgment.reason());
            judgmentMap.put("model_version", judgment.modelVersion());
            judgmentMap.put("latency_ms", judgment.latencyMs());
            judgmentMap.put("cost_micro", judgment.costMicro());
            judgmentMap.put("created_at", format(judgment.createdAt()));
            judgmentMap.put("violations", repository.listViolations(judgment.id()));
            detail.put("judgment", judgmentMap);
        });
        repository.findAsset(appeal.tenantId(), appeal.assetId()).ifPresent(asset -> {
            Map<String, Object> assetMap = new LinkedHashMap<>();
            assetMap.put("id", asset.id());
            assetMap.put("type", asset.type());
            assetMap.put("filename", asset.filename());
            assetMap.put("size_bytes", asset.sizeBytes());
            assetMap.put("mime_type", asset.mimeType());
            assetMap.put("caption", asset.caption());
            assetMap.put("parse_status", asset.parseStatus());
            detail.put("asset", assetMap);
        });
        return detail;
    }

    @Transactional
    public Map<String, Object> review(AuthPrincipal reviewer, String appealId,
                                      AppealDtos.ReviewAppealRequest request) {
        AppealRepository.AppealRow appeal = repository.findById(appealId)
                .orElseThrow(() -> ApiException.notFound("APPEAL_NOT_FOUND", "appeal not found"));
        if (!"open".equals(appeal.status())) {
            throw ApiException.conflict("APPEAL_ALREADY_REVIEWED", "appeal is already " + appeal.status());
        }
        String finalDecision = request.finalDecision() == null ? "" : request.finalDecision().trim().toLowerCase();
        if (!FINAL_DECISIONS.contains(finalDecision)) {
            throw ApiException.badRequest("INVALID_FINAL_DECISION",
                    "final_decision must be one of pass|block|manual");
        }
        AppealRepository.JudgmentRow judgment = repository.findJudgment(appeal.tenantId(), appeal.judgmentId())
                .orElseThrow(() -> ApiException.conflict("JUDGMENT_MISSING",
                        "judgment referenced by the appeal no longer exists"));
        String original = appeal.originalDecision() != null ? appeal.originalDecision() : judgment.decision();
        String status = finalDecision.equals(original) ? "rejected" : "accepted";

        repository.updateReview(appealId, status, finalDecision, reviewer.username(), reviewer.userId(),
                request.comment());

        String feedbackId = repository.newId();
        Map<String, Object> chosen = new LinkedHashMap<>();
        chosen.put("decision", finalDecision);
        chosen.put("review_comment", request.comment());
        chosen.put("reviewer", reviewer.username());
        chosen.put("appeal_id", appealId);
        Map<String, Object> rejected = new LinkedHashMap<>();
        rejected.put("decision", original);
        rejected.put("reason", judgment.reason());
        rejected.put("violations", repository.listViolations(judgment.id()));
        String family = firstFamily(rejected);
        repository.insertFeedbackPair(feedbackId, judgment.id(), appealId, appeal.tenantId(), "appeal",
                family, json(chosen), json(rejected), request.comment());
        audit.record(appeal.tenantId(), reviewer.userId(), reviewer.username(), "appeal.review", "appeal",
                appealId, Map.of("status", status, "final_decision", finalDecision,
                        "feedback_pair_id", feedbackId));

        Map<String, Object> detail = adminDetail(appealId);
        detail.put("feedback_pair_id", feedbackId);
        detail.put("review_result", status);
        return detail;
    }

    public List<Map<String, Object>> listFeedbackPairs(Boolean usedInDpo, int limit) {
        return repository.listFeedbackPairs(usedInDpo, limit);
    }

    @Transactional
    public Map<String, Object> markFeedbackUsed(AuthPrincipal actor, String id) {
        int updated = repository.markFeedbackUsed(id);
        if (updated == 0) {
            throw ApiException.notFound("FEEDBACK_PAIR_NOT_FOUND_OR_USED",
                    "feedback pair not found or already marked as used");
        }
        audit.record(null, actor.userId(), actor.username(), "feedback.mark_used", "feedback_pair", id, null);
        return Map.of("id", id, "used_in_dpo", true);
    }

    private static AppealDtos.AppealView toView(AppealRepository.AppealRow row) {
        return new AppealDtos.AppealView(row.id(), row.judgmentId(), row.assetId(), row.tenantId(),
                row.reason(), row.detail(), row.status(), row.originalDecision(), row.finalDecision(),
                row.reviewer(), row.reviewComment(), format(row.createdAt()), format(row.reviewedAt()));
    }

    @SuppressWarnings("unchecked")
    private static String firstFamily(Map<String, Object> rejected) {
        Object violations = rejected.get("violations");
        if (violations instanceof List<?> list && !list.isEmpty() && list.get(0) instanceof Map<?, ?> first) {
            Object family = first.get("family");
            return family == null ? null : String.valueOf(family);
        }
        return null;
    }

    private String json(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("failed to encode feedback pair", e);
        }
    }

    private static String format(Instant instant) {
        return instant == null ? null : instant.toString();
    }
}
