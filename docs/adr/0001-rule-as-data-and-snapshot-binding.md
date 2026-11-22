# ADR-0001：规则即数据，判定绑定不可变快照

- 状态：已接受（M1）
- 日期：2026-11-22
- 关联：PRD ADR-1 / ADR-3；P2-技术设计 §3.1、§6、§7

## 背景

法规条款在本项目里不是"一段文本"，而是**带判定逻辑的数据**：

- 一条规则包含：法条定位（law/article/item）、禁用词族（family）、判定条件（lexical / semantic / 豁免）、罚则区间、生效期。
- 市场监管规则高频变化，如果规则写进代码，每次新规都要发版、回归、重启——这正是 PRD 痛点 P-3（新规滞后窗口）。
- 判定结论必须可复现：半年后商家申诉或监管检查时，要能回答"当时是按哪一版规则判的"。

## 决策

### 1. 规则 = 结构化 JSON，存 DB，不进代码

规则体定义为 `rule_version.payload`（JSON 列），结构见 `RuleDefinition`：

```json
{
  "rule_id": "ADV-LAW-ART9-3",
  "law": "中华人民共和国广告法",
  "article": "第九条",
  "item": "（三）",
  "family": "absolute_terms",
  "severity": "high",
  "text": "不得使用“国家级”“最高级”“最佳”等用语",
  "canonical_terms": ["最好", "最佳", "最高级", "国家级", "第一", "顶级", "唯一"],
  "detection": {
    "lexical": ["regex:最[好佳优低高强]", "ac_automaton:absolute_terms"],
    "semantic": "判断该表述是否构成对商品性能的绝对化宣称",
    "context_exemptions": ["有据可查的历史事实需附证明"]
  },
  "penalty": {"law_ref": "第五十七条", "min": 200000, "max": 1000000},
  "effective_from": "2015-09-01"
}
```

新规上线 = 写一条 `rule_version` + 激活 + 生成快照，**零代码变更、零重启、不重建索引**。

### 2. 规则版本生命周期

```
POST /admin/rules            -> rule（current_version=1）+ rule_version(v1, draft)
PUT  /admin/rules/{id}       -> rule_version(vN+1, draft)，current_version=N+1
POST /admin/rules/{id}/activate
    -> rule_version(vN+1, active)、旧 active 置 retired、rule.status=active
    -> 生成 rule_snapshot（该租户全部 active 规则的不可变集合）
```

约束：

- `rule_id` 全局唯一（含租户维度），`(rule_id, version)` 唯一。
- 已激活版本不可修改；修改必须产生新版本（审计要求）。
- 每租户同一时刻只有 0 或 1 个 active 版本。

### 3. 快照是判定可复现的唯一凭据

`rule_snapshot.payload` = 该租户全部 active 规则的 JSON 数组；`checksum` = 其规范化 JSON 的 SHA-256。

规范化规则（**永久稳定，不得随意更改**）：

1. 按 `rule_id` 升序排列；
2. Jackson 序列化（snake_case、忽略 null）；
3. checksum = sha256(规范化 JSON) 的十六进制。

`judgment.rule_snapshot_id` 记录判定发生时绑定的快照；查询判定时可用 `GET /internal/rules/snapshot?snapshot_id=` 完整重放当时的规则集合。

### 4. 并发控制：租户行锁而非乐观锁

规则编辑与快照生成都先 `SELECT id FROM tenant WHERE id=? FOR UPDATE`。理由：

- 快照序号 `seq` 必须单调、无空洞地产生；用唯一索引 + 重试会在事务回滚后失效（MySQL 会把事务标记为 rollback-only）。
- 规则编辑是低频管理操作，租户级串行化没有吞吐问题。
- 等到规则编辑成为热点（多运营同时改）再换乐观锁，切换成本低。

## 后果

**正面**

- 新规生效 = 数据变更，满足 PRD 热更新 ≤5min 目标的前提。
- 判定可复现：快照不可变 + checksum 可校验。
- 规则校验集中在 `RuleValidator`，坏数据进不了库。

**负面 / 已知限制**

- 快照是全量快照（M1 简单可靠）；规则规模到万级时需要改为增量快照 + 合并视图（M3 评估）。
- `payload` 是 JSON 列，复杂条件查询需应用层过滤；M2 会把 `family`、`severity` 冗余成列（已建索引）。
- 快照 checksum 依赖 Jackson 序列化行为，升级 Jackson 大版本需固定 `ObjectMapper` 配置并加回归测试（已有 `SnapshotCanonicalizationTest`）。

## 验证

- `SnapshotCanonicalizationTest`：顺序无关、内容敏感、20 次稳定。
- `RuleFlowIT`（Testcontainers MySQL 8）：建规则 → 改版本 → 激活 → 快照 seq=1；激活第二条 → seq=2、count=2、checksum 变化；重复激活 → 409。
