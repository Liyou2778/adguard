package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.AuthDtos;
import com.adguard.controlplane.security.AuthContext;
import com.adguard.controlplane.service.AdminTenantService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/** Platform administrator tenant / quota management (M8 + M9). */
@RestController
@RequestMapping("/admin/tenants")
public class AdminTenantController {

    private final AdminTenantService tenantService;

    public AdminTenantController(AdminTenantService tenantService) {
        this.tenantService = tenantService;
    }

    @GetMapping
    public List<AuthDtos.TenantView> list(@RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return tenantService.list(limit);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public AuthDtos.TenantView create(@Valid @RequestBody AuthDtos.CreateTenantRequest request) {
        return tenantService.create(AuthContext.require(), request);
    }

    @PatchMapping("/{id}")
    public AuthDtos.TenantView update(@PathVariable String id,
                                      @RequestBody AuthDtos.UpdateTenantRequest request) {
        return tenantService.update(AuthContext.require(), id, request);
    }
}
