package com.adguard.controlplane.security;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Minimal HS256 JWT implementation shared with the Go gateway (no external JWT dependency).
 * Token claims: sub=user id, tenant_id, username, role, iat, exp.
 */
@Service
public class JwtService {

    private static final Logger log = LoggerFactory.getLogger(JwtService.class);
    private static final String HMAC_ALG = "HmacSHA256";

    private final ObjectMapper objectMapper;
    private final SecurityProperties properties;
    private byte[] secret;

    public JwtService(ObjectMapper objectMapper, SecurityProperties properties) {
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    @PostConstruct
    void init() {
        String configured = properties.getJwtSecret() == null ? "" : properties.getJwtSecret().trim();
        if (configured.isEmpty()) {
            // Never silently use a well-known secret in production; generate an ephemeral one and warn.
            byte[] random = new byte[48];
            new java.security.SecureRandom().nextBytes(random);
            this.secret = Base64.getEncoder().encode(random);
            log.warn("ADGUARD_JWT_SECRET is empty: generated an ephemeral signing key. "
                    + "Tokens become invalid on restart; set the env var in production.");
        } else {
            this.secret = configured.getBytes(StandardCharsets.UTF_8);
        }
    }

    public String issue(AuthPrincipal principal) {
        Map<String, Object> header = new LinkedHashMap<>();
        header.put("alg", "HS256");
        header.put("typ", "JWT");
        long now = Instant.now().getEpochSecond();
        Map<String, Object> claims = new LinkedHashMap<>();
        claims.put("sub", principal.userId() == null ? "" : principal.userId());
        claims.put("tenant_id", principal.tenantId());
        claims.put("username", principal.username());
        claims.put("role", principal.role());
        claims.put("pwd_change", principal.mustChangePassword());
        claims.put("iat", now);
        claims.put("exp", now + Math.max(1, properties.getTokenTtlHours()) * 3600L);
        try {
            String headerPart = base64Url(objectMapper.writeValueAsBytes(header));
            String payloadPart = base64Url(objectMapper.writeValueAsBytes(claims));
            String signingInput = headerPart + "." + payloadPart;
            String signature = base64Url(hmac(signingInput));
            return signingInput + "." + signature;
        } catch (Exception e) {
            throw new IllegalStateException("failed to issue JWT", e);
        }
    }

    public Optional<AuthPrincipal> verify(String token) {
        if (token == null || token.isBlank()) {
            return Optional.empty();
        }
        String[] parts = token.split("\\.");
        if (parts.length != 3) {
            return Optional.empty();
        }
        try {
            byte[] expected = hmac(parts[0] + "." + parts[1]);
            byte[] actual = Base64.getUrlDecoder().decode(parts[2]);
            if (!MessageDigest.isEqual(expected, actual)) {
                return Optional.empty();
            }
            Map<String, Object> claims = objectMapper.readValue(Base64.getUrlDecoder().decode(parts[1]),
                    new TypeReference<Map<String, Object>>() {
                    });
            Object exp = claims.get("exp");
            if (exp instanceof Number number && number.longValue() < Instant.now().getEpochSecond()) {
                return Optional.empty();
            }
            boolean mustChange = Boolean.TRUE.equals(claims.get("pwd_change"));
            return Optional.of(new AuthPrincipal(
                    str(claims.get("sub")),
                    str(claims.get("tenant_id")),
                    str(claims.get("username")),
                    str(claims.get("role")),
                    mustChange));
        } catch (Exception e) {
            return Optional.empty();
        }
    }

    public long ttlSeconds() {
        return Math.max(1, properties.getTokenTtlHours()) * 3600L;
    }

    private byte[] hmac(String input) throws Exception {
        Mac mac = Mac.getInstance(HMAC_ALG);
        mac.init(new SecretKeySpec(secret, HMAC_ALG));
        return mac.doFinal(input.getBytes(StandardCharsets.UTF_8));
    }

    private static String base64Url(byte[] bytes) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static String str(Object value) {
        return value == null ? null : String.valueOf(value);
    }
}
