---
name: procurement-sourcing-advisor
description: 在淘宝、京东上完成可审计的采购选型与比价：把口语需求形式化为采购简报，用 AI + playwright-mcp 逐链接展开配置（SKU）并采集评价，按配置分别记录价格与参数，计算到手价与 TCO，按 AI 规划的加权评分卡排序，评估拆单，输出 Markdown 采购备忘录 + CSV 比价表 + JSON 机器可读报告。对存在明确型号/MPN 的商品，还会用 AI + playwright-mcp 到厂商官网核对技术参数（官网只补参数，不参与比价）。只要用户提到"采购/批量买/买几台/选一批/选型/比价/预算内推荐/供应商对比/京东淘宝哪家便宜/算上耗材和保修的总成本/出一份采购方案/查一下这个型号的官方参数"，或者只是说"帮我选一台/推荐个型号/买两台"这类要花钱买东西的选型请求，即使用户没有点名平台，也应使用本技能。不适用于拼多多（明确排除），也不用于单纯查询商品参数、解释概念或写爬虫。
---

# 采购选型与比价顾问

这个技能把"帮我买 X"变成一份能被复核的采购决策：每个配置单独计价、证据可追溯、缺失值不被编造、评分权重显式、结论带风险与下一步。

商品与价格采集由 AI 通过 `playwright-mcp` 完成；存在明确型号时，官网参数核对也由 AI 用同一个浏览器完成。本技能的脚本只做**校验、归一化、计算、评分、渲染**——这些是最容易出错、也最需要确定性的部分。

## 为什么按配置（SKU）而不是按链接

一个商品链接通常挂着多个配置：27 寸固定支架 ¥629、27 寸升降支架 ¥749、32 寸 ¥1,299。链接页显示的"起价"往往是最低配，直接拿它比价等于在比较不同的商品。

所以：

- **候选 = 一个配置**，不是一条链接
- 每个配置必须记录 `option_path`（实际点选过的选项链）
- 价格必须在**选中该配置之后**读取
- 同一链接的不同配置分别评分、分别淘汰

## 环境

Python 环境固定为 conda 环境 `procurement-advisor`（Python 3.13），已装 `pytest`、`jsonschema`。

```powershell
& "$env:USERPROFILE\.conda\envs\procurement-advisor\python.exe" --version
```

浏览器通过项目 `.opencode/opencode.json` 里的 `playwright` MCP 提供，使用持久化 profile（首次需在上面手动登录淘宝/京东）。工具不可用时先确认 MCP 已连接，再检查 Chrome 是否被其他实例占用。

脚本从技能目录运行：

```powershell
$SKILL = "D:\workspace\explore\.opencode\skills\procurement-sourcing-advisor"
```

## 工作流

### Stage 0 — 需求澄清与维度确认（采集前必须完成）

读 `references/requirement-intake.md` 与 `references/dimension-planner.md`，把口语需求变成可执行的计划。

流程：

1. 识别缺失信息，最多问 5 个会改变结果的问题（数量/预算、硬条件、使用场景、替代空间、取舍），每条都给默认值
2. 写 `brief.json` 的 `requirements[]`：`must` 是门槛，`should` 是需求匹配维度并带权重
3. 写 `scorecard.json`：维度分为 `fit` / `value` / `risk`，配置 `stages.fit_threshold`
4. 生成确认单，把需求、筛选、维度、假设一次呈现给用户：

```powershell
& $py "$SKILL\scripts\render_confirmation.py" --brief "$WS\brief.json" --scorecard "$WS\scorecard.json"
```

5. 用户确认后冻结（未确认不得采集）：

```powershell
& $py "$SKILL\scripts\freeze_plan.py" --brief "$WS\brief.json" --scorecard "$WS\scorecard.json"
```

用户说"你看着办"时用默认值，写入 `assumptions`，并标记为**未确认假设**——不写进 `must`，不改变淘汰结果。用户修改任一项后重新冻结，`revision` 递增。

**在确认单通过之前，不打开商品页、不采集数据。**

另外必须写 `variant_sensitive`：这次采购里哪些差异**必须**区分（内存/硬盘/尺寸/支架/面板/套餐/颜色）。它决定 Stage 3 要展开哪些选项。

### Stage 1 — 打开页面与登录检查

```
browser_navigate(url="https://www.jd.com")
browser_snapshot()
```

- 出现登录墙 → 让用户在弹出的浏览器里手动登录，**不要**代填账号密码
- 出现验证码/滑块 → 停止，告知用户
- 页面只有页头页脚、正文"暂时无法展示该商品的信息" → 判为 `blocked`，停止并记录，不要重试刷页

### Stage 2 — 搜索召回（listing 级）

用页面搜索，`browser_snapshot` 只取结果卡片区域。这一阶段**只记录 listing 信息**（标题、链接、店铺、是否广告位），不要记录价格——搜索结果价通常是默认 SKU 或"起"价。

