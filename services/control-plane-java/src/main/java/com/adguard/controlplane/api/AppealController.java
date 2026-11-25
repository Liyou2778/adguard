package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.AppealDtos;
import com.adguard.controlplane.security.AuthContext;
import com.adguard.controlplane.service.AppealService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/** Merchant appeal API (JWT tenant-scoped; M8 / FR-10). */
@RestController
@RequestMapping("/api/v1/appeals")
public class AppealController {

    private final AppealService appealService;

    public AppealController(AppealService appealService) {
        this.appealService = appealService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public AppealDtos.AppealView create(@Valid @RequestBody AppealDtos.CreateAppealRequest request) {
        return appealService.create(AuthContext.requireTenant(), AuthContext.require(), request);
    }

    @GetMapping
    public List<AppealDtos.AppealView> list(@RequestParam(required = false) String status,
                                            @RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return appealService.listMine(AuthContext.requireTenant(), status, limit);
    }

    @GetMapping("/{id}")
    public AppealDtos.AppealView get(@PathVariable String id) {
        return appealService.getMine(AuthContext.requireTenant(), id);
    }
}
