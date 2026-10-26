"""Analyst agent: investigates a key moment with data tools and drafts cited claims."""

from __future__ import annotations

import json

from app.config import get_settings
from app.llm import chat_json
from app.models import Claim, KeyMoment
from app.tools import TOOL_SCHEMAS, MatchTools, call_tool

SYSTEM = """You are a football performance analyst working for a broadcaster.
Explain WHY a key moment happened, using only facts you obtain from the tools.

Rules:
- Call tools to investigate (the build-up, the minutes before, team or player stats).
- Every claim must cite the ids of the events that prove it (event_ids). No id, no claim.
- Only state numbers (xG, minutes, counts, percentages) that appear in tool results.
- Use player and team names exactly as the tools return them.
- 2 to 4 claims, each ONE sentence of at most 30 words. Focus on cause and tactical
  context, not just what happened. Never repeat a point another claim already makes.

Respond with JSON:
{"claims": [{"text": "...", "event_ids": ["e00123", ...]}]}"""


async def analyse(moment: KeyMoment, tools: MatchTools, trace: list[dict]) -> list[Claim]:
    window_start = max(0, moment.minute - 10)
    user = (
        f"Key moment ({moment.type}) at {moment.minute}': {moment.summary}\n"
        f"Score after this moment: {tools.score_at(moment.minute)}\n"
        f"Detector evidence: {json.dumps(tools.get_events_by_id(moment.event_ids))}\n"
        f"Suggested investigation window: {window_start}-{moment.minute}'."
    )
    return await _run(user, tools, trace)


async def revise(moment: KeyMoment, rejected: list[Claim], verified: list[Claim],
                 tools: MatchTools, trace: list[dict]) -> list[Claim]:
    feedback = "\n".join(f"- \"{c.text}\" REJECTED: {c.reason}" for c in rejected)
    user = (
        f"Key moment ({moment.type}) at {moment.minute}': {moment.summary}\n"
        f"Detector evidence: {json.dumps(tools.get_events_by_id(moment.event_ids))}\n"
        f"Already verified (keep, do not repeat): {[c.text for c in verified]}\n"
        f"The fact-checker rejected these claims:\n{feedback}\n"
        f"Re-investigate with the tools and return at most {len(rejected)} corrected "
        "replacement claim(s), one per rejected claim, each saying something the verified "
        "claims do not. Return an empty list if a point cannot be supported."
    )
    return await _run(user, tools, trace, step="revise")


async def _run(user: str, tools: MatchTools, trace: list[dict], step: str = "analyse"):
    result = await chat_json(
        get_settings().main_model_deployment, SYSTEM, user,
        tools=TOOL_SCHEMAS, run_tool=lambda n, a: call_tool(tools, n, a),
        trace=trace, agent=f"analyst:{step}",
    )
    return [Claim(text=c["text"], event_ids=list(c.get("event_ids", [])))
            for c in result.get("claims", []) if c.get("text")]
