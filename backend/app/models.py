from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "kickoff", "pass", "carry", "dribble", "tackle", "interception", "pressure",
    "shot", "save", "goal", "foul", "card", "corner", "substitution", "half_time", "full_time",
]


class Player(BaseModel):
    id: str
    name: str
    number: int
    position: str


class Team(BaseModel):
    id: str
    name: str
    short: str
    formation: str
    players: list[Player]


class MatchEvent(BaseModel):
    id: str
    minute: int
    second: int
    period: int
    team: str | None = None
    player: str | None = None
    type: EventType
    x: float | None = None  # 0-100, attacking left to right for the acting team
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None  # success, fail, on_target, off_target, blocked, goal, yellow, red…
    xg: float | None = None
    related_player: str | None = None  # receiver, fouled player, sub coming on…
    sequence: int = 0  # possession sequence id


class Match(BaseModel):
    id: str
    competition: str
    date: str
    venue: str
    home: Team
    away: Team
    events: list[MatchEvent]


MomentType = Literal[
    "goal", "big_chance", "momentum_swing", "pressing_win", "red_card", "key_substitution",
]


class KeyMoment(BaseModel):
    id: str
    minute: int
    type: MomentType
    team: str | None = None
    event_ids: list[str]
    importance: float = Field(ge=0, le=1)
    summary: str  # deterministic one-liner from the detector, used as agent input


class Claim(BaseModel):
    text: str
    event_ids: list[str]
    status: Literal["pending", "verified", "rejected"] = "pending"
    reason: str | None = None


class MomentCard(BaseModel):
    moment: KeyMoment
    headline: str
    narrative: str
    claims: list[Claim]
    language: str = "en"
    persona: str = "casual"
    verifier_rounds: int = 0
