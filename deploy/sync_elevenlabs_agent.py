#!/usr/bin/env python3
"""Create or update the private NAMI ElevenLabs agent entirely through the API."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.elevenlabs_config import (
    ConfigurationError,
    build_agent_payload,
    env_int,
    load_mcp_definitions,
    required_env,
)


API_BASE = "https://api.elevenlabs.io/v1"
ENV_KEY_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=")


class ElevenLabsAPIError(RuntimeError):
    pass


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:2_000] or "неизвестная ошибка"
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)[:2_000]
        return str(detail)[:2_000]
    return str(body)[:2_000]


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    api_key: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.request(
        method,
        f"{API_BASE}{path}",
        headers={"xi-api-key": api_key},
        json=json_body,
    )
    if not response.is_success:
        raise ElevenLabsAPIError(
            f"ElevenLabs API {method} {path}: {response.status_code}: "
            f"{response_detail(response)}"
        )
    body = response.json()
    if not isinstance(body, dict):
        raise ElevenLabsAPIError("ElevenLabs API вернул неожиданный формат ответа")
    return body


def normalize_model_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def resolve_llm_model(client: httpx.Client, api_key: str) -> str:
    requested = os.getenv("ELEVENLABS_LLM_MODEL", "gpt-5.4-mini").strip()
    body = request_json(client, "GET", "/convai/llm/list", api_key=api_key)
    model_ids = [
        item.get("llm")
        for item in body.get("llms", [])
        if isinstance(item, dict) and isinstance(item.get("llm"), str)
    ]
    if requested in model_ids:
        return requested

    normalized_requested = normalize_model_id(requested)
    for model_id in model_ids:
        if normalize_model_id(model_id) == normalized_requested:
            return model_id

    # Newer workspaces do not always expose every hosted model at once.  Keep the
    # agent on an OpenAI reasoning model instead of silently falling back to Qwen
    # when the preferred conversational model is not enabled in this workspace.
    if requested.lower().startswith("gpt-5"):
        for candidate in (
            "gpt-5.4-mini",
            "gpt-5-nano",
            "gpt-5.4-nano",
            "gpt-4.1-nano",
            "gpt-5.6-terra",
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5",
            "gpt-4.1",
            "gpt-4o",
        ):
            normalized_candidate = normalize_model_id(candidate)
            selected = next(
                (
                    model_id
                    for model_id in model_ids
                    if normalize_model_id(model_id) == normalized_candidate
                ),
                None,
            )
            if selected:
                print(f"Модель {requested} недоступна; выбрана доступная {selected}")
                return selected

    raise ConfigurationError(
        f"Модель {requested} недоступна в этом ElevenLabs workspace"
    )


def mcp_config_from_definition(definition: dict[str, Any]) -> dict[str, Any]:
    url = str(definition.get("server_url") or "").strip()
    name = str(definition.get("server_label") or "").strip()
    if not url or not name:
        raise ConfigurationError(
            "У каждого MCP-сервера должны быть server_label и server_url"
        )

    config: dict[str, Any] = {
        "url": url,
        "name": name,
        "description": definition.get("server_description")
        or f"Инструменты {name} для голосового агента NAMI",
        "approval_policy": "auto_approve_all"
        if definition.get("require_approval", "never") == "never"
        else "require_approval_all",
        "pre_tool_speech": "off",
        # A short background sound must not cancel a lookup mid-flight.  The
        # caller can still interrupt the spoken answer that follows.
        "interruption_mode": "disable_during_tool",
        "execution_mode": "immediate",
        "response_timeout_secs": env_int(
            "ELEVENLABS_MCP_RESPONSE_TIMEOUT", 8, 5, 300
        ),
    }
    authorization = definition.get("authorization")
    if authorization:
        config["request_headers"] = {"Authorization": str(authorization)}
    return config


def sync_mcp_servers(client: httpx.Client, api_key: str) -> list[str]:
    definitions = load_mcp_definitions()
    if not definitions:
        return []

    body = request_json(client, "GET", "/convai/mcp-servers", api_key=api_key)
    existing = body.get("mcp_servers", [])
    ids: list[str] = []

    for definition in definitions:
        config = mcp_config_from_definition(definition)
        match = next(
            (
                item
                for item in existing
                if isinstance(item, dict)
                and isinstance(item.get("config"), dict)
                and item["config"].get("url") == config["url"]
            ),
            None,
        )
        if match and match.get("id"):
            mcp_id = str(match["id"])
            # MCP integrations outlive agent updates. Keep their latency and
            # interruption settings in sync instead of retaining stale values.
            request_json(
                client,
                "PATCH",
                f"/convai/mcp-servers/{mcp_id}",
                api_key=api_key,
                json_body={
                    key: config[key]
                    for key in (
                        "approval_policy",
                        "pre_tool_speech",
                        "interruption_mode",
                        "execution_mode",
                        "response_timeout_secs",
                        "request_headers",
                    )
                    if key in config
                },
            )
            print(f"MCP {config['name']} уже существует: {mcp_id}")
        else:
            created = request_json(
                client,
                "POST",
                "/convai/mcp-servers",
                api_key=api_key,
                json_body={"config": config},
            )
            mcp_id = str(created.get("id") or "")
            if not mcp_id:
                raise ElevenLabsAPIError("ElevenLabs не вернул ID созданного MCP")
            print(f"MCP {config['name']} создан: {mcp_id}")
            existing.append(created)
        ids.append(mcp_id)
    return ids


def write_env_values(path: Path, updates: dict[str, str]) -> None:
    current_lines = (
        path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    )
    output: list[str] = []
    applied: set[str] = set()
    for line in current_lines:
        match = ENV_KEY_PATTERN.match(line)
        key = match.group(1) if match else None
        if key in updates:
            if key not in applied:
                output.append(f"{key}={updates[key]}")
                applied.add(key)
            continue
        output.append(line)
    for key, value in updates.items():
        if key not in applied:
            output.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".env.",
        delete=False,
    ) as temp_file:
        temp_file.write("\n".join(output) + "\n")
        temp_path = Path(temp_file.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def sync_agent(env_path: Path) -> str:
    load_env_file(env_path)
    api_key = required_env("ELEVENLABS_API_KEY")
    required_env("ELEVENLABS_VOICE_ID")

    with httpx.Client(timeout=httpx.Timeout(45.0)) as client:
        llm_model = resolve_llm_model(client, api_key)
        mcp_ids = sync_mcp_servers(client, api_key)
        payload = build_agent_payload(
            llm_model=llm_model,
            mcp_server_ids=mcp_ids,
        )

        agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
        if agent_id:
            response = request_json(
                client,
                "PATCH",
                f"/convai/agents/{agent_id}",
                api_key=api_key,
                json_body=payload,
            )
            agent_id = str(response.get("agent_id") or agent_id)
            print(f"Агент ElevenLabs обновлён: {agent_id}")
        else:
            response = request_json(
                client,
                "POST",
                "/convai/agents/create",
                api_key=api_key,
                json_body=payload,
            )
            agent_id = str(response.get("agent_id") or "")
            if not agent_id:
                raise ElevenLabsAPIError("ElevenLabs не вернул ID созданного агента")
            print(f"Агент ElevenLabs создан: {agent_id}")

    write_env_values(
        env_path,
        {
            "ELEVENLABS_AGENT_ID": agent_id,
            "ELEVENLABS_MCP_SERVER_IDS": ",".join(mcp_ids),
            "ELEVENLABS_LLM_MODEL": llm_model,
        },
    )
    return agent_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создать или обновить NAMI ElevenLabs Agent через API"
    )
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        sync_agent(args.env)
    except (ConfigurationError, ElevenLabsAPIError, httpx.RequestError) as exc:
        raise SystemExit(f"Ошибка синхронизации: {exc}") from exc


if __name__ == "__main__":
    main()
