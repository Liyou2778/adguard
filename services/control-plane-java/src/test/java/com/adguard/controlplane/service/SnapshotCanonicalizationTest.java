package com.adguard.controlplane.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The snapshot checksum is the reproducibility contract (ADR-3): the same rule set
 * must always produce the same checksum, regardless of input order, while any
 * content change must change it.
 */
class SnapshotCanonicalizationTest {

    private final ObjectMapper mapper = new ObjectMapper()
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

    private static final String RULE_A = """
            {"rule_id":"ADV-LAW-ART9-3","law":"广告法","article":"第九条","family":"absolute_terms",
             "severity":"high","canonical_terms":["最好","最佳"]}
            """.trim();

    private static final String RULE_B = """
            {"rule_id":"ADV-LAW-ART28","law":"广告法","article":"第二十八条","family":"false_advertising",
             "severity":"high","canonical_terms":["虚假宣传"]}
            """.trim();

    @Test
    void checksumIsOrderIndependent() {
        String first = SnapshotService.canonicalChecksum(mapper, List.of(RULE_A, RULE_B));
        String second = SnapshotService.canonicalChecksum(mapper, List.of(RULE_B, RULE_A));
        assertThat(first).isEqualTo(second).hasSize(64);
    }

    @Test
    void checksumChangesWhenContentChanges() {
        String before = SnapshotService.canonicalChecksum(mapper, List.of(RULE_A));
        String after = SnapshotService.canonicalChecksum(mapper,
                List.of(RULE_A.replace("最好", "最优")));
        assertThat(before).isNotEqualTo(after);
    }

    @Test
    void checksumIsStableAcrossRuns() {
        String checksum = SnapshotService.canonicalChecksum(mapper, List.of(RULE_A, RULE_B));
        for (int i = 0; i < 20; i++) {
            assertThat(SnapshotService.canonicalChecksum(mapper, List.of(RULE_A, RULE_B)))
                    .isEqualTo(checksum);
        }
    }
}
