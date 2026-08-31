import os
from contextlib import asynccontextmanager
from datetime import date

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
) -> JSONResponse:
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
    return JSONResponse(payload, status_code=response.status_code)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "configured": bool(os.getenv("YC_PARTNER_TOKEN")),
    }


@app.get("/services")
async def services(request: Request) -> JSONResponse:
    return await yclients_request(
        request, "GET", f"book_services/{required_env('YC_COMPANY_ID')}"
    )


@app.get("/staff")
async def staff(request: Request, service_id: int = Query(gt=0)) -> JSONResponse:
    return await yclients_request(
        request,
        "GET",
        f"book_staff/{required_env('YC_COMPANY_ID')}",
        params=[("service_ids[]", service_id)],
    )


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
    return await yclients_request(
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


@app.get("/times")
async def times(
    request: Request,
    service_id: int = Query(gt=0),
    staff_id: int = Query(gt=0),
    target_date: str = Query(alias="date"),
) -> JSONResponse:
    DateRange.validate(target_date)
    return await yclients_request(
        request,
        "GET",
        f"book_times/{required_env('YC_COMPANY_ID')}/{staff_id}/{target_date}",
        params=[("service_ids[]", service_id)],
    )


@app.post("/check")
async def check(request: Request, slot: SlotCheck) -> JSONResponse:
    return await yclients_request(
        request,
        "POST",
        f"book_check/{required_env('YC_COMPANY_ID')}",
        json_body={
            "service_ids": [slot.service_id],
            "staff_id": slot.staff_id,
            "datetime": slot.datetime,
        },
    )
