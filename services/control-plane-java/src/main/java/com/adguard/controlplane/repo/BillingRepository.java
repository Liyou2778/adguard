package com.adguard.controlplane.repo;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/** Usage ledger and price list (M9 / FR-13 billing). */
@Repository
public class BillingRepository {

    public record PriceRow(String code, String service, String assetType, long unitPriceMicro, int quotaWeight) {
    }

    private final JdbcTemplate jdbc;

    public BillingRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<PriceRow> listPrices() {
        return jdbc.query("SELECT code, service, asset_type, unit_price_micro, quota_weight "
                        + "FROM billing_price ORDER BY code",
                (rs, rowNum) -> new PriceRow(rs.getString("code"), rs.getString("service"),
                        rs.getString("asset_type"), rs.getLong("unit_price_micro"), rs.getInt("quota_weight")));
    }

    /** Daily aggregates for one tenant or for all tenants (tenantId null). */
    public List<Map<String, Object>> dailyUsage(String tenantId, LocalDate from, LocalDate to) {
        StringBuilder sql = new StringBuilder(
                "SELECT tenant_id, billed_date, asset_type, COUNT(*) AS calls, "
                        + "COALESCE(SUM(weight),0) AS weight, COALESCE(SUM(cost_micro),0) AS cost_micro "
                        + "FROM usage_record WHERE billed_date BETWEEN ? AND ?");
        Object[] baseArgs = new Object[]{from, to};
        if (tenantId == null || tenantId.isBlank()) {
            sql.append(" GROUP BY tenant_id, billed_date, asset_type ORDER BY tenant_id, billed_date, asset_type");
            return jdbc.queryForList(sql.toString(), baseArgs);
        }
        sql.append(" AND tenant_id = ? GROUP BY tenant_id, billed_date, asset_type "
                + "ORDER BY billed_date, asset_type");
        return jdbc.queryForList(sql.toString(), from, to, tenantId);
    }

    public List<Map<String, Object>> monthlyTotals(LocalDate from, LocalDate to) {
        return jdbc.queryForList("SELECT tenant_id, COUNT(*) AS calls, COALESCE(SUM(weight),0) AS weight, "
                        + "COALESCE(SUM(cost_micro),0) AS cost_micro FROM usage_record "
                        + "WHERE billed_date BETWEEN ? AND ? GROUP BY tenant_id ORDER BY cost_micro DESC",
                from, to);
    }

    public List<Map<String, Object>> tenantMonthDetail(String tenantId, LocalDate from, LocalDate to) {
        return jdbc.queryForList("SELECT billed_date, asset_type, COUNT(*) AS calls, "
                        + "COALESCE(SUM(weight),0) AS weight, COALESCE(SUM(cost_micro),0) AS cost_micro "
                        + "FROM usage_record WHERE tenant_id = ? AND billed_date BETWEEN ? AND ? "
                        + "GROUP BY billed_date, asset_type ORDER BY billed_date, asset_type",
                tenantId, from, to);
    }

    public long totalCostMicro(LocalDate from, LocalDate to) {
        Long total = jdbc.queryForObject("SELECT COALESCE(SUM(cost_micro),0) FROM usage_record "
                + "WHERE billed_date BETWEEN ? AND ?", Long.class, from, to);
        return total == null ? 0 : total;
    }
}
