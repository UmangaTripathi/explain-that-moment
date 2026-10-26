"""Read-only match data tools. Agents call these instead of answering from memory,
and every fact they return carries event IDs that claims can cite."""

from __future__ import annotations

from collections import Counter

from app.models import Match, MatchEvent


class MatchTools:
    def __init__(self, match: Match):
        self.match = match
        self.by_id = {e.id: e for e in match.events}
        self.names = {p.id: p.name for t in (match.home, match.away) for p in t.players}
        self.positions = {p.id: p.position for t in (match.home, match.away) for p in t.players}
        self.team_names = {match.home.id: match.home.name, match.away.id: match.away.name}
        self.player_ids = {p.name.lower(): p.id for t in (match.home, match.away) for p in t.players}

    # -- formatting ----------------------------------------------------------
    def describe(self, e: MatchEvent) -> dict:
        """Compact, human-readable event used in prompts and the evidence drawer."""
        d = {
            "id": e.id,
            "time": f"{e.minute}:{e.second:02d}",
            "team": self.team_names.get(e.team or "", None),
            "player": self.names.get(e.player or "", None),
            "type": e.type,
        }
        for key in ("outcome", "xg", "x", "y", "end_x", "end_y"):
            value = getattr(e, key)
            if value is not None:
                d[key] = value
        if e.related_player:
            d["related_player"] = self.names.get(e.related_player)
        return d

    # -- tools ----------------------------------------------------------------
    def get_events(self, start_minute: int, end_minute: int, team: str | None = None,
                   types: list[str] | None = None, limit: int = 40) -> list[dict]:
        team_id = self._team_id(team)
        events = [
            e for e in self.match.events
            if start_minute <= e.minute <= end_minute
            and (team_id is None or e.team == team_id)
            and (not types or e.type in types)
        ]
        # Prefer the most informative events when the window is large.
        if len(events) > limit:
            key = [e for e in events if e.type not in ("pass", "carry", "pressure")]
            events = key[-limit:] if len(key) >= limit else (key + events[-(limit - len(key)):])
            events.sort(key=lambda e: e.id)
        return [self.describe(e) for e in events]

    def get_events_by_id(self, event_ids: list[str]) -> list[dict]:
        return [self.describe(self.by_id[i]) for i in event_ids if i in self.by_id]

    def team_stats(self, start_minute: int = 0, end_minute: int = 200) -> dict:
        window = [e for e in self.match.events if start_minute <= e.minute <= end_minute]
        out = {}
        total_passes = sum(e.type == "pass" for e in window) or 1
        for team_id, name in self.team_names.items():
            ev = [e for e in window if e.team == team_id]
            passes = [e for e in ev if e.type == "pass"]
            shots = [e for e in ev if e.type == "shot"]
            out[name] = {
                "goals": sum(e.type == "goal" for e in ev),
                "shots": len(shots),
                "shots_on_target": sum(s.outcome in ("on_target", "goal") for s in shots),
                "xg": round(sum(s.xg or 0 for s in shots), 2),
                "passes": len(passes),
                "pass_accuracy_pct": round(
                    100 * sum(p.outcome == "success" for p in passes) / (len(passes) or 1)),
                "possession_pct": round(100 * len(passes) / total_passes),
                "final_third_entries": sum(
                    p.outcome == "success" and (p.end_x or 0) > 66 and (p.x or 0) <= 66
                    for p in passes),
                "high_regains": sum(
                    e.type in ("tackle", "interception") and e.outcome == "success"
                    and (e.x or 0) >= 66 for e in ev),
                "fouls": sum(e.type == "foul" for e in ev),
                "yellow_cards": sum(e.type == "card" and e.outcome == "yellow" for e in ev),
                "red_cards": sum(e.type == "card" and e.outcome in ("red", "second_yellow") for e in ev),
                "corners": sum(e.type == "corner" for e in ev),
            }
        return {"window": f"{start_minute}-{end_minute}'", "teams": out}

    def player_stats(self, player_name: str, start_minute: int = 0,
                     end_minute: int = 200) -> dict:
        pid = self.player_ids.get(player_name.lower())
        if not pid:
            return {"error": f"unknown player '{player_name}'",
                    "known_players": sorted(n.title() for n in self.player_ids)}
        ev = [e for e in self.match.events
              if e.player == pid and start_minute <= e.minute <= end_minute]
        counts = Counter(e.type for e in ev)
        passes = [e for e in ev if e.type == "pass"]
        shots = [e for e in ev if e.type == "shot"]
        return {
            "player": self.names[pid],
            "position": self.positions[pid],
            "window": f"{start_minute}-{end_minute}'",
            "actions": dict(counts),
            "pass_accuracy_pct": round(
                100 * sum(p.outcome == "success" for p in passes) / (len(passes) or 1)),
            "xg": round(sum(s.xg or 0 for s in shots), 2),
            "key_event_ids": [e.id for e in ev if e.type in ("shot", "goal", "card", "tackle")][-8:],
        }

    def score_at(self, minute: int) -> str:
        home = sum(e.type == "goal" and e.team == self.match.home.id and e.minute <= minute
                   for e in self.match.events)
        away = sum(e.type == "goal" and e.team == self.match.away.id and e.minute <= minute
                   for e in self.match.events)
        return f"{self.match.home.name} {home}-{away} {self.match.away.name}"

    def _team_id(self, team: str | None) -> str | None:
        if not team:
            return None
        for tid, name in self.team_names.items():
            if team.lower() in (tid.lower(), name.lower()):
                return tid
        return None


# OpenAI tool schemas (shared by every agent that can look things up).
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "List match events in a minute window. Each has an id you can cite.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_minute": {"type": "integer"},
                    "end_minute": {"type": "integer"},
                    "team": {"type": "string", "description": "Team name, optional"},
                    "types": {"type": "array", "items": {"type": "string"},
                              "description": "Filter e.g. ['shot','tackle'], optional"},
                },
                "required": ["start_minute", "end_minute"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "team_stats",
            "description": "Aggregate stats per team (xG, shots, possession, pressing, cards) "
                           "for a minute window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_minute": {"type": "integer"},
                    "end_minute": {"type": "integer"},
                },
                "required": ["start_minute", "end_minute"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "player_stats",
            "description": "One player's actions, pass accuracy and xG in a minute window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player_name": {"type": "string"},
                    "start_minute": {"type": "integer"},
                    "end_minute": {"type": "integer"},
                },
                "required": ["player_name"],
            },
        },
    },
]


def call_tool(tools: MatchTools, name: str, args: dict):
    if name == "get_events":
        return tools.get_events(**args)
    if name == "team_stats":
        return tools.team_stats(**args)
    if name == "player_stats":
        return tools.player_stats(**args)
    return {"error": f"unknown tool {name}"}
