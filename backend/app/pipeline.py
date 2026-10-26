"""Orchestrator: replays a match, detects key moments, and runs the agent pipeline.

    events -> MomentDetector -> Analyst -> Verifier (reject -> Analyst revise, max 2 rounds)
           -> Narrator -> MomentCard -> SSE subscribers
    full time -> Recap agent
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path

from app.agents import analyst, narrator, recap, verifier
from app.detector import MomentDetector
from app.models import Claim, KeyMoment, Match, MomentCard
from app.tools import MatchTools

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MIN_IMPORTANCE = 0.55  # moments below this are shown on the timeline but not explained
MAX_VERIFY_ROUNDS = 2
AGENT_CONCURRENCY = 3
BROADCAST_TYPES = {"kickoff", "goal", "shot", "card", "substitution", "half_time", "full_time"}


def load_match(match_id: str) -> Match:
    path = DATA_DIR / f"{match_id}.json"
    return Match.model_validate(json.loads(path.read_text()))


def list_matches() -> list[dict]:
    out = []
    for path in sorted(DATA_DIR.glob("match_*.json")):
        m = load_match(path.stem)
        tools = MatchTools(m)
        out.append({"id": path.stem, "home": m.home.name, "away": m.away.name,
                    "final_score": tools.score_at(200), "venue": m.venue, "date": m.date})
    return out


async def explain_moment(moment: KeyMoment, tools: MatchTools, persona: str = "casual",
                         language: str = "en", red_team: bool = False) -> MomentCard:
    trace: list[dict] = []
    claims = await analyst.analyse(moment, tools, trace)
    if red_team:
        claims.append(fabricate_claim(moment, tools))

    rounds = 0
    for rounds in range(1, MAX_VERIFY_ROUNDS + 1):  # noqa: B007 - rounds is reported
        await verifier.verify(moment, claims, tools, trace)
        rejected = [c for c in claims if c.status == "rejected" and not c.reason_handled]
        if not rejected or rounds == MAX_VERIFY_ROUNDS:
            break
        for c in rejected:
            c.reason_handled = True
        verified = [c for c in claims if c.status == "verified"]
        claims += await analyst.revise(moment, rejected, verified, tools, trace)

    text = await narrator.narrate(moment, claims, tools.score_at(moment.minute), persona,
                                  language, trace)
    return MomentCard(moment=moment, headline=text["headline"], narrative=text["narrative"],
                      claims=claims, persona=persona, language=language,
                      verifier_rounds=rounds, red_team=red_team, trace=trace)


def fabricate_claim(moment: KeyMoment, tools: MatchTools) -> Claim:
    """Red-team mode: inject a plausible but false claim to prove the Verifier catches it."""
    rng = random.Random(moment.id)
    opponents = [p for t in (tools.match.home, tools.match.away) if t.id != moment.team
                 for p in t.players]
    culprit = rng.choice(opponents).name
    return Claim(
        text=f"{culprit} had already hit the post with a 0.{rng.randint(55, 79)} xG chance "
             f"moments earlier, in the {max(1, moment.minute - 3)}th minute.",
        event_ids=moment.event_ids[:1],
        injected=True,
    )


class MatchSession:
    """One live replay of a match, shared by every viewer of that match."""

    def __init__(self, match_id: str):
        self.match_id = match_id
        self.match = load_match(match_id)
        self.tools = MatchTools(self.match)
        self.subscribers: set[asyncio.Queue] = set()
        self.cards: dict[str, MomentCard] = {}
        self.moments: list[KeyMoment] = []
        self.history: list[dict] = []  # replayed to late joiners
        self.recap: dict | None = None
        self.task: asyncio.Task | None = None
        self.semaphore = asyncio.Semaphore(AGENT_CONCURRENCY)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, speed: float, red_team: bool) -> None:
        if self.task:
            self.task.cancel()
        self.cards.clear()
        self.moments.clear()
        self.history.clear()
        self.recap = None
        self.task = asyncio.create_task(self._replay(speed, red_team))

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for message in self.history:
            queue.put_nowait(message)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish(self, kind: str, data) -> None:
        message = {"kind": kind, "data": data}
        self.history.append(message)
        for queue in self.subscribers:
            queue.put_nowait(message)

    async def _replay(self, speed: float, red_team: bool) -> None:
        detector = MomentDetector(self.match)
        pending: list[asyncio.Task] = []
        last_clock = -1.0
        last_minute = -1
        red_team_used = False
        try:
            self.publish("status", {"state": "live", "speed": speed})
            for ev in self.match.events:
                clock = ev.minute * 60 + ev.second
                if last_clock >= 0 and clock > last_clock:
                    await asyncio.sleep((clock - last_clock) / speed)
                last_clock = clock
                if ev.minute != last_minute:
                    last_minute = ev.minute
                    self.publish("clock", {"minute": ev.minute,
                                           "score": self.tools.score_at(ev.minute)})
                if ev.type in BROADCAST_TYPES:
                    self.publish("event", self.tools.describe(ev))
                for moment in detector.feed(ev):
                    self.moments.append(moment)
                    self.publish("moment", moment.model_dump())
                    if moment.importance >= MIN_IMPORTANCE:
                        # Red-team one high-impact moment per match, so the demo shows a catch.
                        inject = red_team and not red_team_used and moment.importance >= 0.85
                        red_team_used = red_team_used or inject
                        pending.append(asyncio.create_task(self._explain(moment, inject)))
            self.publish("status", {"state": "finishing"})
            await asyncio.gather(*pending, return_exceptions=True)
            self.recap = await recap.recap(self.tools, list(self.cards.values()), "casual",
                                           "en", [])
            self.publish("recap", self.recap)
            self.publish("status", {"state": "finished"})
        except asyncio.CancelledError:
            for task in pending:
                task.cancel()
            raise
        except Exception as exc:  # surface failures to the UI instead of dying silently
            log.exception("replay failed")
            self.publish("status", {"state": "error", "detail": str(exc)})

    async def _explain(self, moment: KeyMoment, red_team: bool) -> None:
        async with self.semaphore:
            self.publish("agent", {"moment_id": moment.id, "state": "analysing"})
            try:
                card = await explain_moment(moment, self.tools, red_team=red_team)
            except Exception as exc:
                log.exception("moment %s failed", moment.id)
                self.publish("agent", {"moment_id": moment.id, "state": "failed",
                                       "detail": str(exc)})
                return
            self.cards[moment.id] = card
            self.publish("card", card.model_dump())


SESSIONS: dict[str, MatchSession] = {}


def session(match_id: str) -> MatchSession:
    if match_id not in SESSIONS:
        SESSIONS[match_id] = MatchSession(match_id)
    return SESSIONS[match_id]
