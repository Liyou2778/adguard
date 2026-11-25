package com.adguard.controlplane.api;

import com.adguard.controlplane.security.AuthContext;
import com.adguard.controlplane.service.BillingService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.time.YearMonth;
import java.util.Map;

/** Usage / quota / billing APIs (M9 / FR-13). */
@RestController
public class BillingController {

    private final BillingService billingService;

    public BillingController(BillingService billingService) {
        this.billingService = billingService;
    }

    /** Merchant self-service usage (tenant from the authenticated token). */
    @GetMapping("/api/v1/usage")
    public Map<String, Object> myUsage(@RequestParam(required = false) String from,
                                       @RequestParam(required = false) String to) {
        return billingService.usage(AuthContext.requireTenant(), from, to);
    }

    @GetMapping("/admin/usage")
    public Map<String, Object> adminUsage(@RequestParam(required = false) String tenantId,
                                          @RequestParam(required = false) String from,
                                          @RequestParam(required = false) String to) {
        return billingService.adminUsage(tenantId, from, to);
    }

    @GetMapping("/admin/billing/summary")
    public Map<String, Object> summary(@RequestParam(required = false) String month) {
        return billingService.monthlySummary(month);
    }

    @GetMapping("/admin/billing/export.csv")
    public ResponseEntity<byte[]> exportCsv(@RequestParam(required = false) String month,
                                            @RequestParam(required = false) String tenantId) {
        String csv = billingService.exportCsv(month, tenantId);
        String filename = "adguard-billing-" + (month == null || month.isBlank() ? YearMonth.now() : month) + ".csv";
        return ResponseEntity.ok()
                .contentType(new MediaType("text", "csv", StandardCharsets.UTF_8))
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + filename + "\"")
                .body(csv.getBytes(StandardCharsets.UTF_8));
    }

    @GetMapping("/admin/billing/export.pdf")
    public ResponseEntity<byte[]> exportPdf(@RequestParam(required = false) String month,
                                            @RequestParam(required = false) String tenantId) {
        byte[] pdf = billingService.exportPdf(month, tenantId);
        String filename = "adguard-billing-" + (month == null || month.isBlank() ? YearMonth.now() : month) + ".pdf";
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_PDF)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + filename + "\"")
                .body(pdf);
    }
}
