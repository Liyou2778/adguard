package com.adguard.controlplane.repo;

import com.adguard.controlplane.domain.RegulationClauseRow;
import com.adguard.controlplane.domain.RegulationRow;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

/** Persistence for regulation documents and their clause tree (M2). */
@Repository
public class RegulationRepository {

    private static final RowMapper<RegulationRow> REGULATION_MAPPER = (rs, i) -> new RegulationRow(
            rs.getString("id"),
            rs.getString("title"),
            rs.getString("source_url"),
            rs.getDate("effective_from") == null ? null : rs.getDate("effective_from").toLocalDate(),
            rs.getString("checksum"),
            nullableInt(rs.getObject("char_count")),
            toLocalDateTime(rs.getTimestamp("updated_at")));

    private static final RowMapper<RegulationClauseRow> CLAUSE_MAPPER = (rs, i) -> new RegulationClauseRow(
            rs.getString("clause_id"),
            rs.getString("regulation_id"),
            rs.getString("article"),
            rs.getInt("article_no"),
            rs.getString("chapter"),
            rs.getString("level"),
            rs.getString("parent_clause_id"),
            rs.getString("item_label"),
            rs.getString("text"),
            nullableInt(rs.getObject("char_start")),
            nullableInt(rs.getObject("char_end")),
            rs.getString("checksum"),
            toLocalDateTime(rs.getTimestamp("created_at")));

    private final JdbcTemplate jdbc;

    public RegulationRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<RegulationRow> find(String regulationId) {
        List<RegulationRow> rows = jdbc.query(
                "SELECT * FROM regulation WHERE id = ?", REGULATION_MAPPER, regulationId);
        return rows.stream().findFirst();
    }

    public List<RegulationRow> list() {
        return jdbc.query("SELECT * FROM regulation ORDER BY id", REGULATION_MAPPER);
    }

    public void upsert(RegulationRow row) {
        jdbc.update("""
                        INSERT INTO regulation (id, title, source_url, effective_from, checksum, char_count)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON DUPLICATE KEY UPDATE title = VALUES(title), source_url = VALUES(source_url),
                                                effective_from = VALUES(effective_from),
                                                checksum = VALUES(checksum), char_count = VALUES(char_count)
                        """,
                row.id(), row.title(), row.sourceUrl(),
                row.effectiveFrom() == null ? null : Date.valueOf(row.effectiveFrom()),
                row.checksum(), row.charCount());
    }

    public void deleteClauses(String regulationId) {
        jdbc.update("DELETE FROM regulation_clause WHERE regulation_id = ?", regulationId);
    }

    public void insertClause(RegulationClauseRow row) {
        jdbc.update("""
                        INSERT INTO regulation_clause
                        (clause_id, regulation_id, article, article_no, chapter, level, parent_clause_id,
                         item_label, text, char_start, char_end, checksum)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                row.clauseId(), row.regulationId(), row.article(), row.articleNo(), row.chapter(),
                row.level(), row.parentClauseId(), row.itemLabel(), row.text(),
                row.charStart(), row.charEnd(), row.checksum());
    }

    public List<RegulationClauseRow> clauses(String regulationId) {
        return jdbc.query("""
                        SELECT * FROM regulation_clause WHERE regulation_id = ?
                        ORDER BY article_no, level DESC, clause_id
                        """,
                CLAUSE_MAPPER, regulationId);
    }

    public Optional<RegulationClauseRow> findClause(String clauseId) {
        List<RegulationClauseRow> rows = jdbc.query(
                "SELECT * FROM regulation_clause WHERE clause_id = ?", CLAUSE_MAPPER, clauseId);
        return rows.stream().findFirst();
    }

    private static Integer nullableInt(Object value) {
        return value == null ? null : ((Number) value).intValue();
    }

    private static LocalDateTime toLocalDateTime(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toLocalDateTime();
    }
}
