"""
hoger/mcp_server/config_gen.py — Generate MCP server configuration.

Provides functions to build and persist MCP configuration:
- build_mcp_config(): Assemble stdio + http config dict
- write_mcp_config_snippet(): Write config snippets to JSON files
"""

import json
from pathlib import Path

from hoger import config


def build_mcp_config() -> dict:
    """
    Build MCP server configuration dict with stdio and http transports.

    Returns:
        dict with keys "stdio" and "http", each containing mcpServers config.
        Matches the structure expected by Claude Desktop and HTTP clients.

    Note:
        Windows venv layout (.venv/Scripts/python.exe); assumes Windows target.
        For cross-platform support, would need to detect OS and use
        .venv/bin/python on POSIX systems.
    """
    venv_python = str(config.ROOT / ".venv" / "Scripts" / "python.exe")

    return {
        "stdio": {
            "mcpServers": {
                "hoger": {
                    "command": venv_python,
                    "args": ["-m", "hoger.mcp_server.stdio_main"],
                    "cwd": str(config.ROOT),
                    "env": {"HOGER_COMPUTE_URL": config.COMPUTE_URL},
                }
            }
        },
        "http": {
            "mcpServers": {
                "hoger": {"url": f"http://localhost:{config.HOGER_PORT}/mcp"},
            }
        },
    }


def write_mcp_config_snippet(out_dir=None) -> str:
    """
    Write MCP configuration snippets to files for easy user access.

    Writes two JSON files to out_dir:
    - claude_desktop_config.snippet.json: Contains stdio config for Claude Desktop
    - http_client_config.snippet.json: Contains http config for generic HTTP clients

    Args:
        out_dir: Output directory path. If None, defaults to
                 config.ROOT / "generated" / "mcp_config"

    Returns:
        str: Absolute path to the output directory
    """
    if out_dir is None:
        out_dir = config.ROOT / "generated" / "mcp_config"
    else:
        out_dir = Path(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    mcp_config = build_mcp_config()

    # Write stdio config (for Claude Desktop)
    stdio_file = out_dir / "claude_desktop_config.snippet.json"
    stdio_file.write_text(
        json.dumps(mcp_config["stdio"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Write http config (for generic HTTP clients)
    http_file = out_dir / "http_client_config.snippet.json"
    http_file.write_text(
        json.dumps(mcp_config["http"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return str(out_dir.resolve())
