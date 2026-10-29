import json
from pathlib import Path

ws = Path(__file__).parent
payload = json.loads((ws / "raw_searches.json").read_text(encoding="utf-8"))
seen = set()
for search in payload["searches"]:
    platform = search["platform"]
    items = (search["result"].get("items") or [])
    print(f"== {platform} / {search['keyword']} ({len(items)})")
    for item in items:
        pid = str(item.get("id") or item.get("sku"))
        if (platform, pid) in seen:
            continue
        seen.add((platform, pid))
        price = item.get("price")
        ad = " AD" if item.get("ad") else ""
        print(f"  {pid:>14} | {str(price):>8} | {item.get('title') or item.get('name')}{ad}")
