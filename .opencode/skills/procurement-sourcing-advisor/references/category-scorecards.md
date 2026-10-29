# 评分卡规划（Stage 5）

评分卡由 AI 针对**本次实际采购的物品**生成，不是预制模板；但生成后必须是一份显式 JSON，权重和为 1，由 `scripts/score_candidates.py` 确定性计算。这样"推荐理由"可以被逐项复核，而不是一段无法证伪的说辞。

## 评分卡的四个部分

```json
{
  "id": "laptop-office-2026",
  "name": "办公轻薄本评分卡",
  "category": "轻薄本",
  "version": "1.0",
  "rationale": "为什么是这些维度和权重",
  "enrichment_targets": ["price", "detail.warranty", "detail.invoice"],
  "hard_filters": [],
  "dimensions": []
}
```

`rationale` 不是装饰。写下"为什么给保修 0.2 权重"，可以防止下一步为了让某个候选赢而临时改权重。

## 维度分组（fit / value / risk）

评分卡采用需求优先模型，每个维度必须标 `group`：

- `fit`：需求匹配（保修、定位、重量等 should 项），决定 `fit_score`
- `value`：同价位性能（规格档次、缓存、刷新率），用于 `value_score`
- `risk`：渠道、评价，只影响置信度与提示，不参与主排序

```json
"stages": {"fit_threshold": 0.8}
```

组内权重各自和为 1；排序是字典序（must → fit 阈值 → value → 价格），不是加权总分。价格不设独立维度，它是 `value = 性能 / 价格指数` 的分母。

## 硬过滤 vs 评分维度

判断标准：**违反后是否"不可接受"**。

- 会直接造成采购失败的（超预算、二手、广告位、无发票），进 `hard_filters`，用 `fail` / `pass` / `review` 三种缺失策略。
- 只是"更差一点"的（性能档次、保修 2 年 vs 3 年、店铺等级），进 `dimensions`。

把偏好塞进硬过滤会让候选池过窄，最后只剩 1 条，比价失去意义；把硬条件放进评分又会让不合格品靠高分翻盘。

## 可用的评分方法

| method | 适用 | 关键字段 |
|---|---|---|
| `minmax` | 连续数值，池内相对比较 | `field`, `direction` |
| `thresholds` | 有明确档位（价格区间、重量区间） | `thresholds: [{min,max,score}]` |
| `categorical` | 枚举值映射（店铺类型、成色） | `map: {值: 分}` |
| `keyword` | 文本包含（CPU 型号、保修年限） | `rules: [{any:[...],score}]` |
| `boolean` | 是/否 | `map` 可选 |
| `presence` | 有证据即满分（如"有检测报告"） | `field` |
| `reviews` | 评价评分 + 负评率惩罚 | `field: reviews.rating`、`negative_rate_penalty` |
| `manual` | 只能由人/模型判断 | 需 `evidence_required: true` |

分数统一 0–5，脚本折算成 0–100 加权分。

**字段路径必须与归一化结果一致**。归一化器把页面标签映射为英文规范键（`面板`→`attributes.panel`、`屏幕尺寸`→`attributes.screen_inch`、`分辨率`→`attributes.resolution`），并生成可比较的数值键（`screen_inch`、`ram_gb`、`storage_gb`、`weight_kg`）。写完评分卡后先跑一次，检查 `missing_dimensions`：如果某个维度对所有候选都"缺失"，多半是键名写错了，而不是数据真的没有。


`minmax` 使用池内最小/最大值，因此**候选池变化会改变分数**。这是有意的：它是相对比较，不是绝对质量分。报告中应说明这一点，避免用户拿两次不同池子的分数直接对比。

## 缺失值策略

每个维度必须声明 `missing`：

- `worst`：缺证据按 0 分。适合"没写保修 = 不能假设有保修"这类合规项。
- `neutral`：按 2.5 分。只适合该项确实与质量无关、且缺失不影响判断的情况；慎用。
- `zero`：显式按 0 分（与 `worst` 类似，但语义上表示"缺了就是没有"）。
- `review`：计分同时标记人工复核。

缺失策略与得分是两条独立信息：`missing_dimensions` 记录哪些维度靠策略补分，`score_confidence` 单独反映证据完整度。**不要**把缺失字段当作 0 元、0 年保修或"不支持"。

## 评价维度怎么用

`method: "reviews"` 只吃采集到的评价证据：

```json
{"id": "reviews", "label": "评价", "weight": 0.15, "method": "reviews",
 "field": "reviews.rating", "negative_rate_penalty": 2.0, "missing": "worst"}
```

