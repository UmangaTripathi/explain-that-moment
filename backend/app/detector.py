"""Rule-based key-moment detector.

Deterministic and LLM-free: it decides *what* is worth explaining, so the agents
only spend tokens on moments that matter. Works incrementally: call feed() with
each event as it arrives and collect the moments it returns.
"""

from __future__ import annotations

from collections import deque

from app.models import KeyMoment, Match, MatchEvent

BIG_CHANCE_XG = 0.25
MOMENTUM_WINDOW_MIN = 10
MOMENTUM_DOMINANT = 0.68  # share of threat needed to call a team dominant
MOMENTUM_COOLDOWN_MIN = 12
PRESS_ZONE_X = 66  # ball won this far up the pitch counts as a high regain
PRESS_TO_SHOT_EVENTS = 8
PRESS_MIN_XG = 0.1  # the regain must lead to a real chance
PRESS_COOLDOWN_MIN = 10


class MomentDetector:
    def __init__(self, match: Match):
        self.match = match
        self.names = {p.id: p.name for t in (match.home, match.away) for p in t.players}
        self.team_names = {match.home.id: match.home.name, match.away.id: match.away.name}
        self.recent: deque[MatchEvent] = deque()  # events in the momentum window
        self.subs_on: dict[str, MatchEvent] = {}  # player id -> substitution event
        self.high_regains: dict[str, MatchEvent] = {}  # team -> latest high ball win
        self.events_since_regain: dict[str, int] = {}
        self.dominant: str | None = None
        self.last_swing_minute = -MOMENTUM_COOLDOWN_MIN
        self.last_minute_checked = -1
        self.last_press: dict[str, int] = {}  # team -> minute of last pressing moment
        self.count = 0

    # -- public ------------------------------------------------------------
    def feed(self, ev: MatchEvent) -> list[KeyMoment]:
        moments: list[KeyMoment] = []
        self._track_window(ev)

        if ev.type == "substitution" and ev.related_player:
            self.subs_on[ev.related_player] = ev

        if ev.type in ("tackle", "interception") and ev.outcome == "success" and ev.team:
            if (ev.x or 0) >= PRESS_ZONE_X:
                self.high_regains[ev.team] = ev
                self.events_since_regain[ev.team] = 0
        for team in list(self.events_since_regain):
            self.events_since_regain[team] += 1
            if self.events_since_regain[team] > PRESS_TO_SHOT_EVENTS:
                self.high_regains.pop(team, None)
                self.events_since_regain.pop(team, None)

        if ev.type == "goal":
            moments.append(self._goal(ev))
            if ev.player in self.subs_on:
                moments.append(self._super_sub(ev))
        elif ev.type == "shot" and ev.outcome != "goal" and (ev.xg or 0) >= BIG_CHANCE_XG:
            moments.append(self._big_chance(ev))
        elif ev.type == "card" and ev.outcome == "red":
            moments.append(self._red_card(ev))

        if ev.type == "shot" and ev.team in self.high_regains:
            regain = self.high_regains.pop(ev.team)
            self.events_since_regain.pop(ev.team, None)
            last = self.last_press.get(ev.team, -PRESS_COOLDOWN_MIN)
            cooled = ev.minute - last >= PRESS_COOLDOWN_MIN
            if (ev.xg or 0) >= PRESS_MIN_XG and cooled:
                self.last_press[ev.team] = ev.minute
                moments.append(self._pressing_win(regain, ev))

        if ev.minute != self.last_minute_checked:
            self.last_minute_checked = ev.minute
            swing = self._momentum(ev)
            if swing:
                moments.append(swing)
        return moments

    def run(self) -> list[KeyMoment]:
        return [m for ev in self.match.events for m in self.feed(ev)]

    # -- rules -------------------------------------------------------------
    def _new(self, ev: MatchEvent, type_: str, event_ids: list[str], importance: float,
             summary: str, team: str | None = None) -> KeyMoment:
        self.count += 1
        return KeyMoment(
            id=f"m{self.count:03d}", minute=ev.minute, type=type_, team=team or ev.team,
            event_ids=event_ids, importance=round(min(1.0, importance), 2), summary=summary,
        )

    def _buildup(self, ev: MatchEvent, n: int = 6) -> list[str]:
        """IDs of the last few events of the same possession sequence."""
        seq = [e.id for e in self.recent if e.sequence == ev.sequence and e.id != ev.id]
        return seq[-n:]

    def _goal(self, ev: MatchEvent) -> KeyMoment:
        shot = next((e for e in reversed(self.recent) if e.type == "shot" and e.id != ev.id), None)
        ids = self._buildup(shot or ev) + ([shot.id] if shot else []) + [ev.id]
        who = self.names.get(ev.player or "", "Unknown")
        return self._new(
            ev, "goal", ids, 1.0,
            f"GOAL {self.team_names[ev.team]}: {who} scores (xG {ev.xg}), score now {ev.outcome}",
        )

    def _super_sub(self, ev: MatchEvent) -> KeyMoment:
        sub = self.subs_on[ev.player]
        who = self.names.get(ev.player or "", "Unknown")
        return self._new(
            ev, "key_substitution", [sub.id, ev.id], 0.8,
            f"Substitute {who} (on {sub.minute}') scores {ev.minute - sub.minute} minutes later",
        )

    def _big_chance(self, ev: MatchEvent) -> KeyMoment:
        who = self.names.get(ev.player or "", "Unknown")
        follow = [e.id for e in self.recent if e.type == "save" and e.minute == ev.minute]
        return self._new(
            ev, "big_chance", self._buildup(ev, 4) + [ev.id] + follow[-1:],
            0.45 + (ev.xg or 0),
            f"Big chance missed: {who} ({self.team_names[ev.team]}) xG {ev.xg}, {ev.outcome}",
        )

    def _red_card(self, ev: MatchEvent) -> KeyMoment:
        who = self.names.get(ev.player or "", "Unknown")
        foul = next((e for e in reversed(self.recent) if e.type == "foul"), None)
        ids = ([foul.id] if foul else []) + [ev.id]
        return self._new(
            ev, "red_card", ids, 0.9,
            f"RED CARD: {who} ({self.team_names[ev.team]}) sent off, down to 10 men",
        )

    def _pressing_win(self, regain: MatchEvent, shot: MatchEvent) -> KeyMoment:
        who = self.names.get(regain.player or "", "Unknown")
        ids = [e.id for e in self.recent
               if regain.id <= e.id <= shot.id and e.team == shot.team][-8:]
        return self._new(
            shot, "pressing_win", ids, 0.5 + (shot.xg or 0) / 2,
            f"High press pays off: {who} wins it at x={regain.x}, shot follows (xG {shot.xg})",
        )

    def _momentum(self, ev: MatchEvent) -> KeyMoment | None:
        if ev.minute < 15 or ev.minute - self.last_swing_minute < MOMENTUM_COOLDOWN_MIN:
            return None
        threat = {self.match.home.id: 0.0, self.match.away.id: 0.0}
        for e in self.recent:
            if not e.team:
                continue
            if e.type == "shot":
                threat[e.team] += e.xg or 0
            elif e.type == "pass" and e.outcome == "success" and (e.end_x or 0) > 66:
                threat[e.team] += 0.02  # final-third entry
        total = sum(threat.values())
        if total < 0.3:
            return None
        home_share = threat[self.match.home.id] / total
        leader = (self.match.home.id if home_share >= MOMENTUM_DOMINANT else
                  self.match.away.id if home_share <= 1 - MOMENTUM_DOMINANT else None)
        if not leader or leader == self.dominant:
            return None
        previous, self.dominant = self.dominant, leader
        self.last_swing_minute = ev.minute
        share = home_share if leader == self.match.home.id else 1 - home_share
        ids = [e.id for e in self.recent
               if e.team == leader and (e.type == "shot" or
                                        (e.type == "pass" and (e.end_x or 0) > 66))][-10:]
        verb = "seize control" if previous is None else "swing momentum their way"
        return self._new(
            ev, "momentum_swing", ids, 0.55 + (share - MOMENTUM_DOMINANT),
            f"{self.team_names[leader]} {verb}: {share:.0%} of attacking threat in the last "
            f"{MOMENTUM_WINDOW_MIN} minutes (xG {sum(threat.values()) * share:.2f})",
            team=leader,
        )

    # -- internals ---------------------------------------------------------
    def _track_window(self, ev: MatchEvent) -> None:
        self.recent.append(ev)
        while self.recent and self.recent[0].minute < ev.minute - MOMENTUM_WINDOW_MIN:
            self.recent.popleft()
