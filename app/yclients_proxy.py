import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}")
    return value


class SlotCheck(BaseModel):
    service_id: int = Field(gt=0)
    staff_id: int = Field(gt=0)
    datetime: str = Field(min_length=16, max_length=40)


class DateRange:
    @staticmethod
    def validate(value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Date must be YYYY-MM-DD") from exc
        return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)


async def yclients_request(
    request: Request,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int]] | None = None,
    json_body: dict | None = None,
) -> Any:
    base_url = os.getenv("YC_API_BASE_URL", "https://api.yclients.com/api/v1").rstrip("/")
    headers = {
        "Accept": "application/vnd.yclients.v2+json",
        "Authorization": f"Bearer {required_env('YC_PARTNER_TOKEN')}",
    }
    try:
        response = await request.app.state.http_client.request(
            method,
            f"{base_url}/{path.lstrip('/')}",
            headers=headers,
            params=params,
            json=json_body,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="YCLIENTS is unavailable") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid YCLIENTS response") from exc
    if not response.is_success or payload.get("success") is False:
        raise HTTPException(
            status_code=response.status_code if response.status_code >= 400 else 502,
            detail="YCLIENTS не смог обработать запрос",
        )

    if not isinstance(payload, dict) or "data" not in payload:
        raise HTTPException(status_code=502, detail="YCLIENTS вернул неожиданный ответ")
    return payload["data"]


def compact_text(value: Any, *, limit: int = 180) -> str | None:
    """Keep the agent context small and prevent HTML-heavy YCLIENTS fields."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] or None


def compact_minutes(value: Any) -> int | None:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    # YCLIENTS sends session length in seconds.  Retain a sensible fallback for
    # installations that already expose minutes.
    return round(value / 60) if value > 300 else round(value)


def compact_price(value: Any) -> int | float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def compact_services(data: Any) -> dict[str, list[dict[str, Any]]]:
    source = data.get("services", []) if isinstance(data, dict) else []
    services: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        service: dict[str, Any] = {"id": item["id"]}
        name = compact_text(item.get("title"), limit=120)
        if name:
            service["name"] = name
        minimum = compact_price(item.get("price_min"))
        maximum = compact_price(item.get("price_max"))
        if minimum is not None:
            service["price_from"] = minimum
        if maximum is not None and maximum != minimum:
            service["price_to"] = maximum
        duration = compact_minutes(item.get("seance_length"))
        if duration is not None:
            service["duration_minutes"] = duration
        services.append(service)
    return {"services": services}


def compact_staff(data: Any) -> dict[str, list[dict[str, Any]]]:
    staff: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        specialist: dict[str, Any] = {"id": item["id"]}
        name = compact_text(item.get("name"), limit=80)
        if name:
            specialist["name"] = name
        specialization = compact_text(item.get("specialization"), limit=120)
        if specialization:
            specialist["specialization"] = specialization
        staff.append(specialist)
    return {"staff": staff}


def compact_dates(data: Any) -> dict[str, list[str]]:
    values = data.get("booking_dates", []) if isinstance(data, dict) else []
    return {"available_dates": [value for value in values if isinstance(value, str)]}


def compact_times(data: Any, target_date: str) -> dict[str, Any]:
    times: list[str] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("time"), str):
            times.append(item["time"])
    return {"date": target_date, "available_times": times}


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "configured": bool(os.getenv("YC_PARTNER_TOKEN")),
    }


@app.get("/services")
async def services(request: Request) -> JSONResponse:
    data = await yclients_request(
        request, "GET", f"book_services/{required_env('YC_COMPANY_ID')}"
    )
    return JSONResponse({"data": compact_services(data)})


@app.get("/staff")
async def staff(request: Request, service_id: int = Query(gt=0)) -> JSONResponse:
    data = await yclients_request(
        request,
        "GET",
        f"book_staff/{required_env('YC_COMPANY_ID')}",
        params=[("service_ids[]", service_id), ("without_seances", "1")],
    )
    return JSONResponse({"data": compact_staff(data)})


@app.get("/dates")
async def dates(
    request: Request,
    service_id: int = Query(gt=0),
    staff_id: int = Query(gt=0),
    date_from: str = Query(),
    date_to: str = Query(),
) -> JSONResponse:
    start = date.fromisoformat(DateRange.validate(date_from))
    end = date.fromisoformat(DateRange.validate(date_to))
    if end < start or (end - start).days > 31:
        raise HTTPException(status_code=422, detail="Date range must be 0..31 days")
    data = await yclients_request(
        request,
        "GET",
        f"book_dates/{required_env('YC_COMPANY_ID')}",
        params=[
            ("service_ids[]", service_id),
            ("staff_id", staff_id),
            ("date_from", date_from),
            ("date_to", date_to),
        ],
    )
    return JSONResponse({"data": compact_dates(data)})


@app.get("/times")
async def times(
    request: Request,
    service_id: int = Query(gt=0),
    staff_id: int = Query(gt=0),
    target_date: str = Query(alias="date"),
) -> JSONResponse:
    DateRange.validate(target_date)
    data = await yclients_request(
        request,
        "GET",
        f"book_times/{required_env('YC_COMPANY_ID')}/{staff_id}/{target_date}",
        params=[("service_ids[]", service_id)],
    )
    return JSONResponse({"data": compact_times(data, target_date)})


@app.post("/check")
async def check(request: Request, slot: SlotCheck) -> JSONResponse:
    await yclients_request(
        request,
        "POST",
        f"book_check/{required_env('YC_COMPANY_ID')}",
        json_body={
            "appointments": [
                {
                    "id": 0,
                    "services": [slot.service_id],
                    "events": [],
                    "staff_id": slot.staff_id,
                    "datetime": slot.datetime,
                }
            ]
        },
    )
    # A successful book_check response has no useful payload.  Do not pass an
    # empty or provider-specific structure back into the model.
    return JSONResponse({"data": {"available": True}})
