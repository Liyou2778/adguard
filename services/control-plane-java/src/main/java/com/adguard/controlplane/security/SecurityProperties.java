package com.adguard.controlplane.security;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Authentication configuration (M8 / FR-10). Values come from environment variables. */
@ConfigurationProperties(prefix = "adguard.auth")
public class SecurityProperties {

    private String jwtSecret = "";
    private int tokenTtlHours = 12;
    private String adminKey = "";
    private String bootstrapAdminUsername = "admin";
    private String bootstrapAdminPassword = "";
    private String bootstrapAdminEmail = "";
    /** true in production: every request (except public paths) must present a JWT or X-Admin-Key. */
    private boolean required = false;

    public String getJwtSecret() {
        return jwtSecret;
    }

    public void setJwtSecret(String jwtSecret) {
        this.jwtSecret = jwtSecret;
    }

    public int getTokenTtlHours() {
        return tokenTtlHours;
    }

    public void setTokenTtlHours(int tokenTtlHours) {
        this.tokenTtlHours = tokenTtlHours;
    }

    public String getAdminKey() {
        return adminKey;
    }

    public void setAdminKey(String adminKey) {
        this.adminKey = adminKey;
    }

    public String getBootstrapAdminUsername() {
        return bootstrapAdminUsername;
    }

    public void setBootstrapAdminUsername(String bootstrapAdminUsername) {
        this.bootstrapAdminUsername = bootstrapAdminUsername;
    }

    public String getBootstrapAdminPassword() {
        return bootstrapAdminPassword;
    }

    public void setBootstrapAdminPassword(String bootstrapAdminPassword) {
        this.bootstrapAdminPassword = bootstrapAdminPassword;
    }

    public String getBootstrapAdminEmail() {
        return bootstrapAdminEmail;
    }

    public boolean isRequired() {
        return required;
    }

    public void setRequired(boolean required) {
        this.required = required;
    }

    public void setBootstrapAdminEmail(String bootstrapAdminEmail) {
        this.bootstrapAdminEmail = bootstrapAdminEmail;
    }
}
