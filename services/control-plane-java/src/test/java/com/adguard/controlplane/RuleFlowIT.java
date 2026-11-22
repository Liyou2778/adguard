package com.adguard.controlplane;

import com.adguard.controlplane.domain.RuleDefinition;
import com.adguard.controlplane.domain.RuleDetail;
import com.adguard.controlplane.domain.SnapshotView;
import com.adguard.controlplane.service.RuleService;
import com.adguard.controlplane.service.SnapshotService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * End-to-end rule flow against a real MySQL 8 (Flyway migration included).
 * Runs in the Maven "verify" phase (failsafe, *IT.java) because it needs Docker.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@Testcontainers
class RuleFlowIT {

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0")
            .withDatabaseName("adguard")
            .withUsername("adguard")
            .withPassword("adguard");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url",
                () -> MYSQL.getJdbcUrl() + "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC");
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
    }

    @Autowired
    private RuleService ruleService;

    @Autowired
    private SnapshotService snapshotService;

    private static RuleDefinition rule(String ruleId, String family) {
        return new RuleDefinition(ruleId, "中华人民共和国广告法", "第九条", "（三）", family, "high",
                "不得使用绝对化用语", List.of("最好", "最佳"),
                new RuleDefinition.Detection(List.of("regex:最[好佳]"), "semantic prompt", List.of()),
                new RuleDefinition.Penalty("第五十七条", 200000, 1000000),
                LocalDate.of(2015, 9, 1), null, "https://example.gov.cn/law");
    }

    @Test
    void createActivateAndSnapshotFlow() {
        String tenant = "it-tenant";
        RuleDetail created = ruleService.create(tenant, rule("ADV-IT-001", "absolute_terms"));
        assertThat(created.summary().currentVersion()).isEqualTo(1);
        assertThat(created.summary().activeVersion()).isNull();
        assertThat(created.versions()).hasSize(1);

        RuleDetail revised = ruleService.update(tenant, "ADV-IT-001", rule("ADV-IT-001", "absolute_terms"));
        assertThat(revised.summary().currentVersion()).isEqualTo(2);

        ruleService.create(tenant, rule("ADV-IT-002", "false_advertising"));

        SnapshotView snapshot = ruleService.activate(tenant, "ADV-IT-001");
        assertThat(snapshot.ruleCount()).isEqualTo(1);
        assertThat(snapshot.checksum()).hasSize(64);
        assertThat(snapshot.payload().get(0).path("rule_id").asText()).isEqualTo("ADV-IT-001");
        assertThat(snapshot.seq()).isEqualTo(1);
        // The snapshot must carry the *active* version payload (version 2), not version 1.
        assertThat(snapshot.payload().get(0).path("rule_id").asText()).isEqualTo("ADV-IT-001");

        SnapshotView afterSecond = ruleService.activate(tenant, "ADV-IT-002");
        assertThat(afterSecond.seq()).isEqualTo(2);
        assertThat(afterSecond.ruleCount()).isEqualTo(2);
        assertThat(afterSecond.checksum()).isNotEqualTo(snapshot.checksum());
        assertThat(afterSecond.payload().get(0).path("rule_id").asText()).isEqualTo("ADV-IT-001");
        assertThat(afterSecond.payload().get(1).path("rule_id").asText()).isEqualTo("ADV-IT-002");

        SnapshotView latest = snapshotService.latest(tenant);
        assertThat(latest.id()).isEqualTo(afterSecond.id());
        SnapshotView replay = snapshotService.byId(tenant, snapshot.id());
        assertThat(replay.checksum()).isEqualTo(snapshot.checksum());

        assertThatThrownBy(() -> ruleService.activate(tenant, "ADV-IT-001"))
                .hasMessageContaining("already active");
    }

    @Test
    void snapshotRequiresAtLeastOneActiveRule() {
        String tenant = "it-tenant-draft-only";
        ruleService.create(tenant, rule("ADV-IT-200", "absolute_terms"));
        assertThatThrownBy(() -> snapshotService.create(tenant, "test"))
                .hasMessageContaining("no active rules");
    }

    @Test
    void snapshotForUnknownTenantIsNotFound() {
        assertThatThrownBy(() -> snapshotService.create("it-tenant-unknown", "test"))
                .hasMessageContaining("tenant not found");
    }

    @Test
    void tenantIsolationIsEnforced() {
        String tenantA = "it-tenant-a";
        String tenantB = "it-tenant-b";
        ruleService.create(tenantA, rule("ADV-IT-100", "absolute_terms"));
        ruleService.create(tenantB, rule("ADV-IT-101", "absolute_terms"));

        assertThat(ruleService.list(tenantA, null, null, 10)).hasSize(1);
        assertThat(ruleService.list(tenantA, null, null, 10).get(0).ruleId()).isEqualTo("ADV-IT-100");
        assertThatThrownBy(() -> ruleService.get(tenantB, "ADV-IT-100"))
                .hasMessageContaining("rule not found");
        assertThatThrownBy(() -> snapshotService.byId(tenantB, "unknown-snapshot"))
                .hasMessageContaining("snapshot not found");
    }
}