```
browser_navigate(url="https://search.jd.com/Search?keyword=27英寸%202K%20显示器")
browser_snapshot()
```

每个平台 2–3 组关键词；同一平台串行，页面之间间隔 3–5 秒。单次采购最多展开 10 个 listing。

**命中排除项就停**：结果标题或配置里出现简报 `exclusions` 中的词（如"二手""拆机""翻新""SMR"）时，**立即停止该 listing 的后续采集**，只保留已经读到的字段，并在采集文件里记 `aborted: true` + `abort_reason`。这类候选会保留在报告里说明淘汰原因，但不进入评分与比价。

### Stage 3 — 配置展开（核心）

对每个候选 listing：

1. 打开商品页，读取配置选项组（尺寸/型号/套餐/颜色）
2. 逐个点选目标配置（`browser_click` / `browser_select_option`）
3. **每次选择后重新读取价格与库存**，确认页面显示的是当前配置
4. 读取该配置的参数表（`browser_evaluate` 取窄范围 DOM 文本即可，不必整页快照）
5. 写入 `variants[]`，`option_path` 记录实际选项链

页面信息是懒加载的：**先滚动**到参数区/评价区，等 1–2 秒再抽取；只取当前视口往往拿不到完整参数和评价。

同一 listing 下**可以有多个配置同时入选**：只要各自满足硬条件，就都作为独立候选参与评分，不要只保留一个"代表配置"。

判定规则：

- 只抓与 `variant_sensitive` 相关的配置；无关的颜色差异可以合并，但要在 `notes` 说明
- 超预算或明显不满足必选条件的配置，仍然记录（后续硬过滤会淘汰并说明原因），不要静默丢弃
- 价格必须带 `snippet`（页面原话，如 `¥749.00 已选：多功能支架`）
- 选不中/售罄的配置记 `stock_status=无货`，不要用相邻配置的价格代替

### Stage 4 — 评价与详情区采集

在商品页采集：

- **评分/评价数**：淘宝详情页的"用户评价"、京东的"大家评"
- **首屏样本**（建议 10–20 条），按主题归类
- **参数信息**：淘宝的"参数信息"、京东的"商品详情 / 使用说明"
- **售后保障**：保修年限、上门/换新条款（作为上下文；保修年限必须来自明确字段或参数表，不要从“付费延保”文案推断）
- **店铺**：自营/旗舰/专营、店铺名
- **问大家 / 大家评**：常见问题与回答，作为风险线索
- **推荐位**：只记录，不作为候选

负评主题必须有原话 `snippets`。采不到就留空（`sample_size=0`），**不要**写"口碑不错"。样本量会写进报告，不做统计推断。

### Stage 4b — 官网参数补全（有具体型号时）

商品页能读到明确型号/认证型号/MPN（如 `ThinkPad T14 Gen 5 21ML`、`WD201KFGX`、`Dell R760`），且候选可能进入前 5 时，用 `playwright-mcp` 打开厂商官网核对技术参数。没有具体型号的品类跳过，不要凭系列名猜官网页面。

流程：

1. 从商品页参数区读取 `型号` / `认证型号` / `MPN` / `Part Number`
2. 通过厂商官网站内搜索或已知产品页打开**同型号**页面（国行商品优先国行官网）
3. 确认页面标题或规格表包含完整型号，把页面原话记入 `matched_text`
4. 只抽与决策相关的规格：CPU/GPU、内存、硬盘、接口、尺寸、重量、功耗、面板/分辨率、盘片/记录技术、缓存、转速、工作负载、MTBF、保修、兼容列表
5. 写入 `capture.official`（整条 listing 共用型号）或 `variant.official`（各配置型号不同）；后者优先
6. 只对上系列名记 `match_status=family`，官网没有该型号记 `not_found`；这两种都不进入评分
7. 官网登录墙/验证码/403 → 写入 `errors`（`stage="official"`），继续用平台数据，不绕过

官网块只放技术参数，**禁止**写价格、库存、促销、到手价；官网价格不进入价格锚点或排序。官网参数只补商品页缺失的字段；与商品页冲突时保留商品页值，写入 `official.conflicts` 并给出 warning。字段与校验规则见 `references/capture-format.md`。

### Stage 5 — 归一化与校验

把采集结果写成 `raw_capture.json`（结构见 `references/capture-format.md`），先校验：

```powershell
& $py "$SKILL\scripts\validate_capture.py" "$WS\raw_capture.json"
```

校验会拒绝：缺价格 snippet、`option_path` 为空、同一 listing 内 `variant_id` 重复、`page_state` 非 `ok`、官网 `exact` 缺型号原话/参数/证据、官网块出现价格或库存字段。校验不过就回去补采集，不要绕过。

