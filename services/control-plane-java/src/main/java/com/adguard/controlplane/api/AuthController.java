package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.AuthDtos;
import com.adguard.controlplane.security.AuthContext;
import com.adguard.controlplane.service.AccountService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

/** Registration / login / self-service (public endpoints under /auth). */
@RestController
@RequestMapping("/auth")
public class AuthController {

    private final AccountService accountService;

    public AuthController(AccountService accountService) {
        this.accountService = accountService;
    }

    @PostMapping("/login")
    public AuthDtos.TokenResponse login(@Valid @RequestBody AuthDtos.LoginRequest request) {
        return accountService.login(request);
    }

    @PostMapping("/register")
    @ResponseStatus(HttpStatus.CREATED)
    public AuthDtos.TokenResponse register(@Valid @RequestBody AuthDtos.RegisterRequest request) {
        return accountService.register(request);
    }

    @GetMapping("/me")
    public AuthDtos.UserView me() {
        return accountService.me(AuthContext.require());
    }

    @PostMapping("/password")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void changePassword(@Valid @RequestBody AuthDtos.ChangePasswordRequest request) {
        accountService.changePassword(AuthContext.require(), request);
    }
}
