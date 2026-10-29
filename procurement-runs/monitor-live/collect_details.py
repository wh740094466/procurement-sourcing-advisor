"""Fetch product details for the shortlisted candidates via cn-scraper-mcp."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

WS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
TARGETS = json.loads((WS / "details_targets.json").read_text(encoding="utf-8"))


async def main() -> None:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "cn_scraper_mcp.server"],
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    details: list[dict] = []
    errors: list[dict] = []
    async with Client(transport) as client:
        for platform, ids in TARGETS.items():
            for product_id in ids:
                tool = "taobao_product" if platform == "taobao" else "jd_product"
                args = {"item_id": product_id} if platform == "taobao" else {"sku": product_id}
                try:
                    result = await client.call_tool(tool, args)
                    data = result.data
                except Exception as exc:  # noqa: BLE001
                    errors.append({"platform": platform, "product_id": product_id, "error": repr(exc)})
                    print(f"[ERR] {platform} {product_id}: {exc}")
                    continue
                if isinstance(data, dict) and data.get("ok") is False:
                    errors.append({"platform": platform, "product_id": product_id, "error": data.get("error")})
                    print(f"[ERR] {platform} {product_id}: {data.get('error')}")
                    continue
                details.append({"platform": platform, "product_id": product_id, "result": data})
                price = data.get("price")
                print(f"[OK] {platform} {product_id}: price={price} specs={'yes' if data.get('specs') else 'no'}")

    (WS / "raw_details.json").write_text(
        json.dumps({"details": details, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved {len(details)} details, {len(errors)} errors -> {WS / 'raw_details.json'}")


asyncio.run(main())
