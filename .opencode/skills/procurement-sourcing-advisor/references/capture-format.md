# 采集格式（raw_capture.json）

这是 AI 从浏览器采集到计算层之间**唯一**的输入契约。结构不对，`validate_capture.py` 会直接拒绝，流水线不会启动。

## 顶层

```json
{
  "captures": [ /* 每个 listing 一条 */ ],
  "errors": [ /* 采集中断也要记录 */ ]
}
```

## listing（capture）

```json
{
  "platform": "jd",
  "listing_id": "100001",
  "url": "https://item.jd.com/100001.html",
  "title": "联想 ThinkPad T14 Gen 5 14英寸 商务轻薄本",
  "shop": "联想京东自营旗舰店",
  "shop_type": "自营",
  "sales": "2万+",
  "ad": false,
  "captured_at": "2026-10-29T09:00:00+08:00",
  "page_state": "ok",
  "variants": [],
  "reviews": {},
  "notes": []
}
```

- `page_state`：`ok` / `login_required` / `captcha` / `blocked`。非 `ok` 不会被计算层接受。
- `ad`：结果卡片带"广告"标记时为 `true`，后续硬过滤淘汰。
- `listing_id`：京东 sku、淘宝 item id；从 URL 中取。

## variant（配置）

```json
{
  "variant_id": "100001",
  "label": "Ultra 5 / 16GB / 1TB",
  "option_path": ["Ultra 5", "16GB", "1TB"],
  "price": 5499,
  "price_text": "¥5,499.00",
  "price_status": "visible",
  "list_price": "6299",
  "stock_status": "现货",
  "specs": {
    "CPU": "Intel Core Ultra 5 125U",
    "内存": "16GB",
    "硬盘": "1TB",
    "保修": "3年",
    "发票": "可开增值税专用发票"
  },
  "evidence": [
    {
      "field": "price",
      "value": 5499,
      "snippet": "¥5,499.00 已选：Ultra 5 / 16GB / 1TB",
      "source_url": "https://item.jd.com/100001.html",
      "captured_at": "2026-10-29T09:00:00+08:00",
      "confidence": 0.9
    }
  ]
}
```

硬性要求：

| 字段 | 要求 | 原因 |
|---|---|---|
| `variant_id` | 同 listing 内唯一；没有 SKU id 时用 `listing_id-<配置哈希>` | 候选主键 |
| `label` | 人能看懂的配置名 | 报告与 CSV 展示 |
| `option_path` | 非空，且是**实际点选过**的选项链 | 证明不是默认价 |
| `price` / `price_text` | 至少一个；隐藏则 `price_status=hidden` | 价格可审计 |
| `evidence` | 至少一条，`field=price` 必须带 `snippet` 与 `source_url` | 价格是证据，不是记忆 |
| `specs` | 用页面原始键名即可，归一化会映射 | 评分与硬过滤依据 |

`price_status` 取值：

- `visible`：选中配置后页面显示明确价格
- `hidden`：登录后可见、面议、需咨询
- `unknown`：页面结构异常，没读到

> 价格缺失时**不要**填 0、不要用"起"价、不要用同 listing 其他配置的价格。

## sections（页面分区）

```json
"sections": {
  "params": {"面板": "IPS技术", "分辨率": "2560*1440", "支架底座": "旋转升降"},
  "after_sales": "五年质保 · 京东发货&售后服务",
  "usage": "支持 7×24 小时运行，建议置于通风位置",
  "shop": {"name": "东芝存储京东自营旗舰店", "type": "自营"},
  "qa": [{"question": "能用在群晖上吗？", "answer": "可以，SATA 接口通用"}],
  "recommendations": ["同系列 16TB", "搭配硬盘盒"]
}
```

- `params` 会合并进 `attributes`，并与 `variant.specs` 一起作为规格证据
- `after_sales` 只作上下文，**不会**自动变成保修承诺；保修年限必须来自明确的保修字段或参数表
- `recommendations` 只做参考，**不会**变成候选

## 官网参数（official，可选）

当商品有明确型号/认证型号/MPN 时，可以打开厂商官网核对参数。官网块放在 `capture.official`（整条 listing 共用型号）或 `variant.official`（每个配置型号不同）。**`variant.official` 优先于 `capture.official`。**

