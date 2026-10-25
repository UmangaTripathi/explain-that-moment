import statistics

import pytest

from app.datagen import generate, is_dramatic, xg_from
from app.detector import MomentDetector


@pytest.fixture(scope="module")
def demo_match():
    return generate(4)


def test_generator_is_deterministic():
    assert generate(11).model_dump() == generate(11).model_dump()


def test_generator_stats_are_football_realistic():
    matches = [generate(seed) for seed in range(1, 21)]
    shots = statistics.mean(sum(e.type == "shot" for e in m.events) for m in matches)
    goals = statistics.mean(sum(e.type == "goal" for e in m.events) for m in matches)
    assert 18 <= shots <= 35
    assert 1.5 <= goals <= 4.5


def test_xg_rises_closer_to_goal():
    assert xg_from(95, 50) > xg_from(85, 50) > xg_from(75, 50)
    assert xg_from(88, 50) > xg_from(88, 20)  # tighter angle, lower xG


def test_demo_match_is_dramatic(demo_match):
    assert is_dramatic(demo_match)


def test_detector_finds_every_goal_and_red_card(demo_match):
    moments = MomentDetector(demo_match).run()
    goals = [e for e in demo_match.events if e.type == "goal"]
    reds = [e for e in demo_match.events if e.type == "card" and e.outcome == "red"]
    assert sum(m.type == "goal" for m in moments) == len(goals)
    assert sum(m.type == "red_card" for m in moments) == len(reds)


def test_moment_evidence_points_at_real_events(demo_match):
    ids = {e.id for e in demo_match.events}
    for moment in MomentDetector(demo_match).run():
        assert moment.event_ids, moment
        assert set(moment.event_ids) <= ids


def test_detector_is_not_noisy(demo_match):
    moments = MomentDetector(demo_match).run()
    assert 5 <= len(moments) <= 25
