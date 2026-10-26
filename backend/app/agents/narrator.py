"""Narrator agent: turns verified claims into fan-ready text for a persona and language.
It may only rephrase verified facts; it never adds new ones."""

from __future__ import annotations

from app.config import get_settings
from app.llm import chat_json
from app.models import Claim, KeyMoment

PERSONAS = {
    "casual": "a casual fan: warm, vivid, no jargon, explain any football term in plain words",
    "tactical": "a tactics enthusiast: precise, uses terms like xG, high press, final third, "
                "rest defence, and focuses on why it worked",
}

LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese",
    "hi": "Hindi", "ar": "Arabic",
}

SYSTEM = """You are a live football commentator writing a short moment card.
Write for {persona}. Write in {language}.

Hard rule: use ONLY the facts in the verified claims and the moment summary. Do not add
players, numbers, or events that are not there. Team and player names stay as given.

Respond with JSON: {{"headline": "max 8 words", "narrative": "2-3 sentences"}}"""


async def narrate(moment: KeyMoment, claims: list[Claim], score: str, persona: str,
                  language: str, trace: list[dict]) -> dict:
    system = SYSTEM.format(persona=PERSONAS.get(persona, PERSONAS["casual"]),
                           language=LANGUAGES.get(language, "English"))
    facts = "\n".join(f"- {c.text}" for c in claims if c.status == "verified")
    user = (f"Moment at {moment.minute}': {moment.summary}\nScore: {score}\n"
            f"Verified claims:\n{facts or '- (none beyond the summary)'}")
    result = await chat_json(get_settings().mini_model_deployment, system, user,
                             trace=trace, agent="narrator")
    return {"headline": result.get("headline", moment.summary),
            "narrative": result.get("narrative", "")}
