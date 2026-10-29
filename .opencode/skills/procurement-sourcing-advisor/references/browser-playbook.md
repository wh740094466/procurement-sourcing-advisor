# playwright-mcp 操作手册

本技能用 AI 直接驱动浏览器，不依赖任何电商爬虫 MCP。原则只有一条：**页面上写着什么，就记什么；没写就是 UNKNOWN**。

## 工具与用途

| 工具 | 用途 | 注意 |
|---|---|---|
| `browser_navigate` | 打开搜索页/商品页 | 一次一个页面，等加载完成 |
| `browser_snapshot` | 读取可交互结构 | 只对目标区域取快照，别整页复制 |
| `browser_click` | 点选配置、翻页、展开参数 | 点完必须重新读价 |
| `browser_select_option` | 下拉式配置 | 同上 |
| `browser_evaluate` | 窄范围取文本（价格、参数表、评价） | 返回结构化小结果，不要 `document.body.innerText` |
| `browser_take_screenshot` | 争议页面留证 | 只在需要人工复核时用 |

## 搜索入口

- 京东：`https://search.jd.com/Search?keyword=<urlencoded>`
- 淘宝：`https://s.taobao.com/search?q=<urlencoded>`

搜索结果只用于**召回 listing**：标题、链接、店铺、广告位标记。搜索页价格通常是默认 SKU 或"起"价，不写入候选。

## 页面分区与采集点

| 平台 | 区块 | 写入 |
|---|---|---|
| 淘宝 | 参数信息 | `sections.params` → `attributes` |
| 淘宝 | 用户评价 | `reviews` |
| 淘宝 | 问大家 | `sections.qa` |
| 京东 | 商品详情 / 规格参数 | `sections.params` |
| 京东 | 大家评 | `reviews` |
| 京东 | 问大家 | `sections.qa` |
| 京东 | 售后保障 | `sections.after_sales`（可回填 `detail.warranty`） |
| 京东 | 使用说明 | `sections.usage` |
| 京东 | 店铺 | `sections.shop` |
| 京东/淘宝 | 推荐、搭配购、相似商品 | `sections.recommendations`，只记录，**不作候选** |
| 厂商官网 | 产品规格/数据手册 | `official.specs`，只补参数，**不取价** |

页面是懒加载的：先滚动到目标区块，等 1–2 秒再抽取。可以逐段滚动（参数 → 评价 → 售后），不要一次抓 `body.innerText` 就当完整。

```js
// 滚动一屏，触发懒加载
browser_evaluate({ function: "() => { window.scrollBy(0, 900); return window.scrollY; }" })
browser_wait_for({ time: 1.5 })
// 参数区/评价区通常要滚动 2–4 次才会加载完
```

判断是否加载完：目标区块的文本长度连续两次滚动不再增加，或出现"没有更多了"。

## 命中排除项就停

搜索或详情阶段一旦看到简报 `exclusions` 里的词（二手、拆机、翻新、SMR……）：

1. 停止该 listing 的后续点击与翻页
2. 保留已经读到的字段
3. 在 capture 上记 `aborted: true` 与 `abort_reason`
4. 不再为它展开配置、采评价

这类记录会进入报告"淘汰与风险"，但不参与评分。继续采集只会浪费时间并污染候选池。

## 配置展开流程

对每个 listing：

1. 打开商品页，`browser_snapshot` 找到配置区（尺寸/型号/套餐/颜色）
2. 读取当前选中的配置标签，记录为 `option_path`
3. 读取价格与库存，确认与当前选中配置对应
4. 点选下一个目标配置，**重复第 2–3 步**
5. 参数表只取与决策相关的键（面板、分辨率、内存、支架、保修、发票），用 `browser_evaluate` 取文本

判定"价格属于当前配置"的方法：

- 页面上通常有"已选：X / Y / Z"或"规格：X"，价格紧随其后
- 若价格区显示"¥629 起"，说明还没选中具体配置，继续点选
- 若选中后价格没变，检查是否真的切换成功（选中态 class / aria-selected）

**不要**用相邻配置的价格代替售罄配置；售罄就记 `stock_status=无货`，价格留空。

## 官网规格补全（有具体型号时）

只有当商品页能读到**明确型号/认证型号/MPN**（如 `T14 Gen 5 21ML`、`WD201KFGX`、`Dell R760`）时，才去厂商官网核对参数。没有具体型号的品类（例如"27 英寸 2K 显示器"）不要凭系列名猜官网页面。

### 适用与不适用

- 适用：笔记本、服务器、硬盘、内存、显示器、打印机等有唯一型号或料号的产品
- 不适用：型号只写到系列名、页面没有型号字段、官网只有营销页没有规格表
- 只对**可能进入前 5** 的候选做官网核对，单型号最多打开 1 个官网页面，控制时间与上下文

### 入口与匹配

1. 从商品页参数区读取 `型号`、`认证型号`、`MPN`、`Part Number`
2. 优先使用厂商官网的站内搜索或已知产品页，例如：
   - 联想：`https://www.lenovo.com.cn/`
   - 戴尔：`https://www.dell.com.cn/`
   - 华硕：`https://www.asus.com.cn/`
   - 希捷：`https://www.seagate.com/cn/zh/`
   - 西部数据：`https://www.westerndigital.com/zh-cn/`
   - 群晖：`https://www.synology.cn/`
