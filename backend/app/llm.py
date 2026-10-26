"""Azure OpenAI (Microsoft Foundry) client with keyless auth and a tool-calling loop."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI

from app.config import get_settings

log = logging.getLogger(__name__)

API_VERSION = "2025-04-01-preview"
MAX_TOOL_ROUNDS = 5


@lru_cache
def client() -> AsyncAzureOpenAI:
    settings = get_settings()
    # Locally this is your `az login`; in Azure it is the backend's managed identity
    # (AZURE_CLIENT_ID selects the user-assigned identity).
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default")
    return AsyncAzureOpenAI(azure_endpoint=settings.azure_openai_endpoint,
                            azure_ad_token_provider=token_provider, api_version=API_VERSION)


def _is_reasoning(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


async def chat_json(
    model: str,
    system: str,
    user: str,
    *,
    tools: list[dict] | None = None,
    run_tool: Callable[[str, dict], object] | None = None,
    trace: list[dict] | None = None,
    agent: str = "agent",
) -> dict:
    """Run a chat turn (with tool calls if given) and parse the final JSON answer."""
    messages: list[dict] = [{"role": "system", "content": system},
                            {"role": "user", "content": user}]
    extra = {"reasoning_effort": "low"} if _is_reasoning(model) else {"temperature": 0.4}
    started = time.perf_counter()

    for round_ in range(MAX_TOOL_ROUNDS + 1):
        # On the last round, withhold tools so the model must answer.
        round_tools = tools if tools and round_ < MAX_TOOL_ROUNDS else None
        response = await client().chat.completions.create(
            model=model, messages=messages, tools=round_tools,
            response_format={"type": "json_object"}, **extra,
        )
        msg = response.choices[0].message
        if not msg.tool_calls:
            if trace is not None:
                trace.append({"agent": agent, "step": "answer",
                              "ms": round((time.perf_counter() - started) * 1000)})
            return json.loads(msg.content or "{}")

        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            result = run_tool(call.function.name, args) if run_tool else {"error": "no tools"}
            if trace is not None:
                trace.append({"agent": agent, "step": "tool", "tool": call.function.name,
                              "args": args})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, default=str)})

    raise RuntimeError(f"{agent}: no answer after {MAX_TOOL_ROUNDS} tool rounds")
