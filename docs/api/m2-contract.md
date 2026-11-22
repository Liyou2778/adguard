# M2 接口契约（v0.2）：归一化服务 + 法规导入

> 在 M1 契约（[m1-contract.md](m1-contract.md)）基础上的增量。
> Python 归一化服务默认 `http://127.0.0.1:8090`。

## 1. 归一化/匹配服务（Python，FastAPI）

### 1.1 健康与版本

`GET /healthz` → `200 {"status":"ok","matcher_version":"dict:absolute_terms:v1;false_advertising:v1;patterns:..."}`

`GET /v1/version` → `200`

```json
{
  "pipeline_version": "0.2.0",
  "matcher_version": "dict:absolute_terms:v1;false_advertising:v1;patterns:214",
  "pattern_count": 214,
  "families": [{"family": "absolute_terms", "version": 1, "terms": 30},
               {"family": "false_advertising", "version": 1, "terms": 5}]
}
```

### 1.2 归一化

`POST /v1/normalize`

```json
{"text": "本店最\u200b😀佳，全网最低"}
```

```json
{
  "text": "本店最\u200b😀佳，全网最低",
  "normalized_text": "本店最佳,全网最低",
  "compact": "",
  "steps": ["nfkc+nfkd", "strip-zero-width/control/emoji", "t2s", "variant-chars",
            "component-sequences", "lowercase"]
}
```

### 1.3 匹配（含条款级 span）

`POST /v1/match`

```json
{"text": "本店最\u200b😀佳，欢迎选购", "tenant_id": "demo-platform"}
```

```json
{
  "hit": true,
  "normalized_text": "本店最佳,欢迎选购",
  "matcher_version": "dict:absolute_terms:v1;false_advertising:v1;patterns:214",
  "detections": [
    {
      "family": "absolute_terms",
      "canonical": "最佳",
      "variant_kind": "canonical",
      "span": [2, 8],
      "evidence": "最\u200b😀佳",
      "matched_normalized": "最佳"
    }
  ]
}
```

- `span` 是**原文**码点区间（Python `str` 索引，左闭右开）；`evidence` = 原文该区间；
- `variant_kind`：`canonical|variant|homophone|pinyin|synonym`，用于审计"是靠哪种归一化命中的"；
- 错误：`422`（字段缺失/空文本/超长 >100000 字符）。

## 2. 法规导入（Java 控制面）

### 2.1 导入解析结果（幂等）

`POST /admin/regulations/import` + `X-Tenant-Id`

请求体 = `adguard_pipeline.clause_parser` 的输出：

```json
{
  "regulations": [
    {
      "id": "ADV-LAW",
      "title": "中华人民共和国广告法",
      "source_url": "https://...",
      "effective_from": "2015-09-01",
      "char_count": 1932,
      "clauses": [
        {
          "clause_id": "ADV-LAW-ART9",
          "article": "第九条",
          "article_no": 9,
          "chapter": "第二章 广告内容准则",
          "text": "广告不得有下列情形：...",
          "char_start": 100,
          "char_end": 500,
          "items": [
            {"item": "（三）", "index": 3, "text": "使用“国家级”...等用语；",
             "char_start": 160, "char_end": 190}
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
      "canonical_terms": ["国家级", "最高级", "最佳", "最好", "..."],
      "penalty_law_ref": "第五十七条",
      "penalty_min": 200000,
      "penalty_max": 1000000,
      "effective_from": "2015-09-01",
      "context_exemptions": ["仅表明商品经营者的服务态度或者经营理念...", "..."],
      "source_url": "https://...",
      "law": "中华人民共和国广告法"
    }
  ]
}
```

响应 `201`：

```json
{
  "regulations_imported": 2,
  "regulations_updated": 0,
  "regulations_unchanged": 0,
  "clauses_written": 33,
  "rules_created": 2,
  "rules_skipped": 0,
  "rule_ids": ["ADV-LAW-ART9-3", "ADV-LAW-ART28"]
}
```

- 再次导入相同内容：`regulations_unchanged=2、rules_skipped=2、clauses_written=0`；
- 条款树：article 级与 item 级都写入 `regulation_clause`（item 的 `parent_clause_id` 指向 article）；
- 规则以 **draft** 版本写入，仍需 `POST /admin/rules/{id}/activate` 才生效并进入快照。

错误码：

| HTTP | code | 说明 |
|---|---|---|
| 400 | `INVALID_IMPORT` `INVALID_REGULATION_ID` `INVALID_REGULATION` `INVALID_CLAUSE` `DUPLICATE_CLAUSE` `UNKNOWN_CLAUSE` | 载荷不合格 / 规则引用了不存在的条款 |

### 2.2 法规与条款查询

- `GET /admin/regulations` → 法规列表（含 checksum、生效日期、字数）
- `GET /admin/regulations/{regulationId}/clauses` → 条款列表（article + item，含 char_start/char_end、checksum）
- `GET /admin/rules` 的每条规则新增字段 `clause_id`（指向最细条款，如 `ADV-LAW-ART9-ITEM3`）

## 3. 与 M1 契约的兼容性

- M1 的所有接口/字段不变；`rule` 表新增 `clause_id`、`source_regulation_id` 两列（响应里新增可选字段）；
- M1 冒烟脚本 `scripts/smoke-m1.ps1` 在 M2 变更后重跑仍 29/29 通过（回归证据见 M2 验收报告）。
