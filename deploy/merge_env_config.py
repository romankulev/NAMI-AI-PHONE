#!/usr/bin/env python3
import argparse
import os
import re
import tempfile
from pathlib import Path


KEY_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=")
PRESERVED_KEYS = {
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_AGENT_ID",
    "ELEVENLABS_MCP_SERVER_IDS",
    "ELEVENLABS_VOICE_ID",
    "OPENAI_API_KEY",
}
REMOVED_KEYS = {"REALTIME_INSTRUCTIONS"}


def key_from_line(line: str) -> str | None:
    match = KEY_PATTERN.match(line)
    return match.group(1) if match else None


def load_updates(template_path: Path) -> dict[str, str]:
    updates: dict[str, str] = {}
    for line in template_path.read_text(encoding="utf-8").splitlines():
        key = key_from_line(line)
        if key and key not in PRESERVED_KEYS:
            updates[key] = line
    return updates


def merge_env(env_path: Path, template_path: Path) -> None:
    updates = load_updates(template_path)
    current_lines = (
        env_path.read_text(encoding="utf-8").splitlines()
        if env_path.exists()
        else []
    )
    merged_lines: list[str] = []
    applied: set[str] = set()

    for line in current_lines:
        key = key_from_line(line)
        if key in REMOVED_KEYS:
            continue
        if key in updates:
            if key not in applied:
                merged_lines.append(updates[key])
                applied.add(key)
            continue
        merged_lines.append(line)

    for key, line in updates.items():
        if key not in applied:
            merged_lines.append(line)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=env_path.parent,
        prefix=".env.",
        delete=False,
    ) as temp_file:
        temp_file.write("\n".join(merged_lines) + "\n")
        temp_path = Path(temp_file.name)

    os.chmod(temp_path, 0o600)
    os.replace(temp_path, env_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    args = parser.parse_args()
    merge_env(args.env, args.template)


if __name__ == "__main__":
    main()
