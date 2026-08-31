#!/usr/bin/env python3
"""Exercise the live NAMI agent over ElevenLabs text chat with exact inputs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
from websockets.asyncio.client import connect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deploy.sync_elevenlabs_agent import load_env_file, required_env


API_BASE = "https://api.elevenlabs.io/v1"
SOFT_TIMEOUT_MESSAGES = {
    "сейчас сориентирую.",
    "секунду, подбираю вариант.",
    "смотрю, что вам подойдёт.",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    message: str


SCENARIOS = (
    Scenario("date_without_month", "Хочу маникюр 28-го."),
    Scenario("elena_at_twelve", "К мастеру Елене на 12 часов можно?"),
    Scenario("price_objection", "Дороговато. А скидка есть?"),
    Scenario("unknown_service_comparison", "Чем отличаются ногти LMS и маникюр M?"),
    Scenario("light_joke", "Ногти в таком состоянии, что им пора в отпуск, ха-ха."),
    Scenario("hesitation", "Я пока просто смотрю, записываться не готова."),
    Scenario("after_work", "Нужен педикюр завтра после работы, примерно в 19:00."),
    Scenario("minimal_context", "На 17:00 можно?"),
    Scenario("medical_boundary", "Палец воспалился. Маникюр поможет?"),
    Scenario("direct_booking", "Запиши меня прямо сейчас на ближайшее окошко."),
)


def signed_url(client: httpx.Client, api_key: str, agent_id: str) -> str:
    response = client.get(
        f"{API_BASE}/convai/conversation/get-signed-url",
        headers={"xi-api-key": api_key},
        params={"agent_id": agent_id},
    )
    response.raise_for_status()
    url = response.json().get("signed_url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("ElevenLabs did not return a signed URL")
    return url


async def receive_reply(websocket: Any, timeout_seconds: float) -> tuple[str, float]:
    """Return one text reply and latency to the first text chunk."""
    started = monotonic()
    first_chunk_at: float | None = None
    chunks: list[str] = []
    fallback_reply = ""
    fallback_deadline: float | None = None
    while True:
        deadline = started + timeout_seconds
        if fallback_deadline is not None:
            deadline = min(deadline, fallback_deadline)
        remaining = deadline - monotonic()
        if remaining <= 0:
            if fallback_reply:
                return fallback_reply, (first_chunk_at or monotonic()) - started
            raise TimeoutError("Timed out waiting for the agent reply")
        try:
            raw_event = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except TimeoutError:
            if fallback_reply:
                return fallback_reply, (first_chunk_at or monotonic()) - started
            raise
        event = json.loads(raw_event)
        if event.get("type") == "ping":
            await websocket.send(
                json.dumps({"type": "pong", "event_id": event["ping_event"]["event_id"]})
            )
            continue
        if event.get("type") == "agent_chat_response_part":
            part = event.get("text_response_part") or {}
            if part.get("type") == "delta":
                if first_chunk_at is None:
                    first_chunk_at = monotonic()
                chunks.append(str(part.get("text") or ""))
            if part.get("type") == "stop":
                reply = "".join(chunks).strip()
                if not reply:
                    chunks = []
                    continue
                if reply.lower() not in SOFT_TIMEOUT_MESSAGES:
                    return reply, (first_chunk_at or monotonic()) - started
                fallback_reply = reply
                fallback_deadline = monotonic() + 8.0
                chunks = []
            continue
        if event.get("type") == "agent_response":
            text = str((event.get("agent_response_event") or {}).get("agent_response") or "")
            if text:
                return text, (first_chunk_at or monotonic()) - started
        if event.get("type") == "error":
            raise RuntimeError(str(event))


async def run_scenario(url: str, scenario: Scenario, timeout_seconds: float) -> tuple[str, float]:
    async with connect(url, open_timeout=timeout_seconds) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "conversation_initiation_client_data",
                    "conversation_config_override": {"conversation": {"text_only": True}},
                }
            )
        )
        # Consume the configured greeting before measuring the actual user turn.
        await receive_reply(websocket, timeout_seconds)
        await websocket.send(json.dumps({"type": "user_message", "text": scenario.message}))
        return await receive_reply(websocket, timeout_seconds)


async def main_async(args: argparse.Namespace) -> None:
    load_env_file(args.env)
    api_key = required_env("ELEVENLABS_API_KEY")
    agent_id = required_env("ELEVENLABS_AGENT_ID")
    with httpx.Client(timeout=httpx.Timeout(args.timeout)) as client:
        for scenario in SCENARIOS:
            if args.only and scenario.name != args.only:
                continue
            reply, first_chunk_seconds = await run_scenario(
                signed_url(client, api_key, agent_id), scenario, args.timeout
            )
            print(f"{scenario.name}: ttfb={first_chunk_seconds:.2f}s")
            print(f"  reply: {reply[:700] or '[no text reply]'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run exact text-chat NAMI scenarios")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--only", choices=[scenario.name for scenario in SCENARIOS])
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
