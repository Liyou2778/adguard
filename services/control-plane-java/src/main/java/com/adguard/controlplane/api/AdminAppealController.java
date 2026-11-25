package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.AppealDtos;
import com.adguard.controlplane.security.AuthContext;
import com.adguard.controlplane.service.AppealService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/** Reviewer / administrator appeal review + DPO feedback pair API (M8 / FR-10). */
@RestController
@RequestMapping("/admin")
public class AdminAppealController {

    private final AppealService appealService;

    public AdminAppealController(AppealService appealService) {
        this.appealService = appealService;
    }

    @GetMapping("/appeals")
    public List<AppealDtos.AppealView> list(@RequestParam(required = false) String tenantId,
                                            @RequestParam(required = false) String status,
                                            @RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return appealService.listForAdmin(tenantId, status, limit);
    }

    @GetMapping("/appeals/{id}")
    public Map<String, Object> detail(@PathVariable String id) {
        return appealService.adminDetail(id);
    }

    @PatchMapping("/appeals/{id}/review")
    public Map<String, Object> review(@PathVariable String id,
                                      @Valid @RequestBody AppealDtos.ReviewAppealRequest request) {
        return appealService.review(AuthContext.require(), id, request);
    }

    @GetMapping("/feedback-pairs")
    public List<Map<String, Object>> feedbackPairs(@RequestParam(required = false) Boolean usedInDpo,
                                                   @RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return appealService.listFeedbackPairs(usedInDpo, limit);
    }

    @PostMapping("/feedback-pairs/{id}/mark-used")
    public Map<String, Object> markUsed(@PathVariable String id) {
        return appealService.markFeedbackUsed(AuthContext.require(), id);
    }
}
