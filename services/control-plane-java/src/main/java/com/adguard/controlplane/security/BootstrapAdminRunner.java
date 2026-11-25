package com.adguard.controlplane.security;

import com.adguard.controlplane.repo.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.security.SecureRandom;
import java.util.Base64;

/**
 * Creates the first platform administrator on startup when the user table has no admin.
 *
 * <p>Password priority: {@code ADGUARD_BOOTSTRAP_ADMIN_PASSWORD} env var, otherwise a random
 * password printed once to the log. The password is never written to the database in plain text.</p>
 */
@Component
public class BootstrapAdminRunner implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(BootstrapAdminRunner.class);
    private static final String CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%";

    private final UserRepository userRepository;
    private final SecurityProperties properties;

    public BootstrapAdminRunner(UserRepository userRepository, SecurityProperties properties) {
        this.userRepository = userRepository;
        this.properties = properties;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (userRepository.existsAdmin()) {
            return;
        }
        String username = properties.getBootstrapAdminUsername() == null
                || properties.getBootstrapAdminUsername().isBlank()
                ? "admin" : properties.getBootstrapAdminUsername().trim();
        String configuredPassword = properties.getBootstrapAdminPassword();
        boolean generated = configuredPassword == null || configuredPassword.isBlank();
        String password = generated ? randomPassword(20) : configuredPassword;
        userRepository.createAdmin(username, PasswordHasher.hash(password),
                properties.getBootstrapAdminEmail());
        if (generated) {
            log.warn("BOOTSTRAP ADMIN CREATED (shown once): username={} password={}", username, password);
        } else {
            log.info("bootstrap admin created from ADGUARD_BOOTSTRAP_ADMIN_PASSWORD: username={}", username);
        }
    }

    private static String randomPassword(int length) {
        SecureRandom random = new SecureRandom();
        StringBuilder builder = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            builder.append(CHARS.charAt(random.nextInt(CHARS.length())));
        }
        return builder.toString();
    }
}
