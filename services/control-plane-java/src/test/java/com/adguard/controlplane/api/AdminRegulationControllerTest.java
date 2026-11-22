package com.adguard.controlplane.api;

import com.adguard.controlplane.domain.RegulationClauseRow;
import com.adguard.controlplane.domain.RegulationImportRequest;
import com.adguard.controlplane.domain.RegulationRow;
import com.adguard.controlplane.repo.RegulationRepository;
import com.adguard.controlplane.service.RegulationImportService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(AdminRegulationController.class)
class AdminRegulationControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private RegulationImportService importService;

    @MockitoBean
    private RegulationRepository regulationRepository;

    private static final String IMPORT_JSON = """
            {
              "regulations": [
                {
                  "id": "ADV-LAW",
                  "title": "中华人民共和国广告法",
                  "source_url": "https://example.gov.cn/law",
                  "effective_from": "2015-09-01",
                  "char_count": 1000,
                  "clauses": [
                    {
                      "clause_id": "ADV-LAW-ART9",
                      "article": "第九条",
                      "article_no": 9,
                      "chapter": "第二章 广告内容准则",
                      "text": "广告不得有下列情形：",
                      "char_start": 0,
                      "char_end": 10,
                      "items": [
                        {"item": "（三）", "index": 3, "text": "使用“国家级”、“最高级”、“最佳”等用语；",
                         "char_start": 20, "char_end": 40}
                      ]
                    }
                  ]
                }
              ],
              "rules": [
                {
                  "rule_id": "ADV-LAW-ART9-3",
                  "family": "absolute_terms",
                  "severity": "high",
                  "regulation_id": "ADV-LAW",
                  "article": "第九条",
                  "item": "（三）",
                  "clause_id": "ADV-LAW-ART9-ITEM3",
                  "text": "使用“国家级”、“最高级”、“最佳”等用语；",
                  "canonical_terms": ["国家级", "最高级", "最佳"],
                  "penalty_law_ref": "第五十七条",
                  "penalty_min": 200000,
                  "penalty_max": 1000000,
                  "effective_from": "2015-09-01",
                  "context_exemptions": ["仅表明服务态度的"],
                  "source_url": "https://example.gov.cn/law",
                  "law": "中华人民共和国广告法"
                }
              ]
            }
            """;

    @Test
    void importReturnsSummary() throws Exception {
        when(importService.importRegulations(eq("demo-platform"), any())).thenReturn(
                new RegulationImportService.ImportResult(1, 0, 0, 2, 1, 0, List.of("ADV-LAW-ART9-3")));

        mockMvc.perform(post("/admin/regulations/import")
                        .header("X-Tenant-Id", "demo-platform")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(IMPORT_JSON))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.regulations_imported").value(1))
                .andExpect(jsonPath("$.clauses_written").value(2))
                .andExpect(jsonPath("$.rules_created").value(1))
                .andExpect(jsonPath("$.rule_ids[0]").value("ADV-LAW-ART9-3"));
    }

    @Test
    void importRequiresTenantHeader() throws Exception {
        mockMvc.perform(post("/admin/regulations/import")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(IMPORT_JSON))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("MISSING_HEADER"));
    }

    @Test
    void listRegulations() throws Exception {
        when(regulationRepository.list()).thenReturn(List.of(new RegulationRow(
                "ADV-LAW", "中华人民共和国广告法", "https://example.gov.cn/law",
                LocalDate.of(2015, 9, 1), "abc", 1000, LocalDateTime.now())));

        mockMvc.perform(get("/admin/regulations"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value("ADV-LAW"))
                .andExpect(jsonPath("$[0].title").value("中华人民共和国广告法"));
    }

    @Test
    void clausesByRegulation() throws Exception {
        when(regulationRepository.find("ADV-LAW")).thenReturn(Optional.of(new RegulationRow(
                "ADV-LAW", "中华人民共和国广告法", null, null, "abc", 1000, LocalDateTime.now())));
        when(regulationRepository.clauses("ADV-LAW")).thenReturn(List.of(new RegulationClauseRow(
                "ADV-LAW-ART9", "ADV-LAW", "第九条", 9, "第二章", "article", null, null,
                "广告不得有下列情形：", 0, 10, "abc", LocalDateTime.now())));

        mockMvc.perform(get("/admin/regulations/ADV-LAW/clauses"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].clause_id").value("ADV-LAW-ART9"))
                .andExpect(jsonPath("$[0].article_no").value(9));
    }

    @Test
    void clausesUnknownRegulationReturns404() throws Exception {
        when(regulationRepository.find("UNKNOWN")).thenReturn(Optional.empty());

        mockMvc.perform(get("/admin/regulations/UNKNOWN/clauses"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("REGULATION_NOT_FOUND"));
    }
}
