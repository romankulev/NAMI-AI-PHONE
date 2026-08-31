import json
import os
import re
from typing import Any


ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class ConfigurationError(ValueError):
    pass


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Не настроена переменная окружения {name}")
    return value


def prompt_env(name: str) -> str:
    return required_env(name).replace("\\n", "\n")


def env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} должен быть числом") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} должен быть в диапазоне от {minimum} до {maximum}"
        )
    return value


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} должен быть целым числом") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} должен быть в диапазоне от {minimum} до {maximum}"
        )
    return value


def env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, str(default)).strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} должен быть true или false")


def expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            return required_env(match.group(1))

        return ENV_PLACEHOLDER_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [expand_env_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_placeholders(item) for key, item in value.items()}
    return value


def load_mcp_definitions() -> list[dict[str, Any]]:
    raw_value = os.getenv("MCP_SERVERS_JSON", "[]").strip() or "[]"
    try:
        servers = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("MCP_SERVERS_JSON содержит некорректный JSON") from exc

    if not isinstance(servers, list) or not all(
        isinstance(server, dict) and server.get("type") == "mcp"
        for server in servers
    ):
        raise ConfigurationError(
            'MCP_SERVERS_JSON должен быть JSON-массивом объектов с "type": "mcp"'
        )
    return expand_env_placeholders(servers)


def build_agent_payload(
    *,
    llm_model: str,
    mcp_server_ids: list[str] | None = None,
) -> dict[str, Any]:
    turn_eagerness = os.getenv("ELEVENLABS_TURN_EAGERNESS", "normal").strip()
    if turn_eagerness not in {"patient", "normal", "eager"}:
        raise ConfigurationError(
            "ELEVENLABS_TURN_EAGERNESS должен быть patient, normal или eager"
        )

    keywords = [
        item.strip()
        for item in os.getenv(
            "ELEVENLABS_ASR_KEYWORDS",
            "NAMI,НАМИ,NAMI BEAUTY,YCLIENTS",
        ).split(",")
        if item.strip()
    ]

    prompt_config: dict[str, Any] = {
        "prompt": prompt_env("REALTIME_SYSTEM_PROMPT"),
        "llm": llm_model,
        "temperature": env_float("ELEVENLABS_LLM_TEMPERATURE", 0.3, 0.0, 1.0),
        "max_tokens": env_int("ELEVENLABS_LLM_MAX_TOKENS", 300, 1, 4_096),
        "ignore_default_personality": True,
        "enable_reasoning_summary": False,
        "built_in_tools": {
            "end_call": {
                "name": "end_call",
                "description": (
                    "Заверши разговор после явного прощания пользователя. "
                    "Перед завершением передай одну короткую тёплую прощальную "
                    "реплику в параметре message. Не завершай разговор только "
                    "из-за паузы, благодарности или завершения одного вопроса."
                ),
                "type": "system",
                "params": {"system_tool_type": "end_call"},
            }
        },
    }
    if mcp_server_ids:
        prompt_config["mcp_server_ids"] = mcp_server_ids

    return {
        "name": os.getenv("ELEVENLABS_AGENT_NAME", "NAMI Beauty Administrator").strip(),
        "tags": ["nami", "voice", "telegram-mini-app"],
        "conversation_config": {
            "asr": {
                "quality": "high",
                "provider": "scribe_realtime",
                "keywords": keywords,
            },
            "turn": {
                "turn_timeout": env_int("ELEVENLABS_TURN_TIMEOUT", 8, 1, 30),
                "silence_end_call_timeout": -1,
                "turn_eagerness": turn_eagerness,
                "spelling_patience": "auto",
                "speculative_turn": False,
                "turn_model": "turn_v3",
            },
            "tts": {
                "model_id": os.getenv(
                    "ELEVENLABS_TTS_MODEL",
                    "eleven_v3_conversational",
                ).strip(),
                "voice_id": required_env("ELEVENLABS_VOICE_ID"),
                "expressive_mode": env_bool("ELEVENLABS_EXPRESSIVE_MODE", True),
                "stability": env_float("ELEVENLABS_VOICE_STABILITY", 0.45, 0.0, 1.0),
                "speed": env_float("ELEVENLABS_VOICE_SPEED", 1.0, 0.7, 1.2),
                "similarity_boost": env_float(
                    "ELEVENLABS_VOICE_SIMILARITY", 0.8, 0.0, 1.0
                ),
            },
            "conversation": {
                "text_only": False,
                "max_duration_seconds": env_int(
                    "ELEVENLABS_MAX_CALL_SECONDS", 1_800, 60, 7_200
                ),
            },
            "agent": {
                "first_message": prompt_env("REALTIME_GREETING_PROMPT"),
                "language": "ru",
                "disable_first_message_interruptions": False,
                "prompt": prompt_config,
            },
        },
        "platform_settings": {
            "auth": {"enable_auth": True},
        },
    }
