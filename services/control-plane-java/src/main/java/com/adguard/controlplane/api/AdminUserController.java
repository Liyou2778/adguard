package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.AuthDtos;
import com.adguard.controlplane.security.AuthContext;
import com.adguard.controlplane.service.AdminUserService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/** Platform administrator user management (M8 / FR-10). */
@RestController
@RequestMapping("/admin/users")
public class AdminUserController {

    private final AdminUserService userService;

    public AdminUserController(AdminUserService userService) {
        this.userService = userService;
    }

    @GetMapping
    public List<AuthDtos.UserView> list(@RequestParam(required = false) String tenantId,
                                        @RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return userService.list(tenantId, limit);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public AuthDtos.UserView create(@Valid @RequestBody AuthDtos.CreateUserRequest request) {
        return userService.create(AuthContext.require(), request);
    }

    @PatchMapping("/{id}")
    public AuthDtos.UserView update(@PathVariable String id,
                                    @RequestBody AuthDtos.UpdateUserRequest request) {
        return userService.update(AuthContext.require(), id, request);
    }

    @PostMapping("/{id}/reset-password")
    public Map<String, Object> resetPassword(@PathVariable String id,
                                             @RequestBody(required = false) AuthDtos.ResetPasswordRequest request) {
        String password = request == null ? null : request.newPassword();
        return userService.resetPassword(AuthContext.require(), id, password);
    }
}
