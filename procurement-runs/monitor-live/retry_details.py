"""Retry failed detail calls once and merge into raw_details.json."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

WS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
path = WS / "raw_details.json"
payload = json.loads(path.read_text(encoding="utf-8"))
failed = payload.get("errors", [])

RETRY = [item for item in failed if item.get("error", {}).get("retryable")]
if not RETRY:
    print("nothing to retry")
    raise SystemExit(0)


async def main() -> None:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "cn_scraper_mcp.server"],
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    remaining = [item for item in failed if item not in RETRY]
    async with Client(transport) as client:
        for item in RETRY:
            platform, product_id = item["platform"], item["product_id"]
            tool = "taobao_product" if platform == "taobao" else "jd_product"
            args = {"item_id": product_id} if platform == "taobao" else {"sku": product_id}
            try:
                result = await client.call_tool(tool, args)
                data = result.data
            except Exception as exc:  # noqa: BLE001
                remaining.append({**item, "error": {"repr": repr(exc)}})
                print(f"[ERR] {platform} {product_id}: {exc}")
                continue
            if isinstance(data, dict) and data.get("ok") is False:
                remaining.append({**item, "error": data.get("error")})
                print(f"[ERR] {platform} {product_id}: {data.get('error')}")
                continue
            payload["details"].append({"platform": platform, "product_id": product_id, "result": data})
            print(f"[OK] {platform} {product_id}: price={data.get('price')}")

    payload["errors"] = remaining
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"details={len(payload['details'])} errors={len(remaining)}")


asyncio.run(main())
