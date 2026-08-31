import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.elevenlabs_config import ConfigurationError, required_env


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
BACKCHANNEL_CACHE_DIR = Path(
    os.getenv("NAMI_BACKCHANNEL_CACHE_DIR", "/tmp/nami-backchannels")
)
# These are fixed clips, never LLM output.  Short clips make a long customer
# monologue feel heard without cutting off the person or changing the dialog.
BACKCHANNELS = {
    "agree": "Ага.",
    "understand": "Угу.",
    "thinking": "Хм.",
    "continue": "Так.",
}
_backchannel_locks = {key: asyncio.Lock() for key in BACKCHANNELS}


class ConversationTokenRequest(BaseModel):
    participant_name: str | None = Field(default=None, min_length=1, max_length=128)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(35.0))
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(
    title="ElevenLabs NAMI Voice Agent MVP",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "provider": "elevenlabs",
            "elevenlabs_key_configured": bool(os.getenv("ELEVENLABS_API_KEY")),
            "agent_configured": bool(os.getenv("ELEVENLABS_AGENT_ID")),
        }
    )


@app.get("/api/client-config", include_in_schema=False)
async def client_config() -> JSONResponse:
    return JSONResponse(
        {
            "provider": "elevenlabs",
            "connection_type": "webrtc",
        },
        headers={"Cache-Control": "no-store"},
    )


def elevenlabs_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:2_000] or "Неизвестная ошибка ElevenLabs API"

    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)[:2_000]
    return str(detail or body)[:2_000]


@app.post("/api/elevenlabs/conversation-token", include_in_schema=False)
async def create_conversation_token(
    payload: ConversationTokenRequest,
    request: Request,
) -> JSONResponse:
    try:
        api_key = required_env("ELEVENLABS_API_KEY")
        agent_id = required_env("ELEVENLABS_AGENT_ID")
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    params: dict[str, str] = {"agent_id": agent_id}
    if payload.participant_name:
        params["participant_name"] = payload.participant_name

    try:
        response = await request.app.state.http_client.get(
            f"{ELEVENLABS_API_BASE}/convai/conversation/token",
            headers={"xi-api-key": api_key},
            params=params,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Не удалось подключиться к ElevenLabs API",
        ) from exc

    if not response.is_success:
        raise HTTPException(
            status_code=response.status_code,
            detail=elevenlabs_error_detail(response),
        )

    body = response.json()
    token = body.get("token")
    if not token:
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs API не вернул WebRTC-токен",
        )

    return JSONResponse(
        {
            "token": token,
            "conversation_id": body.get("conversation_id"),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/elevenlabs/signed-url", include_in_schema=False)
async def create_signed_url(request: Request) -> JSONResponse:
    """Create a short-lived URL for a browser text-chat session.

    The ElevenLabs API key remains server-side; the client receives only a
    single-use signed URL for the authenticated WebSocket connection.
    """
    try:
        api_key = required_env("ELEVENLABS_API_KEY")
        agent_id = required_env("ELEVENLABS_AGENT_ID")
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        response = await request.app.state.http_client.get(
            f"{ELEVENLABS_API_BASE}/convai/conversation/get-signed-url",
            headers={"xi-api-key": api_key},
            params={"agent_id": agent_id},
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Не удалось подключиться к ElevenLabs API",
        ) from exc

    if not response.is_success:
        raise HTTPException(
            status_code=response.status_code,
            detail=elevenlabs_error_detail(response),
        )

    body = response.json()
    signed_url = body.get("signed_url")
    if not signed_url:
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs API не вернул подписанный URL для чата",
        )

    return JSONResponse({"signed_url": signed_url}, headers={"Cache-Control": "no-store"})


@app.get("/api/backchannels/{clip}", include_in_schema=False)
async def get_backchannel_clip(clip: str, request: Request) -> Response:
    """Return a cached, pre-scripted acknowledgement in the agent's voice.

    It is intentionally independent from the conversation model: the browser
    decides *when* to play a clip while the caller is speaking, and ElevenLabs
    is contacted only once per clip to create its cache.
    """
    text = BACKCHANNELS.get(clip)
    if text is None:
        raise HTTPException(status_code=404, detail="Неизвестная реплика")

    cache_path = BACKCHANNEL_CACHE_DIR / f"{clip}.mp3"
    cache_headers = {"Cache-Control": "public, max-age=604800, immutable"}
    if cache_path.is_file():
        return FileResponse(cache_path, media_type="audio/mpeg", headers=cache_headers)

    async with _backchannel_locks[clip]:
        if cache_path.is_file():
            return FileResponse(cache_path, media_type="audio/mpeg", headers=cache_headers)
        try:
            api_key = required_env("ELEVENLABS_API_KEY")
            voice_id = required_env("ELEVENLABS_VOICE_ID")
            response = await request.app.state.http_client.post(
                f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}/stream",
                headers={"xi-api-key": api_key},
                params={"output_format": "mp3_44100_128"},
                json={
                    "text": text,
                    # This call happens once and is cached; use the same voice
                    # but a reliable multilingual TTS model for Russian clips.
                    "model_id": "eleven_multilingual_v2",
                    "language_code": "ru",
                    "voice_settings": {
                        "stability": 0.35,
                        "similarity_boost": 0.8,
                        "speed": 1.0,
                    },
                },
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail="Не удалось подготовить короткую реплику",
            ) from exc

        if not response.is_success:
            raise HTTPException(
                status_code=response.status_code,
                detail=elevenlabs_error_detail(response),
            )

        BACKCHANNEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return Response(
            response.content,
            media_type="audio/mpeg",
            headers=cache_headers,
        )