### Stage 6 — 到手价与 TCO

需要用户/业务提供的费用写进 `costs.json` 和 `tco_model.json`。**不知道就留空**，脚本会输出区间而不是编造。

```json
{
  "defaults": {
    "price_includes_tax": true,
    "price_includes_shipping": false,
    "shipping_cny": 0,
    "coupon_cny": 0,
    "other_fees_cny": 0
  },
  "by_product": {"jd:100001": {"shipping_cny": 15}}
}
```

读 `references/procurement-strategies.md` 了解为什么 `tax_rate` 单独出现不算数、为什么缺运费时不给上限。

**价格锚点**：同一型号在自营/官方旗舰和第三方店之间可能差一倍（官换、散装、OEM、店保）。流水线会自动取同型号下自营/官方旗舰价格的中位数作为锚点；低于锚点 70% 的候选，价格分封顶为最低档，并标记 ⚠，必须先证实"全新 + 发票 + 保修主体"才能按正常价比较。

锚点比例可在评分卡里调整：

```json
"price_anchor": {"min_ratio": 0.7}
```

### Stage 7 — 需求匹配、性价比与排序

按实际物品生成 `scorecard.json`（参考 `references/category-scorecards.md`），维度分三组：

- `fit`：需求匹配（来自 `requirements[]` 的 should 项）
- `value`：同价位性能（规格/定位/缓存等）
- `risk`：渠道、评价，只影响置信度与提示

并配置 `stages.fit_threshold`（默认 0.8）。评分卡需要用户确认后冻结。

字段路径必须对齐**归一化后的键名**。`scripts/common.py` 的 `_SPEC_KEY_MAP` 会把页面标签映射成英文规范键（`面板` → `attributes.panel`、`屏幕尺寸` → `attributes.screen_inch`、`保修` → `detail.warranty`）。写评分卡前先打开 `output/normalized.json` 看一眼实际键名。

评价维度用 `method: "reviews"`，按评分分档并扣减负评率；缺失时按声明的策略计分：

```json
{"id": "reviews", "label": "评价", "group": "risk", "weight": 0.5, "method": "reviews",
 "field": "reviews.rating", "negative_rate_penalty": 2.0, "missing": "worst"}
```

排序是字典序，不是加权和：

1. `must` 全部通过，否则淘汰
2. `fit_score ≥ fit_threshold`，否则进入“需求未完全满足”备选
3. 在合格池内按 `value_score`（性能 / 价格指数）排序
4. 同分看价格

流水线会自动完成，不需要手算：

```powershell
& $py "$SKILL\scripts\run_pipeline.py" --workspace "$WS"
```

硬过滤淘汰的候选保留在结果里，带 `elimination_reasons`（含实际值与阈值）。缺失维度按评分卡策略计分，同时进入 `missing_dimensions`，并降低 `score_confidence`。

### Stage 8 — 拆单、报告与自检

一键跑完（离线、可重放）：

```powershell
$WS = "D:\workspace\explore\procurement-runs\laptop-2026-10"
& $py "$SKILL\scripts\run_pipeline.py" --workspace $WS
```

工作区需要：`brief.json`、`raw_capture.json`、`scorecard.json`；可选 `costs.json`、`tco_model.json`、`bundle_options.json`。

