#!/usr/bin/env python3
"""Set the public NAMI MCP descriptor without printing or touching secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path


ENV_PATH = Path(os.environ.get("MINIAPP_ENV_PATH", "/home/roman/apps/openai-realtime-miniapp/.env"))
MCP_SERVERS = [
    {
        "type": "mcp",
        "server_label": "nami_booking",
        "server_url": "https://n8n-netherlands.nl.tuna.am/mcp/a6568945-7da1-4a3b-a32a-d8c67cd86615",
        "allowed_tools": [
            "nami_current_datetime",
            "nami_get_services",
            "nami_get_staff_for_service",
            "nami_get_available_dates",
            "nami_get_available_times",
            "nami_check_slot",
        ],
        "require_approval": "never",
    }
]


def main() -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    value = json.dumps(MCP_SERVERS, ensure_ascii=False, separators=(",", ":"))
    replacements = {
        "MCP_SERVERS_JSON": f"MCP_SERVERS_JSON='{value}'",
    }
    output: list[str] = []
    replaced: set[str] = set()

    for line in lines:
        key = line.partition("=")[0]
        if key in replacements:
            if key not in replaced:
                output.append(replacements[key])
                replaced.add(key)
            continue
        output.append(line)

    for key, replacement in replacements.items():
        if key not in replaced:
            output.append(replacement)

    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)
    print("NAMI MCP configuration updated")


if __name__ == "__main__":
    main()
