"""Synthetic, football-realistic match generator.

Simulates possession sequences (passes, carries, dribbles, pressing, turnovers),
shots with an xG model, fouls, cards, corners and substitutions. A time-varying
momentum curve per team makes the match tell a story (spells of dominance,
comebacks), which is what the moment detector and agents feed on.

Usage:
    python -m app.datagen --seed 7 --out data/match_001.json
    python -m app.datagen --dramatic --out data/match_001.json   # search seeds for a good story
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from app.models import Match, MatchEvent, Player, Team

FORMATION_433 = ["GK", "RB", "CB", "CB", "LB", "DM", "CM", "CM", "RW", "ST", "LW"]
FORMATION_4231 = ["GK", "RB", "CB", "CB", "LB", "DM", "DM", "AM", "RW", "ST", "LW"]
BENCH = ["CM", "FW", "DF"]

HOME_NAMES = [
    "Tom Harlow", "Kieran Doyle", "Marcus Webb", "Elias Brandt", "Josh Okafor", "Luca Ferri",
    "Sam Whitlock", "Dani Ortega", "Rafael Lima", "Callum Price", "Yusuf Adeyemi",
    "Ben Carver", "Theo Lindqvist", "Ollie Grant",
]
AWAY_NAMES = [
    "Mateo Ruiz", "Jonas Keller", "Andre Matos", "Kwame Mensah", "Leo Hart", "Ivan Petrov",
    "Noah Brennan", "Hugo Lefevre", "Sami Haddad", "Ryan Maddox", "Kenji Sato",
    "Alfie Moore", "Emeka Nwosu", "Felix Sorensen",
]

# Which positions tend to perform which actions.
ACTION_WEIGHTS = {
    "build": {"GK": 1, "CB": 6, "RB": 4, "LB": 4, "DM": 6, "CM": 6, "AM": 4, "RW": 2, "LW": 2,
              "ST": 1, "FW": 1, "DF": 4},
    "attack": {"GK": 0, "CB": 1, "RB": 3, "LB": 3, "DM": 2, "CM": 4, "AM": 6, "RW": 7, "LW": 7,
               "ST": 6, "FW": 6, "DF": 1},
    "shoot": {"GK": 0, "CB": 1, "RB": 1, "LB": 1, "DM": 1, "CM": 3, "AM": 5, "RW": 5, "LW": 5,
              "ST": 9, "FW": 8, "DF": 1},
    "defend": {"GK": 0, "CB": 7, "RB": 5, "LB": 5, "DM": 7, "CM": 4, "AM": 2, "RW": 2, "LW": 2,
               "ST": 1, "FW": 1, "DF": 6},
}


def xg_from(x: float, y: float, header: bool = False) -> float:
    """Simple distance/angle xG model on a 100x100 pitch (goal at x=100, y=50)."""
    dx = (100 - x) * 1.05  # ~105m pitch
    dy = (y - 50) * 0.68  # ~68m wide
    dist = math.hypot(dx, dy)
    # Angle subtended by a 7.32m goal mouth.
    angle = math.atan2(7.32 * dx, dx * dx + dy * dy - (7.32 / 2) ** 2)
    angle = angle if angle > 0 else angle + math.pi
    logit = -1.6 - 0.11 * dist + 1.6 * angle - (0.6 if header else 0)
    return round(min(0.95, max(0.01, 1 / (1 + math.exp(-logit)))), 3)


@dataclass
class TeamState:
    team: Team
    on_pitch: list[Player]
    bench: list[Player]
    strength: float
    red_cards: int = 0
    yellows: set[str] = field(default_factory=set)
    subs_made: int = 0
    goals: int = 0


class Simulator:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.seed = seed
        self.events: list[MatchEvent] = []
        self.minute = 0
        self.second = 0
        self.period = 1
        self.sequence = 0
        # Story knobs, randomized per seed: who starts on top, when the swing comes.
        self.swing_minute = self.rng.randint(52, 70)
        self.home_first = self.rng.random() < 0.6

    # -- setup -------------------------------------------------------------
    def make_team(self, tid: str, name: str, short: str, formation: str,
                  names: list[str], strength: float) -> TeamState:
        positions = FORMATION_433 if formation == "4-3-3" else FORMATION_4231
        players = [
            Player(id=f"{tid}-{i + 1}", name=n, number=i + 1, position=p)
            for i, (n, p) in enumerate(zip(names, positions + BENCH, strict=False))
        ]
        team = Team(id=tid, name=name, short=short, formation=formation, players=players)
        return TeamState(team=team, on_pitch=players[:11], bench=players[11:], strength=strength)

    # -- helpers -----------------------------------------------------------
    def tick(self, lo: int = 2, hi: int = 6) -> None:
        self.second += self.rng.randint(lo, hi)
        while self.second >= 60:
            self.second -= 60
            self.minute += 1

    def emit(self, **kw) -> MatchEvent:
        ev = MatchEvent(
            id=f"e{len(self.events) + 1:05d}", minute=self.minute, second=self.second,
            period=self.period, sequence=self.sequence, **kw,
        )
        self.events.append(ev)
        return ev

    def pick(self, ts: TeamState, role: str, exclude: str | None = None) -> Player:
        pool = [p for p in ts.on_pitch if p.id != exclude]
        weights = [ACTION_WEIGHTS[role].get(p.position, 1) + 0.01 for p in pool]
        return self.rng.choices(pool, weights=weights)[0]

    def momentum(self, ts: TeamState, is_home: bool) -> float:
        """Team's current edge: base strength + story curve - red card penalty."""
        before = self.minute < self.swing_minute
        on_top_early = is_home == self.home_first
        story = 0.12 if (before == on_top_early) else -0.06
        score_diff = ts.goals - (self.away.goals if is_home else self.home.goals)
        chasing = 0.06 if score_diff < 0 else (-0.04 if score_diff > 0 else 0)
        return ts.strength + story + chasing - 0.35 * ts.red_cards + (0.05 if is_home else 0)

    @staticmethod
    def flip(v: float | None) -> float | None:
        return None if v is None else round(100 - v, 1)

    # -- match flow ----------------------------------------------------------
    def run(self) -> Match:
        self.home = self.make_team("NBR", "Northbridge FC", "NBR", "4-3-3", HOME_NAMES, 0.55)
        self.away = self.make_team("RVA", "Riverside Athletic", "RVA", "4-2-3-1", AWAY_NAMES, 0.5)

        for period, end in ((1, 45 + self.rng.randint(1, 4)), (2, 90 + self.rng.randint(3, 6))):
            self.period = period
            self.minute, self.second = (0, 0) if period == 1 else (45, 0)
            kicker = self.home if period == 1 else self.away
            self.emit(type="kickoff", team=kicker.team.id, x=50, y=50)
            possession, x, y = kicker, 50.0, 50.0
            while self.minute < end:
                self.maybe_substitute()
                possession, x, y = self.play_sequence(possession, x, y)
            self.emit(type="half_time" if period == 1 else "full_time")

        return Match(
            id=f"synthetic-{self.seed}", competition="Synthetic Premier Fixture",
            date="2026-10-18", venue="Northbridge Park", home=self.home.team,
            away=self.away.team, events=self.events,
        )

    def other(self, ts: TeamState) -> TeamState:
        return self.away if ts is self.home else self.home

    def play_sequence(self, att: TeamState, x: float, y: float) -> tuple[TeamState, float, float]:
        self.sequence += 1
        dfn = self.other(att)
        edge = self.momentum(att, att is self.home) - self.momentum(dfn, dfn is self.home)
        carrier = self.pick(att, "build" if x < 50 else "attack")

        for _ in range(self.rng.randint(3, 18)):
            self.tick()
            # Shooting zone.
            if x > 72 and self.rng.random() < 0.016 + 0.003 * (x - 72) + 0.04 * max(edge, 0):
                return self.shot(att, x, y)

            # Defensive action: pressure, tackle, interception or foul.
            press_risk = 0.10 + (0.08 if x > 66 else 0) - 0.12 * edge
            if self.rng.random() < max(0.03, press_risk):
                return self.defend(att, dfn, carrier, x, y)

            action = self.rng.choices(["pass", "carry", "dribble"], [0.72, 0.22, 0.06])[0]
            if action == "pass":
                fwd = self.rng.gauss(6 + 8 * edge, 9)
                nx = min(99.0, max(1.0, x + fwd))
                ny = min(99.0, max(1.0, y + self.rng.gauss(0, 18)))
                success_p = 0.9 - 0.004 * max(0, nx - 60) + 0.1 * edge
                receiver = self.pick(att, "attack" if nx > 55 else "build", exclude=carrier.id)
                ok = self.rng.random() < success_p
                self.emit(type="pass", team=att.team.id, player=carrier.id, x=round(x, 1),
                          y=round(y, 1), end_x=round(nx, 1), end_y=round(ny, 1),
                          outcome="success" if ok else "fail", related_player=receiver.id)
                if not ok:
                    self.tick(1, 2)
                    interceptor = self.pick(dfn, "defend")
                    self.emit(type="interception", team=dfn.team.id, player=interceptor.id,
                              x=self.flip(nx), y=self.flip(ny), outcome="success")
                    return dfn, 100 - nx, 100 - ny
                carrier, x, y = receiver, nx, ny
            elif action == "carry":
                nx = min(99.0, x + abs(self.rng.gauss(5, 4)))
                ny = min(99.0, max(1.0, y + self.rng.gauss(0, 6)))
                self.emit(type="carry", team=att.team.id, player=carrier.id, x=round(x, 1),
                          y=round(y, 1), end_x=round(nx, 1), end_y=round(ny, 1),
                          outcome="success")
                x, y = nx, ny
            else:
                ok = self.rng.random() < 0.55 + 0.2 * edge
                self.emit(type="dribble", team=att.team.id, player=carrier.id, x=round(x, 1),
                          y=round(y, 1), outcome="success" if ok else "fail")
                if not ok:
                    tackler = self.pick(dfn, "defend")
                    self.emit(type="tackle", team=dfn.team.id, player=tackler.id,
                              x=self.flip(x), y=self.flip(y), outcome="success",
                              related_player=carrier.id)
                    return dfn, 100 - x, 100 - y
                x = min(99.0, x + 4)

        # Sequence fizzled out: recycled possession or a loose ball to the opponent.
        if self.rng.random() < 0.5:
            return att, max(20.0, x - 25), 50.0
        return dfn, 100 - x, 100 - y

    def defend(self, att, dfn, carrier, x, y):
        defender = self.pick(dfn, "defend" if x < 70 else "attack")
        self.emit(type="pressure", team=dfn.team.id, player=defender.id,
                  x=self.flip(x), y=self.flip(y), related_player=carrier.id)
        self.tick(1, 2)
        roll = self.rng.random()
        if roll < 0.14:  # foul
            self.emit(type="foul", team=dfn.team.id, player=defender.id, x=self.flip(x),
                      y=self.flip(y), related_player=carrier.id)
            self.maybe_card(dfn, defender, x)
            return att, x, y
        if roll < 0.62:  # ball won
            kind = "tackle" if self.rng.random() < 0.6 else "interception"
            self.emit(type=kind, team=dfn.team.id, player=defender.id, x=self.flip(x),
                      y=self.flip(y), outcome="success", related_player=carrier.id)
            return dfn, 100 - x, 100 - y
        self.emit(type="tackle", team=dfn.team.id, player=defender.id, x=self.flip(x),
                  y=self.flip(y), outcome="fail", related_player=carrier.id)
        return att, min(99.0, x + 3), y

    def maybe_card(self, dfn: TeamState, player: Player, x: float) -> None:
        p_card = 0.18 + (0.15 if x > 70 else 0)
        if self.rng.random() >= p_card:
            return
        # Rare straight red, or a second yellow.
        straight_red = self.rng.random() < 0.04
        second_yellow = player.id in dfn.yellows
        colour = "red" if (straight_red or second_yellow) else "yellow"
        self.emit(type="card", team=dfn.team.id, player=player.id, outcome=colour)
        if colour == "red":
            dfn.red_cards += 1
            dfn.on_pitch = [p for p in dfn.on_pitch if p.id != player.id]
        else:
            dfn.yellows.add(player.id)

    def shot(self, att: TeamState, x: float, y: float) -> tuple[TeamState, float, float]:
        dfn = self.other(att)
        shooter = self.pick(att, "shoot")
        # Final ball pushes the shot location into the box most of the time.
        sx = min(95.0, x + self.rng.uniform(-4, 9))
        sy = min(80.0, max(20.0, 50 + (y - 50) * self.rng.uniform(0.3, 0.8)))
        header = self.rng.random() < 0.15
        xg = xg_from(sx, sy, header)
        roll = self.rng.random()
        if roll < xg:
            outcome = "goal"
        elif roll < xg + 0.28:
            outcome = "on_target"
        elif roll < xg + 0.55:
            outcome = "blocked"
        else:
            outcome = "off_target"
        self.emit(type="shot", team=att.team.id, player=shooter.id, x=round(sx, 1),
                  y=round(sy, 1), xg=xg, outcome=outcome)
        self.tick(1, 3)
        if outcome == "goal":
            att.goals += 1
            self.emit(type="goal", team=att.team.id, player=shooter.id, x=round(sx, 1),
                      y=round(sy, 1), xg=xg, outcome=f"{self.home.goals}-{self.away.goals}")
            self.tick(30, 60)
            self.emit(type="kickoff", team=dfn.team.id, x=50, y=50)
            return dfn, 50.0, 50.0
        if outcome == "on_target":
            gk = next((p for p in dfn.on_pitch if p.position == "GK"), dfn.on_pitch[0])
            self.emit(type="save", team=dfn.team.id, player=gk.id, x=self.flip(sx),
                      y=self.flip(sy), outcome="success", related_player=shooter.id)
            if self.rng.random() < 0.35:
                return self.corner(att)
            return dfn, 8.0, 50.0
        if outcome == "blocked" and self.rng.random() < 0.45:
            return self.corner(att)
        return dfn, 6.0, 50.0

    def corner(self, att: TeamState) -> tuple[TeamState, float, float]:
        self.tick(15, 30)
        taker = self.pick(att, "attack")
        cy = self.rng.choice([1.0, 99.0])
        self.emit(type="corner", team=att.team.id, player=taker.id, x=99.0, y=cy)
        if self.rng.random() < 0.3:
            return self.shot(att, 86, 50)
        return att, 75.0, cy

    def maybe_substitute(self) -> None:
        for ts in (self.home, self.away):
            windows = (58, 70, 80)
            if ts.subs_made >= len(windows) or not ts.bench:
                continue
            if self.minute < windows[ts.subs_made] + (ts is self.away):
                continue
            outfield = [p for p in ts.on_pitch if p.position != "GK"]
            off = self.rng.choice(outfield)
            on = ts.bench.pop(0)
            ts.on_pitch = [on if p.id == off.id else p for p in ts.on_pitch]
            ts.subs_made += 1
            # A fresh attacker lifts the team a little (gives the detector something to find).
            if on.position == "FW":
                ts.strength += 0.08
            self.emit(type="substitution", team=ts.team.id, player=off.id, related_player=on.id)


