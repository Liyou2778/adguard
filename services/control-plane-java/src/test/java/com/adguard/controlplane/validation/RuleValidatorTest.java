package com.adguard.controlplane.validation;

import com.adguard.controlplane.api.ApiException;
import com.adguard.controlplane.domain.RuleDefinition;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RuleValidatorTest {

    private final RuleValidator validator = new RuleValidator();

    private static RuleDefinition valid() {
        return new RuleDefinition(
                "ADV-LAW-ART9-3",
                "中华人民共和国广告法",
                "第九条",
                "（三）",
                "absolute_terms",
                "high",
                "不得使用“国家级”“最高级”“最佳”等用语",
                List.of("最好", "最佳", "最高级", "国家级", "第一", "顶级", "唯一"),
                new RuleDefinition.Detection(
                        List.of("regex:最[好佳优低高强]", "ac_automaton:absolute_terms"),
                        "判断该表述是否构成对商品性能的绝对化宣称",
                        List.of("有据可查的历史事实需附证明")),
                new RuleDefinition.Penalty("第五十七条", 200000, 1000000),
                LocalDate.of(2015, 9, 1),
                null,
                "https://www.gov.cn/guoqing/2015-04/25/content_2853642.htm");
    }

    @Test
    void acceptsValidRule() {
        assertThatCode(() -> validator.validate(valid())).doesNotThrowAnyException();
    }

    @Test
    void rejectsMissingRuleId() {
        RuleDefinition base = valid();
        RuleDefinition def = new RuleDefinition(null, base.law(), base.article(), base.item(),
                base.family(), base.severity(), base.text(), base.canonicalTerms(),
                base.detection(), base.penalty(), base.effectiveFrom(), base.effectiveTo(),
                base.sourceUrl());
        assertThatThrownBy(() -> validator.validate(def))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("rule_id is required");
    }

    @Test
    void rejectsBadFamilyAndSeverity() {
        RuleDefinition base = valid();
        RuleDefinition def = new RuleDefinition(base.ruleId(), base.law(), base.article(), base.item(),
                "Absolute Terms", "critical", base.text(), base.canonicalTerms(),
                base.detection(), base.penalty(), base.effectiveFrom(), base.effectiveTo(),
                base.sourceUrl());
        assertThatThrownBy(() -> validator.validate(def))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("family must match")
                .hasMessageContaining("severity must be one of");
    }

    @Test
    void rejectsEmptyCanonicalTermsAndInvertedPenalty() {
        RuleDefinition base = valid();
        RuleDefinition def = new RuleDefinition(base.ruleId(), base.law(), base.article(), base.item(),
                base.family(), base.severity(), base.text(), List.of(),
                base.detection(), new RuleDefinition.Penalty("第五十七条", 1000000, 200000),
                base.effectiveFrom(), base.effectiveTo(), base.sourceUrl());
        assertThatThrownBy(() -> validator.validate(def))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("canonical_terms must contain at least one term")
                .hasMessageContaining("penalty.min must be <= penalty.max");
    }

    @Test
    void rejectsEffectiveDateInversion() {
        RuleDefinition base = valid();
        RuleDefinition def = new RuleDefinition(base.ruleId(), base.law(), base.article(), base.item(),
                base.family(), base.severity(), base.text(), base.canonicalTerms(),
                base.detection(), base.penalty(), LocalDate.of(2026, 1, 1),
                LocalDate.of(2025, 1, 1), base.sourceUrl());
        assertThatThrownBy(() -> validator.validate(def))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("effective_from must be <= effective_to");
    }
}
