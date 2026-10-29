# 需求形式化（Stage 0）

采购检索最贵的错误不是"没找到便宜货"，而是"找错了东西"。在打开浏览器之前，先把一句口语需求变成可审计的采购简报。简报是后续硬过滤、评分卡和报告的共同依据；没有它，比价只是在比较不同的商品。

## 何时需要追问

用户通常只给一句话，例如"买两台办公用的笔记本，预算一万二"。以下信息缺失会直接改变结果，值得一次性问清（一次问完，不要逐条打断）：

| 字段 | 为什么关键 | 缺失时的默认 |
|---|---|---|
| 数量 | 决定是否拆单、是否走批量价 | 1 |
| 预算口径 | 单件还是总额，含税还是不含税 | 按总额/数量折算单件，标注假设 |
| 必选条件 | 硬过滤依据，决定谁被淘汰 | 只保留平台可验证的条件 |
| 排除条件 | 二手/翻新/广告位/特定品牌 | 排除二手、翻新、广告位 |
| 售后要求 | 保修年限、发票类型 | 记为 UNKNOWN，不假设 |
| 是否允许替代型号 | 决定召回宽度 | 允许，但报告中单列 |
| 是否允许拆单 | 决定 bundle 分析 | 不允许 |
| 使用场景 | 决定评分权重 | 通用办公 |
| **配置敏感项** | 决定要展开哪些 SKU 选项 | 按品类取：尺寸/内存/硬盘/支架 |

如果用户明确说"你看着办"，就用上表默认值，并把每一项默认写进简报的 `assumptions`，让用户能在报告里看到你替他做了什么决定。**默认值不是事实**，不能写进 `must_have`。

## 先确认，再采集

确认单是采集的前置条件，不是可选项。流程：

1. 最多问 5 个会改变结果的问题，每条带默认值
2. 写 `brief.requirements[]`（must/should）与 `scorecard`（fit/value/risk）
3. `render_confirmation.py` 生成确认单，一次呈现“需求 / 筛选 / 维度 / 假设”
4. 用户确认后 `freeze_plan.py` 冻结，写入 `confirmed.by_user / at / revision`
5. 未冻结不打开商品页

用户要求改维度时，修改后重新冻结，`revision` 递增；旧报告标注被取代。

## 简报 JSON

写入工作区 `brief.json`，字段定义见 `schemas/procurement_brief.schema.json`：

```json
{
  "brief_id": "laptop-2026-10",
  "category": "轻薄本",
  "item_description": "团队用 14 英寸轻薄本",
  "quantity": 2,
  "budget_cny": 12000,
  "budget_per_unit_cny": 6000,
  "must_have": ["16GB 内存", "1TB 硬盘", "至少 2 年保修"],
  "nice_to_have": ["高色域屏", "轻于 1.5kg"],
  "exclusions": ["二手", "翻新"],
  "allow_split": true,
  "allow_alternative_models": true,
  "invoice_required": true,
  "min_warranty_months": 24,
  "usage_context": "日常办公、差旅",
  "variant_sensitive": ["内存", "硬盘", "屏幕尺寸", "CPU"],
  "usage_profile": {"workload": "办公 + 差旅", "lifetime_years": 4},
  "requirements": [
    {"id": "ram", "type": "must", "label": "16GB 内存", "field": "attributes.ram_gb", "op": "gte", "value": 16, "missing": "review"},
    {"id": "storage", "type": "must", "label": "1TB 硬盘", "field": "attributes.storage_gb", "op": "gte", "value": 1000, "missing": "review"},
    {"id": "warranty", "type": "should", "label": "3 年保修", "weight": 0.6, "field": "detail.warranty", "op": "contains_any", "value": ["3年", "三年"], "missing": "worst"},
    {"id": "weight", "type": "should", "label": "轻于 1.5kg", "weight": 0.4, "field": "attributes.weight_kg", "op": "lte", "value": 1.5, "missing": "worst"}
  ],
  "confirmed": {"by_user": true, "at": "2026-10-29T08:00:00+08:00", "revision": 2, "changes": []},
  "assumptions": ["预算按单件 6000 元理解"]
}
```

`variant_sensitive` 是这次采购里**必须区分**的配置维度。它直接决定 Stage 3 要展开哪些选项：写"内存/硬盘"，就不要在 8GB 与 16GB 之间取一个默认价；没写的维度（例如颜色）可以合并，但要在采集文件的 `notes` 里说明。

## 把需求翻译成"可检索"与"可判定"

一条需求要同时满足两个条件才值得写进 `must_have`：

1. **可检索**：能变成关键词。`16GB 内存` 可以；`用起来不卡` 不行。
2. **可判定**：能从商品页的配置、参数表或评价区读出真/假/未知。`有发票` 可以；`质量好` 不行。

不可判定的偏好放进 `nice_to_have`，由评分卡用关键词或人工评分处理；完全主观的诉求写进 `usage_context`，让评分卡的理由部分体现它，而不是伪装成硬条件。

## 关键词规划

每个必选条件至少派生一个检索词，并准备 2–4 组查询覆盖不同表述：

- 规格词：`16GB 1TB`、`16G+1T`
- 型号词：`ThinkPad T14`、`战66 七代`
- 场景词：`商务轻薄本`、`学生本`
- 排除词：`二手`、`翻新`、`官翻`

淘宝与京东的检索语法不同：淘宝吃自然语言长词，京东对型号词更敏感。同一组需求在两边用不同措辞是正常的，不要为了"公平"强行用同一串关键词。

## 交付前的自检

- [ ] 数量、预算口径、必选条件都能被脚本读取
- [ ] 每条必选条件都有对应的检索词
- [ ] 默认值全部写进 `assumptions`
- [ ] 没有把"希望"写进 `must_have`
- [ ] 拼多多相关需求已明确告知不在覆盖范围

## 拼多多边界

本技能只覆盖淘宝与京东。用户提出拼多多时，不要"顺手比一下"：直接说明未覆盖，并解释原因（无可用 MCP 契约、爬虫/反爬不在本技能范围内）。如果用户坚持，建议他自行提供拼多多页面链接与截图，再手工录入为候选——但不要伪造 `platform` 字段。
