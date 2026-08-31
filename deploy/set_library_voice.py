#!/usr/bin/env python3
"""Add a Voice Library voice and configure optional temporary persona text."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.elevenlabs_config import required_env
from deploy.sync_elevenlabs_agent import load_env_file, request_json, write_env_values


def select_voice(env_path: Path, source_voice_id: str) -> str:
    load_env_file(env_path)
    api_key = required_env("ELEVENLABS_API_KEY")

    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        library = request_json(
            client,
            "GET",
            f"/shared-voices?search={source_voice_id}&page_size=20",
            api_key=api_key,
        )
        shared = next(
            (
                voice
                for voice in library.get("voices", [])
                if isinstance(voice, dict) and voice.get("voice_id") == source_voice_id
            ),
            None,
        )
        if not shared:
            raise RuntimeError("Указанный Voice ID не найден в Voice Library")

        account = request_json(client, "GET", "/voices", api_key=api_key)
        existing = next(
            (
                voice
                for voice in account.get("voices", [])
                if isinstance(voice, dict) and voice.get("name") == shared.get("name")
            ),
            None,
        )
        if existing and existing.get("voice_id"):
            return str(existing["voice_id"])

        owner_id = str(shared.get("public_owner_id") or "")
        if not owner_id:
            raise RuntimeError("Voice Library не вернула владельца голоса")
        added = request_json(
            client,
            "POST",
            f"/voices/add/{owner_id}/{source_voice_id}",
            api_key=api_key,
            json_body={"new_name": str(shared.get("name") or "Voice Library voice")},
        )
        voice_id = str(added.get("voice_id") or "")
        if not voice_id:
            raise RuntimeError("ElevenLabs не вернул ID добавленного голоса")
        return voice_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Выбрать голос из ElevenLabs Voice Library")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--voice-id", required=True)
    parser.add_argument(
        "--test-persona",
        help="Временный стиль; записывается в NAMI_TEST_PERSONA_PROMPT",
    )
    args = parser.parse_args()

    voice_id = select_voice(args.env, args.voice_id)
    updates = {"ELEVENLABS_VOICE_ID": voice_id}
    if args.test_persona is not None:
        updates["NAMI_TEST_PERSONA_PROMPT"] = args.test_persona
    write_env_values(args.env, updates)
    print("Voice Library voice selected")


if __name__ == "__main__":
    main()
