import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.elevenlabs_config import build_agent_payload, load_mcp_definitions
from app.main import ELEVENLABS_API_BASE, app
from app.yclients_proxy import (
    compact_dates,
    compact_services,
    compact_staff,
    compact_times,
)
from deploy.merge_env_config import merge_env
from deploy.set_nami_prompt import env_value
from deploy.sync_elevenlabs_agent import (
    mcp_config_from_definition,
    resolve_llm_model,
    write_env_values,
)


@pytest.fixture(autouse=True)
def configure_assistant(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice_test")
    monkeypatch.setenv("ELEVENLABS_LLM_MODEL", "qwen3.6-35b-a3b")
    monkeypatch.setenv("REALTIME_SYSTEM_PROMPT", "Тестовый системный промпт")
    monkeypatch.setenv("REALTIME_GREETING_PROMPT", "Тестовое приветствие")
    monkeypatch.setenv("MCP_SERVERS_JSON", "[]")


def test_healthz(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": "elevenlabs",
        "elevenlabs_key_configured": False,
        "agent_configured": True,
    }


def test_index_is_not_cached_and_uses_elevenlabs_sdk():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "@elevenlabs/client@1.21.0" in response.text
    assert "Conversation.startSession" in response.text
    assert 'id="mute"' in response.text
    assert "conversation.setMicMuted" in response.text
    assert 'connectionType: "webrtc"' in response.text
    assert 'id="chat-toggle"' in response.text
    assert 'id="chat-input"' in response.text
    assert 'connectionType: "websocket"' in response.text
    assert "conversation.sendUserMessage" in response.text
    assert "onAgentChatResponsePart" in response.text
    assert "/api/elevenlabs/signed-url" in response.text
    assert "onMessage: handleConversationMessage" in response.text
    assert "/api/backchannels/agree" in response.text


def test_client_config():
    with TestClient(app) as client:
        response = client.get("/api/client-config")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "elevenlabs",
        "connection_type": "webrtc",
    }
    assert response.headers["cache-control"] == "no-store"


def test_conversation_token_requires_api_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/elevenlabs/conversation-token",
            json={"participant_name": "telegram-1"},
        )

    assert response.status_code == 503
    assert "ELEVENLABS_API_KEY" in response.json()["detail"]


def test_conversation_token_is_proxied_without_exposing_api_key():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/convai/conversation/token"
        assert str(request.url).startswith(
            f"{ELEVENLABS_API_BASE}/convai/conversation/token?"
        )
        assert request.url.params["agent_id"] == "agent_test"
        assert request.url.params["participant_name"] == "telegram-42"
        assert request.headers["xi-api-key"] == "test-elevenlabs-key"
        return httpx.Response(
            200,
            json={"token": "temporary-token", "conversation_id": "conv_test"},
        )

    with TestClient(app) as client:
        original_client = app.state.http_client
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post(
            "/api/elevenlabs/conversation-token",
            json={"participant_name": "telegram-42"},
        )
        app.state.http_client = original_client

    assert response.status_code == 200
    assert response.json() == {
        "token": "temporary-token",
        "conversation_id": "conv_test",
    }
    assert "test-elevenlabs-key" not in response.text


def test_signed_url_is_proxied_without_exposing_api_key():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/convai/conversation/get-signed-url"
        assert request.url.params["agent_id"] == "agent_test"
        assert request.headers["xi-api-key"] == "test-elevenlabs-key"
        return httpx.Response(200, json={"signed_url": "wss://temporary.example/session"})

    with TestClient(app) as client:
        original_client = app.state.http_client
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post("/api/elevenlabs/signed-url")
        app.state.http_client = original_client

    assert response.status_code == 200
    assert response.json() == {"signed_url": "wss://temporary.example/session"}
    assert "test-elevenlabs-key" not in response.text


