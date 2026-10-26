"""Verifier agent: no claim reaches a fan unless the cited match data supports it.

Two layers:
1. Hard checks in code: cited events exist; player names, xG values and minutes
   mentioned in the claim match the cited events.
2. LLM judge: reads the claim against the cited events and window stats and rejects
   anything the evidence does not support (overstated causes, wrong outcomes).
"""

from __future__ import annotations

import json
import re

from app.config import get_settings
from app.llm import chat_json
from app.models import Claim, KeyMoment
from app.tools import MatchTools

XG_RE = re.compile(r"\b0\.\d{1,3}\b")
MINUTE_RE = re.compile(r"\b(\d{1,3})(?:'|’|(?:th|st|nd|rd)? minute(?!s))")

SYSTEM = """You are a strict football fact-checker for live broadcast.
For each claim, decide if the cited events (and the window stats) SUPPORT it.
Reject a claim if it states something the evidence does not show: wrong player, wrong
outcome, invented numbers, or a cause/effect the events do not demonstrate.
Interpretation is allowed only when the cited events clearly show it.

Respond with JSON:
{"verdicts": [{"index": 0, "supported": true|false, "reason": "short reason"}]}"""


def hard_check(claim: Claim, tools: MatchTools) -> str | None:
    """Return a rejection reason, or None if the claim passes the deterministic checks."""
    if not claim.event_ids:
        return "no evidence cited"
    missing = [i for i in claim.event_ids if i not in tools.by_id]
    if missing:
        return f"cited events do not exist: {', '.join(missing)}"
    cited = [tools.by_id[i] for i in claim.event_ids]

    involved = {tools.names.get(p) for e in cited for p in (e.player, e.related_player) if p}
    for name in tools.names.values():
        if _mentions(claim.text, name) and name not in involved:
            return f"{name} does not appear in the cited events"

    xgs = [e.xg for e in cited if e.xg is not None]
    candidates = set(xgs) | {round(sum(xgs), 2)}
    for raw in XG_RE.findall(claim.text):
        if "xg" in claim.text.lower() and not any(abs(float(raw) - v) <= 0.015 for v in candidates):
            return f"xG {raw} does not match the cited events"

    minutes = {e.minute for e in cited}
    for raw in MINUTE_RE.findall(claim.text):
        if not any(abs(int(raw) - m) <= 1 for m in minutes):
            return f"minute {raw} does not match the cited events"
    return None


def _mentions(text: str, full_name: str) -> bool:
    # Full name, or surname alone (how commentators usually refer to players).
    surname = full_name.split()[-1]
    return re.search(rf"\b({re.escape(full_name)}|{re.escape(surname)})\b", text) is not None


async def verify(moment: KeyMoment, claims: list[Claim], tools: MatchTools,
                 trace: list[dict]) -> list[Claim]:
    """Check the pending claims in place; already-decided claims are left alone."""
    claims = [c for c in claims if c.status == "pending"]
    for claim in claims:
        reason = hard_check(claim, tools)
        if reason:
            claim.status, claim.reason = "rejected", reason
    trace.append({"agent": "verifier", "step": "hard_checks",
                  "rejected": sum(c.status == "rejected" for c in claims)})

    pending = [c for c in claims if c.status == "pending"]
    if not pending:
        return claims

    payload = [
        {"index": i, "claim": c.text, "cited_events": tools.get_events_by_id(c.event_ids)}
        for i, c in enumerate(pending)
    ]
    stats = tools.team_stats(max(0, moment.minute - 15), moment.minute)
    user = (f"Moment: {moment.summary}\nWindow stats: {json.dumps(stats)}\n"
            f"Claims to check:\n{json.dumps(payload)}")
    result = await chat_json(get_settings().main_model_deployment, SYSTEM, user,
                             trace=trace, agent="verifier:judge")
    verdicts = {v.get("index"): v for v in result.get("verdicts", [])}
    for i, claim in enumerate(pending):
        verdict = verdicts.get(i, {"supported": False, "reason": "no verdict returned"})
        claim.status = "verified" if verdict.get("supported") else "rejected"
        claim.reason = verdict.get("reason")
    return claims
