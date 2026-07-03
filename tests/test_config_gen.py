"""
tests/test_config_gen.py — Test MCP config generation (build and file writing).

Tests for hoger.mcp_server.config_gen module:
- build_mcp_config(): Returns dict with proper stdio/http structure
- write_mcp_config_snippet(): Writes JSON snippet files to disk
- Integration: API endpoint calls write_mcp_config_snippet and files exist
"""

import json
from pathlib import Path

import pytest

from hoger import config
from hoger.mcp_server import config_gen


def test_build_mcp_config_structure():
    """build_mcp_config() returns dict with stdio and http keys."""
    result = config_gen.build_mcp_config()

    assert isinstance(result, dict)
    assert "stdio" in result
    assert "http" in result
    assert isinstance(result["stdio"], dict)
    assert isinstance(result["http"], dict)


def test_build_mcp_config_stdio_hoger_server():
    """stdio.mcpServers.hoger has command (with .venv), args, cwd, env."""
    result = config_gen.build_mcp_config()

    stdio_hoger = result["stdio"]["mcpServers"]["hoger"]

    # command should contain venv python path
    assert "command" in stdio_hoger
    assert ".venv" in stdio_hoger["command"]
    assert "Scripts" in stdio_hoger["command"] or "bin" in stdio_hoger["command"]

    # args should be ["<-m", "hoger.mcp_server.stdio_main"]
    assert "args" in stdio_hoger
    assert isinstance(stdio_hoger["args"], list)
    assert "-m" in stdio_hoger["args"]
    assert "hoger.mcp_server.stdio_main" in stdio_hoger["args"]

    # cwd should be project ROOT
    assert "cwd" in stdio_hoger

    # env should have HOGER_COMPUTE_URL
    assert "env" in stdio_hoger
    assert isinstance(stdio_hoger["env"], dict)
    assert "HOGER_COMPUTE_URL" in stdio_hoger["env"]


def test_build_mcp_config_http_url_ends_with_mcp():
    """http.mcpServers.hoger.url ends with /mcp."""
    result = config_gen.build_mcp_config()

    http_hoger = result["http"]["mcpServers"]["hoger"]
    assert "url" in http_hoger
    assert http_hoger["url"].endswith("/mcp")


def test_write_mcp_config_snippet_creates_files(tmp_path):
    """write_mcp_config_snippet() creates claude_desktop_config and http_client_config files."""
    out_dir = config_gen.write_mcp_config_snippet(out_dir=tmp_path)

    assert Path(out_dir).exists()
    assert Path(out_dir).is_absolute()

    stdio_file = Path(out_dir) / "claude_desktop_config.snippet.json"
    http_file = Path(out_dir) / "http_client_config.snippet.json"

    assert stdio_file.exists()
    assert http_file.exists()


def test_write_mcp_config_snippet_valid_json(tmp_path):
    """Both output files contain valid JSON."""
    config_gen.write_mcp_config_snippet(out_dir=tmp_path)

    stdio_file = tmp_path / "claude_desktop_config.snippet.json"
    http_file = tmp_path / "http_client_config.snippet.json"

    stdio_data = json.loads(stdio_file.read_text())
    http_data = json.loads(http_file.read_text())

    assert isinstance(stdio_data, dict)
    assert isinstance(http_data, dict)


def test_write_mcp_config_snippet_content_matches_build_config(tmp_path):
    """Snippet content matches build_mcp_config() result."""
    mcp_config = config_gen.build_mcp_config()
    config_gen.write_mcp_config_snippet(out_dir=tmp_path)

    stdio_file = tmp_path / "claude_desktop_config.snippet.json"
    http_file = tmp_path / "http_client_config.snippet.json"

    stdio_data = json.loads(stdio_file.read_text())
    http_data = json.loads(http_file.read_text())

    # stdio snippet should match build_mcp_config()["stdio"]
    assert stdio_data == mcp_config["stdio"]

    # http snippet should match build_mcp_config()["http"]
    assert http_data == mcp_config["http"]


def test_write_mcp_config_snippet_default_out_dir(tmp_path, monkeypatch):
    """
    write_mcp_config_snippet() with out_dir=None defaults to
    config.ROOT / "generated" / "mcp_config".
    """
    # Monkeypatch config.ROOT to tmp_path so generated dir is in our temp area
    monkeypatch.setattr("hoger.config.ROOT", tmp_path)

    result = config_gen.write_mcp_config_snippet(out_dir=None)

    expected_dir = tmp_path / "generated" / "mcp_config"
    assert Path(result) == expected_dir
    assert (expected_dir / "claude_desktop_config.snippet.json").exists()
    assert (expected_dir / "http_client_config.snippet.json").exists()


def test_write_mcp_config_snippet_json_formatting(tmp_path):
    """
    JSON files are written with indent=2 and ensure_ascii=False
    (human-readable, UTF-8 safe).
    """
    config_gen.write_mcp_config_snippet(out_dir=tmp_path)

    stdio_file = tmp_path / "claude_desktop_config.snippet.json"
    content = stdio_file.read_text(encoding="utf-8")

    # indent=2 means lines start with 2-space indentation
    assert "  " in content  # should have indented lines
    # ensure_ascii=False allows non-ASCII in the output


def test_api_mcp_config_endpoint_writes_snippets(monkeypatch, tmp_path):
    """
    GET /api/mcp-config endpoint calls write_mcp_config_snippet() and
    files are created in the monkeypatched directory.
    """
    from fastapi.testclient import TestClient
    from hoger.api.app import app

    # Monkeypatch config.ROOT so generated files go to tmp_path
    monkeypatch.setattr("hoger.config.ROOT", tmp_path)
    monkeypatch.setattr("hoger.api.routes.ROOT", tmp_path)

    client = TestClient(app)
    resp = client.get("/api/mcp-config")

    assert resp.status_code == 200

    # After the call, snippet files should exist
    expected_dir = tmp_path / "generated" / "mcp_config"
    assert (expected_dir / "claude_desktop_config.snippet.json").exists()
    assert (expected_dir / "http_client_config.snippet.json").exists()


def test_api_mcp_config_response_unchanged(monkeypatch, tmp_path):
    """
    GET /api/mcp-config endpoint response is unchanged from before
    (still returns stdio + http dict).
    """
    from fastapi.testclient import TestClient
    from hoger.api.app import app

    monkeypatch.setattr("hoger.config.ROOT", tmp_path)
    monkeypatch.setattr("hoger.api.routes.ROOT", tmp_path)

    client = TestClient(app)
    resp = client.get("/api/mcp-config")

    assert resp.status_code == 200
    data = resp.json()

    # Response still has stdio and http
    assert "stdio" in data
    assert "http" in data
    assert "mcpServers" in data["stdio"]
    assert "mcpServers" in data["http"]
    assert "hoger" in data["stdio"]["mcpServers"]
    assert "hoger" in data["http"]["mcpServers"]
