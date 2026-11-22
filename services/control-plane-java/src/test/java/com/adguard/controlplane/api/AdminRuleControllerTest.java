package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.RuleDefinition;
import com.adguard.controlplane.domain.RuleDetail;
import com.adguard.controlplane.domain.RuleSummary;
import com.adguard.controlplane.domain.RuleVersionView;
import com.adguard.controlplane.service.RuleService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(AdminRuleController.class)
class AdminRuleControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private RuleService ruleService;

    private static final String VALID_RULE_JSON = """
            {
              "rule_id": "ADV-LAW-ART9-3",
              "law": "中华人民共和国广告法",
              "article": "第九条",
              "item": "（三）",
              "family": "absolute_terms",
              "severity": "high",
              "text": "不得使用国家级、最高级、最佳等用语",
              "canonical_terms": ["最好", "最佳"],
              "detection": {"lexical": ["regex:最[好佳]"], "semantic": "判断是否为绝对化宣称"},
              "penalty": {"law_ref": "第五十七条", "min": 200000, "max": 1000000},
              "effective_from": "2015-09-01"
            }
            """;

    private static RuleDetail detail() {
        RuleDefinition def = new RuleDefinition("ADV-LAW-ART9-3", "广告法", "第九条", "（三）",
                "absolute_terms", "high", "text", List.of("最好"), null, null,
                LocalDate.of(2015, 9, 1), null, null);
        RuleSummary summary = new RuleSummary("ADV-LAW-ART9-3", "demo-platform", "广告法", "第九条",
                "（三）", "absolute_terms", "high", "draft", 1, null, LocalDateTime.now());
        RuleVersionView version = new RuleVersionView(1, "draft", "api", LocalDateTime.now(), def);
        return new RuleDetail(summary, List.of(version));
    }

    @Test
    void createReturns201WithDraftVersion() throws Exception {
        when(ruleService.create(eq("demo-platform"), any())).thenReturn(detail());

        mockMvc.perform(post("/admin/rules")
                        .header("X-Tenant-Id", "demo-platform")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(VALID_RULE_JSON))
                .andExpect(status().isCreated())
                .andExpect(header().exists("X-Request-Id"))
                .andExpect(jsonPath("$.summary.rule_id").value("ADV-LAW-ART9-3"))
                .andExpect(jsonPath("$.summary.status").value("draft"))
                .andExpect(jsonPath("$.versions[0].version").value(1));
    }

    @Test
    void missingTenantHeaderReturns400() throws Exception {
        mockMvc.perform(post("/admin/rules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(VALID_RULE_JSON))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("MISSING_HEADER"));
    }

    @Test
    void invalidTenantHeaderReturns400() throws Exception {
        mockMvc.perform(post("/admin/rules")
                        .header("X-Tenant-Id", "Demo Platform!")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(VALID_RULE_JSON))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("INVALID_TENANT"));
    }

    @Test
    void conflictMapsTo409() throws Exception {
        when(ruleService.create(eq("demo-platform"), any()))
                .thenThrow(ApiException.conflict("RULE_EXISTS", "rule already exists"));

        mockMvc.perform(post("/admin/rules")
                        .header("X-Tenant-Id", "demo-platform")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(VALID_RULE_JSON))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("RULE_EXISTS"));
    }

    @Test
    void listReturnsRules() throws Exception {
        when(ruleService.list(eq("demo-platform"), any(), any(), any(Integer.class)))
                .thenReturn(List.of(detail().summary()));

        mockMvc.perform(get("/admin/rules").header("X-Tenant-Id", "demo-platform"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].rule_id").value("ADV-LAW-ART9-3"));
    }
}
