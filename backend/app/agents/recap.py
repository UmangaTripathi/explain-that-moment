"""Recap agent: full-time story built from verified moment cards and final stats."""

from __future__ import annotations

import json

from app.agents.narrator import LANGUAGES, PERSONAS
from app.config import get_settings
from app.llm import chat_json
from app.models import MomentCard
from app.tools import MatchTools

SYSTEM = """You are a football writer producing the full-time recap for {persona}.
Write in {language}. Use ONLY the facts provided (final stats, half stats, and verified
moments). Do not invent players, numbers, or events.

Respond with JSON:
{{"headline": "max 10 words",
  "story": "one paragraph, 4-6 sentences, the arc of the match",
  "turning_point": {{"minute": 0, "why": "one sentence"}},
  "player_of_the_match": {{"name": "...", "why": "one sentence"}},
  "by_the_numbers": ["3-5 short stat lines"]}}"""


async def recap(tools: MatchTools, cards: list[MomentCard], persona: str, language: str,
                trace: list[dict]) -> dict:
    system = SYSTEM.format(persona=PERSONAS.get(persona, PERSONAS["casual"]),
                           language=LANGUAGES.get(language, "English"))
    last_minute = tools.match.events[-1].minute
    moments = [
        {"minute": c.moment.minute, "type": c.moment.type, "summary": c.moment.summary,
         "verified_claims": [cl.text for cl in c.claims if cl.status == "verified"]}
        for c in sorted(cards, key=lambda c: c.moment.minute)
    ]
    user = (
        f"Final score: {tools.score_at(last_minute)}\n"
        f"Full match stats: {json.dumps(tools.team_stats())}\n"
        f"First half: {json.dumps(tools.team_stats(0, 45))}\n"
        f"Second half: {json.dumps(tools.team_stats(46, last_minute))}\n"
        f"Verified key moments: {json.dumps(moments)}"
    )
    return await chat_json(get_settings().mini_model_deployment, system, user,
                           trace=trace, agent="recap")