def test_backchannel_is_cached_and_uses_fixed_scripted_clip(tmp_path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/text-to-speech/voice_test/stream"
        assert request.headers["xi-api-key"] == "test-elevenlabs-key"
        body = json.loads(request.content)
        assert body["text"] == "Ага."
        assert body["model_id"] == "eleven_multilingual_v2"
        return httpx.Response(200, content=b"fixed-audio")

    monkeypatch.setattr(main_module, "BACKCHANNEL_CACHE_DIR", tmp_path)
    with TestClient(app) as client:
        original_client = app.state.http_client
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.get("/api/backchannels/agree")
        app.state.http_client = original_client

    assert response.status_code == 200
    assert response.content == b"fixed-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert (tmp_path / "agree.mp3").read_bytes() == b"fixed-audio"


def test_agent_config_uses_qwen_voice_prompt_and_mcp():
    payload = build_agent_payload(
        llm_model="qwen3.6-35b-a3b",
        mcp_server_ids=["mcp_nami"],
    )

    config = payload["conversation_config"]
    assert config["agent"]["language"] == "ru"
    assert config["agent"]["first_message"] == "Тестовое приветствие"
    assert config["agent"]["prompt"]["prompt"] == "Тестовый системный промпт"
    assert config["agent"]["prompt"]["llm"] == "qwen3.6-35b-a3b"
    assert config["agent"]["prompt"]["mcp_server_ids"] == ["mcp_nami"]
    assert config["agent"]["prompt"]["enable_reasoning_summary"] is False
    assert (
        config["agent"]["prompt"]["built_in_tools"]["end_call"]["params"]
        ["system_tool_type"]
        == "end_call"
    )
    assert config["tts"]["model_id"] == "eleven_v3_conversational"
    assert config["tts"]["voice_id"] == "voice_test"
    assert config["tts"]["expressive_mode"] is True
    assert config["turn"]["turn_model"] == "turn_v3"
    assert config["turn"]["turn_eagerness"] == "normal"
    assert config["turn"]["speculative_turn"] is True
    assert config["conversation"]["client_events"] == [
        "audio",
        "interruption",
        "user_transcript",
    ]
    assert payload["platform_settings"]["auth"]["enable_auth"] is True


def test_old_openai_mcp_format_is_reused_for_elevenlabs(monkeypatch):
    monkeypatch.setenv("N8N_MCP_TOKEN", "secret-token")
    monkeypatch.setenv(
        "MCP_SERVERS_JSON",
        (
            '[{"type":"mcp","server_label":"nami_booking",'
            '"server_url":"https://n8n.example.com/mcp",'
            '"authorization":"Bearer ${N8N_MCP_TOKEN}",'
            '"require_approval":"never"}]'
        ),
    )

    definition = load_mcp_definitions()[0]
    config = mcp_config_from_definition(definition)

    assert config["name"] == "nami_booking"
    assert config["url"] == "https://n8n.example.com/mcp"
    assert config["request_headers"]["Authorization"] == "Bearer secret-token"
    assert config["approval_policy"] == "auto_approve_all"
    assert config["pre_tool_speech"] == "off"
    assert config["execution_mode"] == "immediate"
    assert config["interruption_mode"] == "disable_during_tool"
    assert config["response_timeout_secs"] == 8


def test_invalid_mcp_json_returns_configuration_error(monkeypatch):
    monkeypatch.setenv("MCP_SERVERS_JSON", "not-json")
    with pytest.raises(ValueError, match="MCP_SERVERS_JSON"):
        load_mcp_definitions()


def test_env_writer_preserves_secrets(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ELEVENLABS_API_KEY=keep-secret\nELEVENLABS_AGENT_ID=old-agent\n",
        encoding="utf-8",
    )

    write_env_values(
        env_path,
        {
            "ELEVENLABS_AGENT_ID": "new-agent",
            "ELEVENLABS_MCP_SERVER_IDS": "mcp-one",
        },
    )

    merged = env_path.read_text(encoding="utf-8")
    assert "ELEVENLABS_API_KEY=keep-secret" in merged
    assert "ELEVENLABS_AGENT_ID=new-agent" in merged
    assert "ELEVENLABS_MCP_SERVER_IDS=mcp-one" in merged


def test_env_merge_preserves_elevenlabs_credentials(tmp_path):
    env_path = tmp_path / ".env"
    template_path = tmp_path / "server.env.example"
    env_path.write_text(
        "ELEVENLABS_API_KEY=keep-secret\n"
        "ELEVENLABS_AGENT_ID=keep-agent\n"
        "ELEVENLABS_VOICE_ID=keep-voice\n",
        encoding="utf-8",
    )
    template_path.write_text(
        "ELEVENLABS_API_KEY=\n"
        "ELEVENLABS_AGENT_ID=\n"
        "ELEVENLABS_VOICE_ID=\n"
        "ELEVENLABS_TTS_MODEL=eleven_v3_conversational\n",
        encoding="utf-8",
    )

    merge_env(env_path, template_path)

    merged = env_path.read_text(encoding="utf-8")
    assert "ELEVENLABS_API_KEY=keep-secret" in merged
    assert "ELEVENLABS_AGENT_ID=keep-agent" in merged
    assert "ELEVENLABS_VOICE_ID=keep-voice" in merged
    assert "ELEVENLABS_TTS_MODEL=eleven_v3_conversational" in merged


def test_strong_openai_model_falls_back_to_another_openai_model(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_LLM_MODEL", "gpt-5.6-terra")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/convai/llm/list"
        return httpx.Response(
            200,
            json={"llms": [{"llm": "gpt-5.4"}, {"llm": "qwen3.6-35b-a3b"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert resolve_llm_model(client, "test-key") == "gpt-5.4"


def test_test_persona_is_loaded_from_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'NAMI_TEST_PERSONA_PROMPT="Одесская манера\\nбез карикатуры"\n',
        encoding="utf-8",
    )

    assert env_value(env_path, "NAMI_TEST_PERSONA_PROMPT") == (
        "Одесская манера\nбез карикатуры"
    )


def test_yclients_responses_are_compacted_for_the_agent():
    services = compact_services(
        {
            "services": [
                {
                    "id": 7,
                    "title": "Маникюр с покрытием",
                    "price_min": 2500,
                    "price_max": 3000,
                    "seance_length": 5400,
                    "images": ["must-not-reach-the-agent"],
                    "prepaid_settings": {"irrelevant": True},
                }
            ]
        }
    )
    assert services == {
        "services": [
            {
                "id": 7,
                "name": "Маникюр с покрытием",
                "price_from": 2500,
                "price_to": 3000,
                "duration_minutes": 90,
            }
        ]
    }
    assert compact_staff(
        [{"id": 3, "name": "Елена", "specialization": "Ногтевой сервис", "avatar": "x"}]
    ) == {"staff": [{"id": 3, "name": "Елена", "specialization": "Ногтевой сервис"}]}
    assert compact_dates({"booking_dates": ["2026-09-03"], "working_dates": ["ignored"]}) == {
        "available_dates": ["2026-09-03"]
    }
    assert compact_times(
        [{"time": "12:00", "datetime": "2026-09-03 12:00:00", "sum_length": 1000}],
        "2026-09-03",
    ) == {"date": "2026-09-03", "available_times": ["12:00"]}
