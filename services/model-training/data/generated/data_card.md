# M4 数据卡（m4-data-v1）

> 生成时间：2026-11-22 22:55:09；teacher：M2 matcher + rule catalogue + exemption hint (deterministic)
> 划分协议：
> - positives: term fold sha256(term)%4 == 0 -> eval; held-out case -> case-shangcheng-2024
> - negatives: withheld compliant templates + 2 held-out exemption examples
> - leakage_check: zero text overlap between eval and train/val (build fails otherwise)

| 集合 | 行数 | block | pass | sha256 |
|---|---:|---:|---:|---|
| sft_train | 1242 | 822 | 420 | e1fb53480e23… |
| sft_val | 137 | 91 | 46 | b67371d6f14a… |
| eval | 535 | 372 | 163 | 933ec65b368d… |
| semantic_unknown | 32 | 32 | 0 | 77f19f2421e0… |
| dpo_pairs | 1242 | - | - | 4715ae217190… |

## 公开处罚案例种子覆盖

| 案例 | 覆盖短语 | 来源 |
|---|---:|---|
| case-fanglinfu-2016 | 2/3 | 钱江晚报/央广网 2016-03；杭州市西湖区市场监督管理局对某炒货店作出罚款20万元的处罚决定 |
| case-yisui-2024 | 1/2 | 市场监管总局十起民生领域违法广告典型案例（2024-11）：河南一穗玉米网络科技有限公司，罚款80万元 |
| case-wanya-2024 | 0/1 | 南通市崇川区市场监督管理局 2024-06：某课外培训中心使用“全国首家”广告语 |
| case-shangcheng-2024 | 1/1 | 杭州市上城区市场监督管理局 2024-12：网络餐饮店商品标题使用“肉丝拌川（杭城唯一正宗）” |
| case-samr-top10-2024 | 2/3 | 市场监管总局十起民生领域违法广告典型案例（2024-11）：医疗美容、教育培训等领域使用“亚洲顶尖”“顶级机构”“最专业”等 |

**词典缺口（规则教师无法覆盖，记录为规则升级项）**：
- `最优`（案例 case-fanglinfu-2016）
- `全国全网`（案例 case-yisui-2024）
- `全国首家`（案例 case-wanya-2024）
- `最专业`（案例 case-samr-top10-2024）

## 诚实说明

- 违规标签来自确定性规则教师（M2 matcher + 规则库），不是人工标注；
- 免责样本按 2023 年执法指南第五/六条标为 pass，**假设广告主可举证**；
- 语义改写集（semantic_unknown）不参与训练、不进 M4 门禁，只用于报告覆盖差距；
- eval 按词条分折 + 留出案例 + 留出模板/免责样本，构建时强制文本零重叠；
- 规则教师覆盖不到的公开案例短语（如“最专业”“全国首家”）会列入词典缺口，说明规则升级（M5 热更新）与模型泛化都还有工作要做，不回避。