def is_dramatic(match: Match) -> bool:
    """A demo-worthy match: 3+ goals, a lead change or late equaliser, and not a rout."""
    goals = [e for e in match.events if e.type == "goal"]
    if not 3 <= len(goals) <= 6:
        return False
    home = away = 0
    lead_changes, late_goal = 0, False
    leader = None
    for g in goals:
        if g.team == match.home.id:
            home += 1
        else:
            away += 1
        now = "H" if home > away else "A" if away > home else None
        if now and leader and now != leader:
            lead_changes += 1
        if now:
            leader = now
        late_goal = late_goal or g.minute >= 80
    return abs(home - away) <= 1 and (lead_changes >= 1 or late_goal)


def generate(seed: int) -> Match:
    return Simulator(seed).run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--dramatic", action="store_true", help="search seeds for a good story")
    parser.add_argument("--out", type=Path, default=Path("data/match_001.json"))
    args = parser.parse_args()

    seed = args.seed
    match = generate(seed)
    if args.dramatic:
        while not is_dramatic(match):
            seed += 1
            match = generate(seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(match.model_dump(), indent=1))
    goals = [e for e in match.events if e.type == "goal"]
    print(f"seed={seed} events={len(match.events)} goals="
          + ", ".join(f"{g.minute}' {g.team} ({g.outcome})" for g in goals))


if __name__ == "__main__":
    main()
