package com.adguard.controlplane.domain;

import java.time.LocalDate;
import java.time.LocalDateTime;

/** Regulation document metadata row. */
public record RegulationRow(
        String id,
        String title,
        String sourceUrl,
        LocalDate effectiveFrom,
        String checksum,
        Integer charCount,
        LocalDateTime updatedAt) {
}