- 评分分档默认 4.7→5、4.5→4、4.2→3、4.0→2，其余 1
- 负评率按 `negative_rate_penalty × negative_rate` 扣分
- 没有采样（`reviews.status != ok`）就按缺失策略处理，**不要**给"中性 3 分"——那等于凭空假设口碑不差
- 负评主题用于报告叙述，不直接进分数；避免同一问题既扣分又扣权重两次

评价权重建议 0.10–0.20：它能提示集中质量风险，但样本是首屏，不足以替代保修与发票这类硬条款。

## 价格锚点

评分卡可以配置同款价格锚点：

```json
"price_anchor": {"min_ratio": 0.7}
```

流水线会取同型号自营/官方旗舰价格的中位数作为锚点；低于 `min_ratio` 的候选，`minmax` 价格分封顶为 1.0（最低档），并标记 `price_anomaly`。只有页面写明"全新/原封"且可开发票时才解除封顶。

没有自营/官方对照的型号不会生成锚点，也就不会触发封顶——这类型号的低价仍需人工确认成色。

## 配置（SKU）是评分单位
同一链接的不同配置是不同候选，`price`、`attributes`、`detail` 都按当前配置取值。评分卡不需要为每个配置单独写；但硬过滤（预算、内存、尺寸）会自然把不合格配置淘汰，报告里的"同链接配置对比"会展示差价来自哪里。

## 权重规划

一个可用的起手式（按品类调整，不要照抄）：

| 品类 | 价格 | 核心规格 | 售后/保修 | 合规（发票） | 渠道风险 | 其他 |
|---|---|---|---|---|---|---|
| 办公 IT | 0.30 | 0.25 | 0.20 | 0.15 | 0.10 | — |
| 生产耗材 | 0.40 | 0.25 | — | 0.20 | 0.15 | — |
| 办公家具 | 0.35 | 0.25 | 0.15 | 0.10 | 0.15 | — |
| 工具/仪器 | 0.25 | 0.35 | 0.25 | — | 0.15 | — |
| 礼品/定制 | 0.30 | 0.20 | — | 0.15 | 0.20 | 0.15（交期） |

原则：

- 价格权重很少超过 0.4。采购的目标是"总成本可接受"，不是"最便宜"。
- 售后和发票在 B 端采购里通常是硬条件；若不设硬过滤，就要给足权重。
- 单一维度不建议超过 0.4，否则评分退化成单指标排序。
- 权重必须是 0.05 的倍数，方便人工核对。

## 评分卡示例

```json
{
  "dimensions": [
    {"id": "price", "label": "价格", "weight": 0.30, "method": "minmax",
     "field": "landed_price.floor", "direction": "lower_better", "missing": "worst"},
    {"id": "performance", "label": "性能", "weight": 0.25, "method": "keyword",
     "field": "attributes.cpu",
     "rules": [{"any": ["Ultra 7", "i7"], "score": 5}, {"any": ["Ultra 5", "i5"], "score": 3}],
     "default": 2, "missing": "worst"},
    {"id": "warranty", "label": "保修", "weight": 0.20, "method": "keyword",
     "field": "detail.warranty",
     "rules": [{"any": ["3年"], "score": 5}, {"any": ["2年"], "score": 4}, {"any": ["1年"], "score": 2}],
     "default": 1, "missing": "worst"},
    {"id": "invoice", "label": "发票", "weight": 0.15, "method": "keyword",
     "field": "detail.invoice",
     "rules": [{"any": ["专用发票"], "score": 5}, {"any": ["普通发票"], "score": 4}, {"any": ["发票"], "score": 3}],
     "default": 0, "missing": "worst"},
    {"id": "seller", "label": "渠道", "weight": 0.10, "method": "keyword",
     "field": "shop_name",
     "rules": [{"any": ["自营", "官方旗舰店"], "score": 5}, {"any": ["旗舰店"], "score": 4}],
     "default": 2, "missing": "worst"}
  ]
}
```

## 校验

写完后先跑：

```powershell
conda run -n procurement-advisor python scripts/score_candidates.py `
  --candidates output/tco.json --scorecard scorecard.json `
  --brief brief.json --output output/scored.json
```

脚本会拒绝：权重和 ≠ 1、重复维度 id、未知 method/op、缺失策略非法、`manual` 维度未要求证据。校验失败时修改评分卡，不要绕过脚本手算。

## 人工评分

`manual` 维度（例如"做工手感""供应商配合度"）必须带证据文本：

```json
"manual_scores": {
  "fit": {"score": 4, "evidence": "详情页写明人体工学腰托，客服确认可调节"}
}
```

没有 `evidence` 的 manual 分数按缺失处理——这是防止"我觉得这个更好"进入量化结论的唯一闸门。
