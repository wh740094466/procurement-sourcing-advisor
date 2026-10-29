"""Live collector: run Taobao/JD searches through cn-scraper-mcp and save raw payloads.

This is a thin MCP client (not a scraper): every platform request goes through the
cn-scraper MCP server, exactly like the agent would call the tools directly.
JD tool calls are strictly serial because the server serialises them with BrowserLock.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

WS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
QUERIES = json.loads((WS / "queries.json").read_text(encoding="utf-8"))


async def main() -> None:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "cn_scraper_mcp.server"],
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    searches: list[dict] = []
    errors: list[dict] = []
    async with Client(transport) as client:
        for platform, keywords in QUERIES.items():
            for keyword in keywords:
                tool = "taobao_search" if platform == "taobao" else "jd_search"
                try:
                    result = await client.call_tool(tool, {"keyword": keyword, "limit": 20})
                    data = result.data
                except Exception as exc:  # noqa: BLE001 - record and continue
                    errors.append({"platform": platform, "keyword": keyword, "error": repr(exc)})
                    print(f"[ERR] {platform} {keyword}: {exc}")
                    continue
                if isinstance(data, dict) and data.get("ok") is False:
                    errors.append({"platform": platform, "keyword": keyword, "error": data.get("error")})
                    print(f"[ERR] {platform} {keyword}: {data.get('error')}")
                    continue
                searches.append({"platform": platform, "keyword": keyword, "result": data})
                count = len(data.get("items") or [])
                print(f"[OK] {platform} {keyword}: {count} items")

    (WS / "raw_searches.json").write_text(
        json.dumps({"searches": searches, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved {len(searches)} searches, {len(errors)} errors -> {WS / 'raw_searches.json'}")


asyncio.run(main())