产物在 `$WS\output\`：

- `report.md` — 采购备忘录（决策摘要、候选对比、同链接配置对比、推荐、备选、淘汰、拆单、评价摘要、证据缺口）
- `candidates.csv` — 比价表（每个配置一行，UTF-8 BOM）
- `report.json` — 机器可读全量报告（含配置矩阵、评价摘要、证据、warnings、errors）
- `normalized/priced/tco/scored/bundle.json` — 中间结果

最后对照 `references/compliance.md` 的自检清单逐条确认，再交付。

## 硬规则（不可协商）

**采集前**：需求与评比维度必须经用户确认并冻结（`freeze_plan.py`）；未确认不打开商品页、不采集数据。

1. **一个链接 ≠ 一个商品**。候选必须落到配置（SKU），价格必须是选中该配置后的价格；不允许用搜索页"起价"代表整条链接。
2. **同一链接的多个合格配置都保留**。不要为了"一个链接一个候选"而合并或丢弃。
3. **命中排除项立即停采**。标题/配置出现"二手/拆机/翻新/SMR"等排除词时停止该 listing 的后续采集，保留已采字段并标记 `aborted`；它不参与评分与比价。
4. **异常低价先验证**。同型号低于官方/自营锚点 70% 的候选，价格分封顶，必须先证实"全新 + 发票 + 保修主体"，不允许靠低价直接胜出。
5. **推荐位不是候选**。页面底部的"推荐/搭配购/相似商品"只记录，不进入候选池。
6. **价格缺失 ≠ 0**。空字符串、`null`、`0`、负数、"面议"、"登录后可见" 一律 `UNKNOWN`，不参与价格排序。
7. **不编造**券、运费、税费、库存、保修、发票、规格、销量、店铺资质、评价。
8. **评价必须有样本**。没有采样就写 `UNKNOWN`；负评主题必须带原话，不许概括为"口碑好/差"。
9. **拼多多不覆盖**。不要"顺手比一下"，也不要伪造 platform。
10. **浏览器只读与点选**。登录墙、验证码、403、内容未返回 → 停止并告知用户；不注入脚本、不伪造 UA、不破解验证码。
11. **同平台串行**。页面之间间隔 3–5 秒；不并发开多个同平台页面。
12. **冲突保留页面值**。搜索页与商品页价格不一致时，以**选中配置后的商品页价格**为准，两条都进 `price_observations`。
13. **评分卡显式**。权重和为 1，缺失策略声明，manual 维度必须有证据。
14. **官网只补参数**。只有完整型号 `match_status=exact` 的官网参数才能补入评分字段；官网价格、库存、促销一律不采集、不参与价格锚点与排序。官网 URL 必须是厂商官方域名，不能是京东/淘宝/天猫；`family`/`not_found` 只作记录。
15. **结论带条件**。价格"以结算页为准"，库存"下单前确认"，拆单节省标 `savings_status`。

## 输出格式

Markdown 报告固定章节：

```
# 采购方案：<物品>
## 1. 决策摘要
## 2. 采购简报
## 3. 候选对比
### 3.1 同链接配置对比
### 3.2 价格锚点与异常低价
### 3.3 需求匹配矩阵
### 3.4 性价比与帕累托前沿
### 3.5 评分字段取值
### 3.6 官网参数核对（有官网数据时）
## 4. 推荐方案
### 4.1 评价摘要
## 5. 备选方案
## 6. 淘汰与风险
## 7. 拆单/凑单
## 8. 证据缺口与下一步
## 9. 方法与范围
## 10. 证据附录
```

由 `scripts/render_report.py` 生成，不要手写——手写容易漏掉"证据缺口"或把 `UNKNOWN` 写成数字。

## 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 价格始终是"起"或最低配 | 没展开配置 | 回到 Stage 3，逐个点选配置后再读价 |
| 同一链接的两个配置被合成一条 | `option_path` 为空或 `variant_id` 相同 | 补 `option_path`，确保 `variant_id` 唯一 |
| 校验报"价格可见但没有 snippet" | 价格是记忆或推断 | 重新读取页面原话，或标 `hidden` |
| 所有价格都是 `NaN`/空 | 页面被登录墙拦住 | 人工登录后重试；仍失败则报告"价格不可比" |
| 京东页只有页头页脚 | 未登录/自动化会话被拒 | 按 `blocked` 停止；不要循环重试 |
| 官网只能对上系列名 | 页面不是该具体型号 | 记 `match_status=family`，参数不进入评分 |
| 官网参数与商品页冲突 | 版本/地区/配置差异 | 保留商品页值，官网值写入 `official.conflicts` 并提示复核 |
| 官网页面只有营销文案 | 没有规格表/数据手册 | 记 `not_found` 或跳过，不要猜参数 |
| 评价全是"好评" | 只看了标签没看内容 | 采首屏样本，记录负评主题与原话 |
| 评分卡校验失败 | 权重和 ≠ 1 / 未知 method | 按报错改评分卡 |
| 无合格候选 | 硬过滤过严 | 报告淘汰原因分布，建议放宽哪一项，不要偷偷放宽 |

## 资源导航

- `references/requirement-intake.md` — Stage 0，把口语变简报（含确认与冻结流程）
- `references/dimension-planner.md` — 从使用场景推导 fit/value/risk 维度
- `references/browser-playbook.md` — playwright-mcp 操作手册：搜索、配置展开、评价采集、官网规格核对、登录/验证码处理
- `references/capture-format.md` — `raw_capture.json` 字段与证据规范
- `references/category-scorecards.md` — 评分方法、权重规划、缺失策略、评价维度
- `references/procurement-strategies.md` — 到手价、TCO、拆单、渠道风险
- `references/compliance.md` — 硬规则与交付自检
- `scripts/` — `render_confirmation.py`、`freeze_plan.py`、`validate_capture.py`、`normalize_candidates.py`、`landed_price.py`、`tco_calc.py`、`requirement_fit.py`、`value_analysis.py`、`rank_candidates.py`、`score_candidates.py`、`bundle_optimizer.py`、`review_summary.py`、`variant_matrix.py`、`render_report.py`、`run_pipeline.py`
- `schemas/` — 采集、简报、候选、评分卡、报告的 JSON Schema
- `tests/` — 离线回归（`python -m pytest tests -q`）
