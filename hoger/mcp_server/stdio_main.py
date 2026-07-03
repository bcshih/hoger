"""HOGER MCP Server — stdio 入口（Claude Desktop 等用）。

用法：python -m hoger.mcp_server.stdio_main
stdout 完全保留給 JSON-RPC；所有 log 導向 stderr。

stdio_server()（mcp.server.stdio）內部自行把 sys.stdin.buffer / sys.stdout.buffer
包成 UTF-8 text stream 使用，不會、也不需要我們去 reconfigure sys.stdout 的
encoding——它自己的 TextIOWrapper 是獨立物件，不會動到全域 sys.stdout。
"""

import asyncio
import logging
import sys

from mcp.server.stdio import stdio_server

from hoger.mcp_server.server import server


def _setup_logging() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


async def _amain() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    _setup_logging()
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
