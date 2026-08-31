#!/usr/bin/env python3
"""Add the licensed Sweetie Fox voice from ElevenLabs Voice Library and select it."""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx

from deploy.sync_elevenlabs_agent import load_env_file, request_json, required_env, write_env_values


VOICE_NAME = "Sweetie Fox - Smooth and Warm"


def select_voice(env_path: Path) -> str:
    load_env_file(env_path)
    api_key = required_env("ELEVENLABS_API_KEY")

    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        account_voices = request_json(client, "GET", "/voices", api_key=api_key)
        existing = next(
            (
                voice
                for voice in account_voices.get("voices", [])
                if isinstance(voice, dict) and voice.get("name") == VOICE_NAME
            ),
            None,
        )
        if existing and existing.get("voice_id"):
            voice_id = str(existing["voice_id"])
        else:
            library = request_json(
                client,
                "GET",
                "/shared-voices?search=Sweetie%20Fox&page_size=20",
                api_key=api_key,
            )
            shared = next(
                (
                    voice
                    for voice in library.get("voices", [])
                    if isinstance(voice, dict) and voice.get("name") == VOICE_NAME
                ),
                None,
            )
            if not shared:
                raise RuntimeError(f"Голос {VOICE_NAME} не найден в Voice Library")

            owner_id = str(shared.get("public_owner_id") or "")
            source_voice_id = str(shared.get("voice_id") or "")
            if not owner_id or not source_voice_id:
                raise RuntimeError("Voice Library не вернула идентификаторы Sweetie Fox")
            created = request_json(
                client,
                "POST",
                f"/voices/add/{owner_id}/{source_voice_id}",
                api_key=api_key,
                json_body={"new_name": VOICE_NAME},
            )
            voice_id = str(created.get("voice_id") or "")
            if not voice_id:
                raise RuntimeError("ElevenLabs не вернул ID добавленного голоса")

    write_env_values(env_path, {"ELEVENLABS_VOICE_ID": voice_id})
    print("Sweetie Fox selected")
    return voice_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Выбрать Sweetie Fox для NAMI")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args()
    select_voice(args.env)


if __name__ == "__main__":
    main()
