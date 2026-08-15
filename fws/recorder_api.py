"""Telemetry recordings, and the automatic dump taken when a fault latches.

    GET    /api/v1/recordings              what has been captured
    POST   /api/v1/recordings/start        begin an explicit recording
    POST   /api/v1/recordings/finish       end it
    GET    /api/v1/recordings/{name}       download (JSONL, or ?format=csv)
    DELETE /api/v1/recordings/{name}       remove one

The fault dump needs no route: it happens on its own, because the seconds
before a fault are the ones worth having and nobody is at a keyboard when
the fault lands.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

PREFIX = "/api/v1"


class StartBody(BaseModel):
    name: str = Field(min_length=1, max_length=64,
                      description="letters, digits, dot, dash, underscore")


def build(get_recorder, audit) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["recordings"])

    @router.get("/recordings")
    def list_recordings():
        rec = get_recorder()
        return {"recordings": rec.list(), **rec.health()}

    @router.post("/recordings/start", status_code=201)
    def start(body: StartBody):
        try:
            name = get_recorder().start(body.name)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        except OSError as e:
            raise HTTPException(507, f"could not open the recording: {e}") from e
        audit("recording.start", name=name)
        return {"recording": name,
                "note": "sampling at 10 Hz until POST /recordings/finish"}

    @router.post("/recordings/finish")
    def finish():
        try:
            result = get_recorder().stop()
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        audit("recording.stop", **result)
        return result

    @router.get("/recordings/{name}", response_class=PlainTextResponse)
    def download(name: str,
                 format: str = Query(default="jsonl", pattern="^(jsonl|csv)$")):
        rec = get_recorder()
        try:
            if format == "csv":
                return PlainTextResponse(rec.as_csv(name),
                                         media_type="text/csv")
            return PlainTextResponse(rec.read(name),
                                     media_type="application/x-ndjson")
        except FileNotFoundError as e:
            raise HTTPException(404, f"no recording named {name!r}") from e

    @router.delete("/recordings/{name}")
    def delete(name: str):
        try:
            get_recorder().delete(name)
        except FileNotFoundError as e:
            raise HTTPException(404, f"no recording named {name!r}") from e
        audit("recording.delete", name=name)
        return {"deleted": name}

    return router
