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

`stage`：`search` / `listing` / `variants` / `reviews` / `navigation`。

## 校验会拒绝什么

- 价格 `visible` 但没有 `price` snippet
- `option_path` 为空
- 同一 listing 内 `variant_id` 重复
- `page_state` 不是 `ok`
- 同一 listing 重复出现（应合并为一条 capture）
- 有评价样本但 `reviews.evidence` 为空
