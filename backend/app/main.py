import asyncio
import json
import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents import narrator, recap
from app.config import get_settings
from app.pipeline import DATA_DIR, list_matches, session

logging.basicConfig(level=logging.INFO)
settings = get_settings()

app = FastAPI(title="Explain That Moment API", version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

HEARTBEAT_SECONDS = 15


def _session(match_id: str):
    if not (DATA_DIR / f"{match_id}.json").exists():
        raise HTTPException(404, f"unknown match {match_id}")
    return session(match_id)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": settings.app_version}


@app.get("/matches")
def matches() -> list[dict]:
    return list_matches()


@app.get("/matches/{match_id}")
def match_info(match_id: str) -> dict:
    s = _session(match_id)
    m = s.match
    return {
        "id": match_id, "venue": m.venue, "date": m.date, "competition": m.competition,
        "home": m.home.model_dump(), "away": m.away.model_dump(),
        "duration": m.events[-1].minute, "running": s.running,
        "languages": narrator.LANGUAGES, "personas": list(narrator.PERSONAS),
    }


@app.post("/matches/{match_id}/replay")
async def start_replay(match_id: str, speed: float = Query(30, ge=1, le=600),
                       red_team: bool = False) -> dict:
    s = _session(match_id)
    s.start(speed, red_team)
    return {"started": True, "speed": speed, "red_team": red_team}


@app.get("/matches/{match_id}/stream")
async def stream(match_id: str) -> StreamingResponse:
    s = _session(match_id)

    async def events():
        queue = await s.subscribe()
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield f"event: {message['kind']}\ndata: {json.dumps(message['data'])}\n\n"
        finally:
            s.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/matches/{match_id}/evidence")
def evidence(match_id: str, ids: str) -> list[dict]:
    return _session(match_id).tools.get_events_by_id(ids.split(","))


@app.get("/matches/{match_id}/stats")
def stats(match_id: str, until: int = 200) -> dict:
    return _session(match_id).tools.team_stats(0, until)


class NarrateRequest(BaseModel):
    persona: str = "casual"
    language: str = "en"


@app.post("/matches/{match_id}/moments/{moment_id}/narrate")
async def renarrate(match_id: str, moment_id: str, body: NarrateRequest) -> dict:
    """Re-tell an already verified moment for another persona/language (no new facts)."""
    s = _session(match_id)
    card = s.cards.get(moment_id)
    if not card:
        raise HTTPException(404, "moment not explained yet")
    return await narrator.narrate(card.moment, card.claims, s.tools.score_at(card.moment.minute),
                                  body.persona, body.language, [])


@app.post("/matches/{match_id}/recap")
async def recap_for(match_id: str, body: NarrateRequest) -> dict:
    s = _session(match_id)
    if not s.cards:
        raise HTTPException(409, "replay the match first")
    return await recap.recap(s.tools, list(s.cards.values()), body.persona, body.language, [])