3. 打开产品页/规格页/数据手册，确认页面标题或规格表包含**完整型号**，把原话记入 `matched_text`
4. 只对上系列名时记 `match_status=family`；官网没有该型号记 `not_found`。这两种情况都不进入评分
5. 国行商品优先国行官网；只有全球页时在 `region` 标注，并在 `notes` 说明可能与国行配置不同

### 抽取范围

用 `browser_evaluate` 窄范围读取规格表，只取与决策相关的键：

- CPU/GPU、内存、硬盘、接口、尺寸、重量、功耗
- 面板、分辨率、刷新率、色域
- 盘片/记录技术、缓存、转速、工作负载、MTBF
- 保修年限、兼容列表、工作温度/海拔

滚动到规格区，等 1–2 秒再抽取；不要只取首屏，也不要复制整页 `body.innerText`。

### 写入官网块

```json
"official": {
  "manufacturer": "Lenovo",
  "model": "ThinkPad T14 Gen 5 21ML",
  "region": "CN",
  "url": "https://www.lenovo.com.cn/...",
  "captured_at": "2026-10-30T10:00:00+08:00",
  "match_status": "exact",
  "matched_text": "ThinkPad T14 Gen 5 (21ML) ...",
  "specs": {"CPU": "Intel Core Ultra 5 125U", "接口": "Thunderbolt 4 ×2 / USB-A ×2"},
  "evidence": [
    {"field": "official.specs", "snippet": "Thunderbolt 4 ×2 / USB-A ×2", "source_url": "https://www.lenovo.com.cn/..."}
  ]
}
```

- `capture.official` 用于整条 listing 共用一个型号；各配置型号不同则写到 `variant.official`
- `variant.official` 优先于 `capture.official`
- 官网块只放规格，**禁止**写价格、库存、促销、到手价
- 证据必须带 `source_url` + `snippet`；没有原话就不写

### 官网拦截

官网同样可能遇到登录墙、验证码、403、地区限制：

| 现象 | 处理 |
|---|---|
| 登录/注册墙 | 停止，不注册、不代填账号；记录 `errors` |
| 验证码/滑块 | 停止，不自动点击；记录 `errors` |
| 403/地区限制 | 停止，记录 URL 与时间 |
| 只有营销页、没有规格表 | 记 `not_found` 或跳过，不猜参数 |

失败时写：

```json
{"platform": "jd", "listing_id": "100001", "stage": "official", "message": "官网 403", "url": "..."}
```

官网失败不影响已有平台数据的评分，只是少一项参数证据。

## 评价采集

评价区通常有三块信息：总评分、评价数、首屏列表。

- 评分与评价数：直接读页面文本
- 首屏样本：10–20 条，按主题归类（噪音、漏光、做工、物流、售后……）
- 每个主题保留 1–2 条原话 `snippets`
- 只采当前商品；不要用"问大家"或推荐位内容冒充评价

样本量写 `sample_size`，报告会明确"样本口径，不做统计推断"。

采不到评价（页面无评价区/需要登录）→ `sample_size=0`，`status=missing`，不要写"暂无差评"。

## 登录、验证码与拦截

| 现象 | 判定 | 处理 |
|---|---|---|
| 跳转 passport/login 页面 | `login_required` | 请用户在浏览器窗口手动登录，然后继续 |
| "请完成安全验证"/滑块 | `captcha` | 停止，告知用户；不自动点击、不使用打码服务 |
| "暂时无法展示该商品的信息" | `blocked` | 停止，记录；不要重试刷页 |
| 403 / 访问受限 | `blocked` | 停止，记录 URL 与时间 |

遇到以上任一状态，**不要**换个 UA、注入 stealth 脚本或清 cookie 重试。正确做法是把状态写进 `errors`，让报告显示"该 listing 未采集"。

## 节奏与预算

- 同一平台串行：打开下一个页面前间隔 3–5 秒
- 单次采购最多展开 10 个 listing；每个 listing 最多展开 6 个配置
- 配置展开优先做"可能进前 5"的 listing，不要把所有 listing 的所有颜色都点一遍
- 与 `variant_sensitive` 无关的选项（例如仅颜色不同）可以合并，但要在 `notes` 写明合并了什么

## 上下文控制

`browser_snapshot` 返回的是可交互树，整页快照会非常长。建议：

- 搜索页：只取结果列表区域
- 商品页：先取价格/配置区，再取参数表，最后取评价区
- 结构化字段优先用 `browser_evaluate` 返回小对象，例如 `{priceText, selected, stock}`

## 写入采集文件

每完成一个 listing 就追加到 `raw_capture.json` 的 `captures[]`，不要等全部跑完再回忆。字段定义见 `references/capture-format.md`；价格、配置、评价都必须带 `snippet` 与 `source_url`。

中途失败也要写入 `errors[]`：

```json
{"platform": "jd", "listing_id": "100001", "stage": "variants", "message": "captcha", "url": "..."}
```
