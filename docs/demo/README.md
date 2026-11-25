# 对外演示材料：交付验收一页纸

面向客户/面试官/内部汇报的**单页 A4** 材料，内容全部来自 M0–M12 的实测验收数据。

| 文件 | 说明 |
|---|---|
| `adguard-交付验收一页纸.html` | 源文件（单文件、无外部依赖、可直接浏览器打开/编辑） |
| `adguard-交付验收一页纸.pdf` | A4 单页 PDF（用 Edge 打印生成，中文字体嵌入） |
| `adguard-交付验收一页纸_preview.png` | 预览图（便于聊天/网页直接展示） |
| `render.py` | 重新生成 PDF/PNG 的脚本（Playwright + 系统 Edge/Chrome） |

## 重新生成

```bash
# 依赖：Python + playwright（无需下载 Chromium，使用系统 Edge/Chrome）
pip install playwright
python docs/demo/render.py
```

脚本会：
1. 用 Edge 的 print media 打开 HTML；
2. 打印为 A4 单页 PDF（`prefer_css_page_size`）；
3. 生成全页 PNG 预览；
4. 校验 PDF 必须为 **1 页**（否则退出码非 0，防止材料溢出）。

## 数据口径

- 158 项冒烟 = M1 29 + M3 21 + M7 19 + M8 37 + M9 18 + M10 18 + M12 16；
- 图文一致性 30 对构造样本，recall/FPR/precision 来自真实 DeepSeek 调用（本地 + ECS）；
- 7B 服务端门禁与混合门禁来自 M5 / M11 报告（AutoDL RTX4090 48G + vLLM）；
- 灰度/回滚数据来自 M6（ECS + nginx `split_clients`）；
- 负结果与限制（7B hybrid 未切生产、ECS 不跑 headless 截图、ASR tiny 精度、单 ECS 无跨可用区 HA、无 IdP）均在页面"边界"区如实标注。

## 备注

- 内容需保持**单页**：新增内容前先压缩现有条目，或调整 HTML 中 CSS 字号/间距后重跑 `render.py` 校验页数。
- 对外使用前可按需把"内部验收材料"改为对应受众版本（客户版可去掉 git/仓库路径）。