```json
"official": {
  "manufacturer": "Lenovo",
  "model": "ThinkPad T14 Gen 5 21ML",
  "region": "CN",
  "url": "https://www.lenovo.com.cn/...",
  "captured_at": "2026-10-30T10:00:00+08:00",
  "match_status": "exact",
  "matched_text": "ThinkPad T14 Gen 5 (21ML) ...",
  "specs": {
    "CPU": "Intel Core Ultra 5 125U",
    "接口": "Thunderbolt 4 ×2 / USB-A ×2 / HDMI 2.1",
    "重量": "1.31kg",
    "保修": "3年"
  },
  "notes": ["国行页面；保修主体以下单渠道为准"],
  "evidence": [
    {
      "field": "official.specs",
      "snippet": "Thunderbolt 4 ×2 / USB-A ×2 / HDMI 2.1",
      "source_url": "https://www.lenovo.com.cn/...",
      "captured_at": "2026-10-30T10:00:00+08:00",
      "confidence": 0.85
    }
  ]
}
```

规则：

| 字段/状态 | 要求 |
|---|---|
| `match_status=exact` | 页面标题或规格表必须能对上**完整型号**；必须有 `matched_text`、非空 `specs`、至少一条带 `snippet + source_url` 的证据 |
| `match_status=family` | 只对上系列名，参数仅作记录，**不进入评分** |
| `match_status=not_found` | 官网未找到同型号；只记录尝试，不写参数 |
| `url` | 必须是厂商官方域名，不能是京东/淘宝/天猫页面 |
| `specs` | 只能放技术参数，**不得**出现价格、售价、库存、促销字段 |
| `evidence` | 只能用于规格；禁止 `price` / `stock_status` 等字段 |

归一化时，官网参数只补商品页缺失的字段；商品页已有值时保留商品页值，冲突写入 `official.conflicts` 并给出 warning。官网价格、库存、促销永远不会进入候选价格、价格锚点或排序。

## 早停（aborted）

命中简报排除项时，不删除已采集内容，而是标记：

```json
"aborted": true,
"abort_reason": "标题含『二手』，命中排除项"
```

这类 listing 仍会出现在报告的"淘汰与风险"里，但不参与评分与比价。

## 同一链接多个合格配置

`variants[]` 里每个配置都是独立候选。只要各自满足硬条件，就都保留、都参与评分；不要为了"一个链接一个结果"而只留一个。报告里的"同链接配置对比"会把它们放在一张表里。

## reviews（评价）

```json
{
  "rating": 4.8,
  "rating_count": 20000,
  "sample_size": 20,
  "negative_themes": [
    {"theme": "风扇噪音", "count": 3, "snippets": ["高负载时风扇声音明显"]}
  ],
  "positive_themes": [
    {"theme": "键盘手感", "count": 5, "snippets": ["键盘手感很好"]}
  ],
  "evidence": [
    {
      "field": "reviews",
      "snippet": "4.8 分，2万+条评价，高负载时风扇声音明显",
      "source_url": "https://item.jd.com/100001.html#comment",
      "captured_at": "2026-10-29T09:05:00+08:00",
      "confidence": 0.8
    }
  ]
}
```

规则：

- `sample_size` 是**实际读过的评价条数**，不是评价总数
- 主题 `count` 是该主题在样本中出现的次数，不是全量统计
- 没有采样就 `sample_size=0`，整块可以省略
- 负评主题必须有原话，否则不成立

## errors

```json
{"platform": "jd", "listing_id": "100001", "stage": "variants", "message": "captcha", "url": "..."}
```

`stage`：`search` / `listing` / `variants` / `reviews` / `official` / `navigation`。

## 校验会拒绝什么

- 价格 `visible` 但没有 `price` snippet
- `option_path` 为空
- 同一 listing 内 `variant_id` 重复
- `page_state` 不是 `ok`
- 同一 listing 重复出现（应合并为一条 capture）
- 有评价样本但 `reviews.evidence` 为空
- 官网 `match_status=exact` 但缺 `matched_text` / `specs` / 证据
- 官网参数或证据里出现价格/库存字段
- 官网 URL 指向京东/淘宝/天猫
