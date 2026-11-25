package com.adguard.controlplane.security;

import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

/** BCrypt password hashing (spring-security-crypto only; no servlet security filter chain). */
public final class PasswordHasher {

    private static final BCryptPasswordEncoder ENCODER = new BCryptPasswordEncoder(10);

    private PasswordHasher() {
    }

    public static String hash(String raw) {
        return ENCODER.encode(raw);
    }

    public static boolean matches(String raw, String hash) {
        if (raw == null || hash == null || hash.isBlank()) {
            return false;
        }
        return ENCODER.matches(raw, hash);
    }
}
