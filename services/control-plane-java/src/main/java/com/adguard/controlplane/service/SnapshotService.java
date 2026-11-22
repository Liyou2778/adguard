package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.SnapshotView;
import com.adguard.controlplane.repo.RuleRepository;
import com.adguard.controlplane.repo.SnapshotRepository;
import com.adguard.controlplane.repo.SnapshotRow;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

/**
 * Creates and reads immutable rule snapshots (ADR-3).
 *
 * <p>Snapshot canonicalisation rules (must stay stable forever, they are the
 * reproducibility contract):
 * <ol>
 *   <li>rules are sorted by {@code rule_id};</li>
 *   <li>serialisation uses the control plane's Jackson config (snake_case, non-null only);</li>
 *   <li>checksum = sha256 hex of the canonical JSON string.</li>
 * </ol>
 */
@Service
public class SnapshotService {

    private static final Logger log = LoggerFactory.getLogger(SnapshotService.class);

    private final RuleRepository ruleRepository;
    private final SnapshotRepository snapshotRepository;
    private final ObjectMapper objectMapper;

    public SnapshotService(RuleRepository ruleRepository,
                           SnapshotRepository snapshotRepository,
                           ObjectMapper objectMapper) {
        this.ruleRepository = ruleRepository;
        this.snapshotRepository = snapshotRepository;
        this.objectMapper = objectMapper;
    }

    /**
     * Materialises every currently active rule into a new immutable snapshot.
     * Must run inside a transaction; takes the per-tenant row lock so that
     * snapshot sequence numbers cannot collide.
     */
    @Transactional
    public SnapshotView create(String tenantId, String createdBy) {
        if (!ruleRepository.lockTenant(tenantId)) {
            throw ApiException.notFound("TENANT_NOT_FOUND", "tenant not found: " + tenantId);
        }
        List<String> payloads = ruleRepository.activePayloads(tenantId);
        if (payloads.isEmpty()) {
            throw ApiException.conflict("NO_ACTIVE_RULES",
                    "cannot create a snapshot: tenant " + tenantId + " has no active rules");
        }

        List<JsonNode> nodes = new ArrayList<>(payloads.size());
        for (String payload : payloads) {
            nodes.add(readTree(payload));
        }
        nodes.sort(Comparator.comparing(node -> node.path("rule_id").asText()));
        String canonicalJson = writeTree(nodes);
        String checksum = sha256Hex(canonicalJson);

        int seq = snapshotRepository.nextSeq(tenantId);
        String id = UUID.randomUUID().toString();
        snapshotRepository.insert(id, tenantId, seq, canonicalJson, checksum, nodes.size(), createdBy);
        log.info("rule snapshot created tenant={} seq={} snapshot_id={} rule_count={} checksum={}",
                tenantId, seq, id, nodes.size(), checksum);

        SnapshotRow row = snapshotRepository.findById(id)
                .orElseThrow(() -> new IllegalStateException("snapshot vanished after insert: " + id));
        return toView(row);
    }

    @Transactional(readOnly = true)
    public SnapshotView latest(String tenantId) {
        SnapshotRow row = snapshotRepository.latest(tenantId)
                .orElseThrow(() -> ApiException.notFound("SNAPSHOT_NOT_FOUND",
                        "no rule snapshot exists for tenant " + tenantId));
        return toView(row);
    }

    @Transactional(readOnly = true)
    public SnapshotView byId(String tenantId, String snapshotId) {
        SnapshotRow row = snapshotRepository.findById(snapshotId)
                .filter(r -> r.tenantId().equals(tenantId))
                .orElseThrow(() -> ApiException.notFound("SNAPSHOT_NOT_FOUND",
                        "snapshot not found: " + snapshotId));
        return toView(row);
    }

    private SnapshotView toView(SnapshotRow row) {
        return new SnapshotView(row.id(), row.tenantId(), row.seq(), row.ruleCount(),
                row.checksum(), row.createdAt(), readTree(row.payloadJson()));
    }

    private JsonNode readTree(String json) {
        try {
            return objectMapper.readTree(json);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("corrupted rule payload in database", e);
        }
    }

    private String writeTree(List<JsonNode> nodes) {
        try {
            return objectMapper.writeValueAsString(nodes);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("failed to canonicalise snapshot payload", e);
        }
    }

    static String sha256Hex(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required but unavailable", e);
        }
    }

    /** Exposed for tests to assert canonicalisation stability. */
    public static String canonicalChecksum(ObjectMapper mapper, List<String> payloads) {
        try {
            List<JsonNode> nodes = new ArrayList<>();
            for (String payload : payloads) {
                nodes.add(mapper.readTree(payload));
            }
            nodes.sort(Comparator.comparing(node -> node.path("rule_id").asText()));
            return sha256Hex(mapper.writeValueAsString(nodes));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    static LocalDateTime now() {
        return LocalDateTime.now();
    }
}
