package com.adguard.controlplane.service;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.RegulationImportRequest;
import com.adguard.controlplane.domain.RegulationRow;
import com.adguard.controlplane.domain.RuleDefinition;
import com.adguard.controlplane.repo.RegulationRepository;
import com.adguard.controlplane.repo.RuleRepository;
import com.adguard.controlplane.validation.RuleValidator;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RegulationImportServiceTest {

    private RegulationRepository regulationRepository;
    private RuleRepository ruleRepository;
    private ObjectMapper objectMapper;
    private RegulationImportService service;

    @BeforeEach
    void setUp() {
        regulationRepository = mock(RegulationRepository.class);
        ruleRepository = mock(RuleRepository.class);
        objectMapper = new ObjectMapper().registerModule(new JavaTimeModule());
        service = new RegulationImportService(regulationRepository, ruleRepository,
                new RuleValidator(), objectMapper);
    }

    private static RegulationImportRequest.ClauseDoc clause() {
        return new RegulationImportRequest.ClauseDoc(
                "ADV-LAW-ART9", "第九条", 9, "第二章 广告内容准则",
                "广告不得有下列情形：", 0, 10,
                List.of(new RegulationImportRequest.ItemDoc("（三）", 3,
                        "使用“国家级”、“最高级”、“最佳”等用语；", 20, 40)));
    }

    private static RegulationImportRequest.ParsedRule rule(String clauseId) {
        return new RegulationImportRequest.ParsedRule(
                "ADV-LAW-ART9-3", "absolute_terms", "high", "ADV-LAW", "第九条", "（三）", clauseId,
                "使用“国家级”、“最高级”、“最佳”等用语；",
                List.of("国家级", "最高级", "最佳", "最好"),
                "第五十七条", 200_000, 1_000_000, LocalDate.of(2015, 9, 1),
                List.of("仅表明服务态度的", "自我比较的"), "https://example.gov.cn/law",
                "中华人民共和国广告法");
    }

    private static RegulationImportRequest request(String clauseId) {
        return new RegulationImportRequest(
                List.of(new RegulationImportRequest.RegulationDoc(
                        "ADV-LAW", "中华人民共和国广告法", "https://example.gov.cn/law",
                        LocalDate.of(2015, 9, 1), 1000, List.of(clause()))),
                List.of(rule(clauseId)));
    }

    @Test
    void importsRegulationClausesAndRules() {
        when(regulationRepository.find("ADV-LAW")).thenReturn(Optional.empty());
        when(ruleRepository.exists("demo-platform", "ADV-LAW-ART9-3")).thenReturn(false);
        when(regulationRepository.findClause("ADV-LAW-ART9-ITEM3")).thenReturn(Optional.empty());

        var result = service.importRegulations("demo-platform", request("ADV-LAW-ART9-ITEM3"));

        assertThat(result.regulationsImported()).isEqualTo(1);
        assertThat(result.clausesWritten()).isEqualTo(2); // article + item
        assertThat(result.rulesCreated()).isEqualTo(1);
        assertThat(result.ruleIds()).containsExactly("ADV-LAW-ART9-3");

        ArgumentCaptor<RuleDefinition> definition = ArgumentCaptor.forClass(RuleDefinition.class);
        verify(ruleRepository).insertRule(eq("demo-platform"), definition.capture(),
                eq("ADV-LAW-ART9-ITEM3"), eq("ADV-LAW"));
        assertThat(definition.getValue().family()).isEqualTo("absolute_terms");
        assertThat(definition.getValue().penalty().min()).isEqualTo(200_000);
        assertThat(definition.getValue().detection().contextExemptions()).hasSize(2);
        assertThat(definition.getValue().canonicalTerms()).contains("国家级");

        verify(ruleRepository).insertVersion(eq("demo-platform"), eq("ADV-LAW-ART9-3"), eq(1),
                anyString(), eq("draft"), eq("clause-parser"));
        verify(ruleRepository).upsertTenant("demo-platform");
    }

    @Test
    void isIdempotentForUnchangedRegulationAndExistingRule() throws Exception {
        // same checksum as the service would compute for this document
        List<RegulationImportRequest.ClauseDoc> clauses = List.of(clause());
        String checksum = RegulationImportService.sha256(objectMapper.writeValueAsString(clauses));
        when(regulationRepository.find("ADV-LAW")).thenReturn(Optional.of(new RegulationRow(
                "ADV-LAW", "中华人民共和国广告法", "https://example.gov.cn/law",
                LocalDate.of(2015, 9, 1), checksum, 1000, null)));
        when(ruleRepository.exists("demo-platform", "ADV-LAW-ART9-3")).thenReturn(true);

        var result = service.importRegulations("demo-platform", request("ADV-LAW-ART9-ITEM3"));

        assertThat(result.regulationsUnchanged()).isEqualTo(1);
        assertThat(result.clausesWritten()).isZero();
        assertThat(result.rulesSkipped()).isEqualTo(1);
        assertThat(result.rulesCreated()).isZero();
        verify(regulationRepository, never()).upsert(any());
        verify(regulationRepository, never()).deleteClauses(anyString());
        verify(ruleRepository, never()).insertRule(anyString(), any(), any(), any());
    }

    @Test
    void rejectsUnknownClauseReference() {
        when(regulationRepository.find("ADV-LAW")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.importRegulations("demo-platform", request("ADV-LAW-ART9-ITEM9")))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("unknown clause");
        verify(ruleRepository, never()).insertRule(anyString(), any(), any(), any());
    }

    @Test
    void rejectsInvalidRegulationId() {
        RegulationImportRequest bad = new RegulationImportRequest(
                List.of(new RegulationImportRequest.RegulationDoc(
                        "adv-law", "x", null, null, 1, List.of(clause()))), List.of());
        assertThatThrownBy(() -> service.importRegulations("demo-platform", bad))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("regulation id");
    }

    @Test
    void rejectsDuplicateClauseIds() {
        RegulationImportRequest duplicate = new RegulationImportRequest(
                List.of(new RegulationImportRequest.RegulationDoc(
                        "ADV-LAW", "中华人民共和国广告法", null, null, 1,
                        List.of(clause(), clause()))), List.of());
        assertThatThrownBy(() -> service.importRegulations("demo-platform", duplicate))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("duplicate clause id");
    }
}
