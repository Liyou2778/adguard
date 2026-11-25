package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.repo.BillingRepository;
import com.adguard.controlplane.repo.TenantRepository;
import com.lowagie.text.Document;
import com.lowagie.text.Element;
import com.lowagie.text.Font;
import com.lowagie.text.PageSize;
import com.lowagie.text.Paragraph;
import com.lowagie.text.Phrase;
import com.lowagie.text.pdf.BaseFont;
import com.lowagie.text.pdf.PdfPCell;
import com.lowagie.text.pdf.PdfPTable;
import com.lowagie.text.pdf.PdfWriter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Usage aggregation, CSV and Chinese PDF bills (M9 / FR-13). */
@Service
public class BillingService {

    private static final Logger log = LoggerFactory.getLogger(BillingService.class);
    private static final long MICRO_PER_CNY = 1_000_000L;

    private final BillingRepository billing;
    private final TenantRepository tenants;

    public BillingService(BillingRepository billing, TenantRepository tenants) {
        this.billing = billing;
        this.tenants = tenants;
    }

    public Map<String, Object> usage(String tenantId, String fromText, String toText) {
        LocalDate to = toText == null || toText.isBlank() ? LocalDate.now() : LocalDate.parse(toText);
        LocalDate from = fromText == null || fromText.isBlank() ? to.minusDays(29) : LocalDate.parse(fromText);
        List<Map<String, Object>> rows = billing.dailyUsage(tenantId, from, to);
        long calls = 0;
        long weight = 0;
        long cost = 0;
        for (Map<String, Object> row : rows) {
            calls += number(row.get("calls"));
            weight += number(row.get("weight"));
            cost += number(row.get("cost_micro"));
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("tenant_id", tenantId);
        result.put("from", from.toString());
        result.put("to", to.toString());
        result.put("calls", calls);
        result.put("weight", weight);
        result.put("cost_micro", cost);
        result.put("cost_cny", cny(cost));
        result.put("items", rows);
        return result;
    }

    public Map<String, Object> adminUsage(String tenantFilter, String fromText, String toText) {
        if (tenantFilter != null && !tenantFilter.isBlank()) {
            return usage(tenantFilter, fromText, toText);
        }
        Map<String, Object> result = usage(null, fromText, toText);
        result.put("items", billing.dailyUsage(null, LocalDate.parse(String.valueOf(result.get("from"))),
                LocalDate.parse(String.valueOf(result.get("to")))));
        return result;
    }

    public Map<String, Object> monthlySummary(String monthText) {
        YearMonth month = parseMonth(monthText);
        LocalDate from = month.atDay(1);
        LocalDate to = month.atEndOfMonth();
        List<Map<String, Object>> totals = new ArrayList<>();
        long totalCost = 0;
        for (Map<String, Object> row : billing.monthlyTotals(from, to)) {
            String tenantId = String.valueOf(row.get("tenant_id"));
            Map<String, Object> item = new LinkedHashMap<>(row);
            item.put("tenant_name", tenantName(tenantId));
            totalCost += number(row.get("cost_micro"));
            totals.add(item);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("month", month.toString());
        result.put("currency", "CNY");
        result.put("total_cost_micro", totalCost);
        result.put("total_cost_cny", cny(totalCost));
        result.put("prices", billing.listPrices());
        result.put("tenants", totals);
        return result;
    }

    /** UTF-8 BOM + RFC4180-ish quoting, so Excel opens Chinese correctly. */
    public String exportCsv(String monthText, String tenantFilter) {
        YearMonth month = parseMonth(monthText);
        LocalDate from = month.atDay(1);
        LocalDate to = month.atEndOfMonth();
        List<Map<String, Object>> rows = billing.dailyUsage(
                tenantFilter == null || tenantFilter.isBlank() ? null : tenantFilter, from, to);
        StringBuilder csv = new StringBuilder("\uFEFF");
        csv.append("tenant_id,tenant_name,date,asset_type,calls,weight,cost_micro,cost_cny\n");
        for (Map<String, Object> row : rows) {
            String tenantId = String.valueOf(row.get("tenant_id"));
            csv.append(quote(tenantId)).append(',')
                    .append(quote(tenantName(tenantId))).append(',')
                    .append(quote(String.valueOf(row.get("billed_date")))).append(',')
                    .append(quote(String.valueOf(row.get("asset_type")))).append(',')
                    .append(number(row.get("calls"))).append(',')
                    .append(number(row.get("weight"))).append(',')
                    .append(number(row.get("cost_micro"))).append(',')
                    .append(cny(number(row.get("cost_micro")))).append('\n');
        }
        return csv.toString();
    }

    public byte[] exportPdf(String monthText, String tenantFilter) {
        YearMonth month = parseMonth(monthText);
        LocalDate from = month.atDay(1);
        LocalDate to = month.atEndOfMonth();
        List<Map<String, Object>> rows = billing.dailyUsage(
                tenantFilter == null || tenantFilter.isBlank() ? null : tenantFilter, from, to);
        String titleTenant = tenantFilter == null || tenantFilter.isBlank()
                ? "全部租户" : tenantName(tenantFilter) + "（" + tenantFilter + "）";

        BaseFont baseFont = resolveFont();
        Font titleFont = new Font(baseFont, 18, Font.BOLD);
        Font headFont = new Font(baseFont, 11, Font.BOLD);
        Font bodyFont = new Font(baseFont, 10);
        Font smallFont = new Font(baseFont, 9, Font.NORMAL);

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        Document document = new Document(PageSize.A4, 36, 36, 48, 36);
        try {
            PdfWriter.getInstance(document, out);
            document.open();
            Paragraph title = new Paragraph("AdGuard 合规审核服务账单", titleFont);
            title.setAlignment(Element.ALIGN_CENTER);
            document.add(title);
            document.add(new Paragraph("账期：" + month + "　　租户：" + titleTenant, headFont));
            document.add(new Paragraph("币种：人民币（CNY）　单价见价目表（微元=1/1,000,000 元）", smallFont));
            document.add(new Paragraph(" ", smallFont));

            PdfPTable table = new PdfPTable(new float[]{1.6f, 2.2f, 1.2f, 1.4f, 1.4f, 1.6f});
            table.setWidthPercentage(100);
            addHeader(table, headFont, "日期", "租户", "类型", "调用次数", "配额点数", "金额(元)");
            long totalCalls = 0;
            long totalWeight = 0;
            long totalCost = 0;
            for (Map<String, Object> row : rows) {
                long calls = number(row.get("calls"));
                long weight = number(row.get("weight"));
                long cost = number(row.get("cost_micro"));
                totalCalls += calls;
                totalWeight += weight;
                totalCost += cost;
                addCell(table, bodyFont, String.valueOf(row.get("billed_date")));
                addCell(table, bodyFont, String.valueOf(row.get("tenant_id")));
                addCell(table, bodyFont, String.valueOf(row.get("asset_type")));
                addCell(table, bodyFont, String.valueOf(calls));
                addCell(table, bodyFont, String.valueOf(weight));
                addCell(table, bodyFont, cny(cost));
            }
            document.add(table);
            document.add(new Paragraph(" ", smallFont));
            Paragraph summary = new Paragraph(
                    "合计：" + totalCalls + " 次判定　" + totalWeight + " 配额点数　"
                            + cny(totalCost) + " 元", headFont);
            document.add(summary);
            document.add(new Paragraph("说明：金额为系统内计费口径，仅用于内部成本归集与对账；"
                    + "实际结算以商务合同为准。", smallFont));
            document.add(new Paragraph("生成时间：" + java.time.OffsetDateTime.now(), smallFont));
            document.close();
        } catch (Exception e) {
            log.error("failed to render billing PDF", e);
            throw ApiException.serviceUnavailable("PDF_RENDER_FAILED", "failed to render PDF: " + e.getMessage());
        }
        return out.toByteArray();
    }

    private BaseFont resolveFont() {
        List<String> candidates = new ArrayList<>();
        String pdfFont = System.getenv("ADGUARD_PDF_FONT");
        if (pdfFont != null && !pdfFont.isBlank()) {
            candidates.add(pdfFont);
        }
        candidates.add("C:/Windows/Fonts/msyh.ttc");
        candidates.add("C:/Windows/Fonts/simhei.ttf");
        candidates.add("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc");
        candidates.add("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc");
        candidates.add("/usr/share/fonts/truetype/arphic/uming.ttc");
        for (String candidate : candidates) {
            java.io.File file = new java.io.File(candidate);
            if (!file.exists()) {
                continue;
            }
            String name = candidate.endsWith(".ttc") ? candidate + ",0" : candidate;
            try {
                return BaseFont.createFont(name, BaseFont.IDENTITY_H, BaseFont.EMBEDDED);
            } catch (Exception e) {
                log.debug("font candidate failed: {}", candidate, e);
            }
        }
        throw ApiException.serviceUnavailable("PDF_FONT_MISSING",
                "no CJK TrueType font found; set ADGUARD_PDF_FONT or install fonts-wqy-zenhei");
    }

    private void addHeader(PdfPTable table, Font font, String... values) {
        for (String value : values) {
            PdfPCell cell = new PdfPCell(new Phrase(value, font));
            cell.setHorizontalAlignment(Element.ALIGN_CENTER);
            cell.setPadding(4);
            table.addCell(cell);
        }
    }

    private void addCell(PdfPTable table, Font font, String value) {
        PdfPCell cell = new PdfPCell(new Phrase(value, font));
        cell.setPadding(3);
        table.addCell(cell);
    }

    private String tenantName(String tenantId) {
        return tenants.findById(tenantId).map(TenantRepository.TenantRow::name).orElse(tenantId);
    }

    private static YearMonth parseMonth(String monthText) {
        if (monthText == null || monthText.isBlank()) {
            return YearMonth.now();
        }
        try {
            return YearMonth.parse(monthText);
        } catch (Exception e) {
            throw ApiException.badRequest("INVALID_MONTH", "month must be YYYY-MM");
        }
    }

    private static long number(Object value) {
        if (value == null) {
            return 0;
        }
        if (value instanceof Number number) {
            return number.longValue();
        }
        return Long.parseLong(String.valueOf(value));
    }

    private static String cny(long micro) {
        return BigDecimal.valueOf(micro).divide(BigDecimal.valueOf(MICRO_PER_CNY))
                .setScale(4, java.math.RoundingMode.HALF_UP).toPlainString();
    }

    private static String quote(String value) {
        String text = value == null ? "" : value;
        if (text.contains(",") || text.contains("\"") || text.contains("\n")) {
            return "\"" + text.replace("\"", "\"\"") + "\"";
        }
        return text;
    }
}
