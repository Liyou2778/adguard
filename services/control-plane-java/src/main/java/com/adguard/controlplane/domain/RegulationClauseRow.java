package com.adguard.controlplane.domain;

import java.time.LocalDateTime;

/** One clause (article-level or item-level) of a regulation document. */
public record RegulationClauseRow(
        String clauseId,
        String regulationId,
        String article,
        int articleNo,
        String chapter,
        String level,
        String parentClauseId,
        String itemLabel,
        String text,
        Integer charStart,
        Integer charEnd,
        String checksum,
        LocalDateTime createdAt) {
}
