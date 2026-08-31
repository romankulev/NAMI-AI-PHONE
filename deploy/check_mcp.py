#!/usr/bin/env python3
"""Smoke-test the public NAMI MCP endpoint without displaying business data."""

from __future__ import annotations

import json
import urllib.request


URL = "https://n8n-netherlands.nl.tuna.am/mcp/a6568945-7da1-4a3b-a32a-d8c67cd86615"
BASE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def post(payload: dict, session_id: str | None = None) -> tuple[dict, str | None]:
    headers = dict(BASE_HEADERS)
    if session_id:
        headers["mcp-session-id"] = session_id
    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        next_session = response.headers.get("mcp-session-id") or session_id

    data_lines = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    parsed = json.loads(data_lines[-1] if data_lines else body) if body.strip() else {}
    return parsed, next_session


def main() -> None:
    initialized, session_id = post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "nami-smoke-test", "version": "1.0"},
            },
        }
    )
    if "error" in initialized or not session_id:
        raise RuntimeError("MCP initialize failed")

    post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, session_id)
    listed, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session_id)
    names = [tool["name"] for tool in listed.get("result", {}).get("tools", [])]
    if "nami_get_services" not in names:
        raise RuntimeError("Expected NAMI tool is missing")

    called, _ = post(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "nami_get_services", "arguments": {}},
        },
        session_id,
    )
    if "error" in called or called.get("result", {}).get("isError"):
        details = json.dumps(called, ensure_ascii=False)
        raise RuntimeError(f"nami_get_services failed: {details[:1200]}")

    current_time, _ = post(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nami_current_datetime", "arguments": {}},
        },
        session_id,
    )
    if "error" in current_time or current_time.get("result", {}).get("isError"):
        raise RuntimeError("nami_current_datetime failed")

    print(
        f"MCP OK: {len(names)} tools; services and current datetime returned successfully"
    )


if __name__ == "__main__":
    main()
