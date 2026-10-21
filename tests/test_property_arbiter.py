"""Hypothesis property test for the pure arbiter: invariant 2 (a), rewritten (b), (c).

Each step = one input + one evaluate() (the arbiter-step definition, R2-13).
After every step the independent oracle (tests/oracle.py) checks the commands
emitted in that step and the liveness of the last command.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from custom_components.halo.arbiter import Lease, PanelArbiter, normalise_snapshot
from custom_components.halo.const import (
    BEDROOM_SLEEP_MIN_LEVEL,
    LEVEL_EFFECT_MAP,
    ONE_SHOT_EFFECTS,
    SLEEP_MIN_LEVEL,
)

from .oracle import Oracle

IDS = ["a", "b", "c", "d"]
LEVEL_S = st.sampled_from(["info", "warn", "alert", "critical"])

STEP = st.one_of(
    st.tuples(st.just("indicate"), st.sampled_from(IDS), LEVEL_S, st.integers(1, 50)),
    st.tuples(st.just("release"), st.sampled_from(IDS)),
    st.tuples(st.just("tick"), st.integers(1, 30)),
    st.tuples(st.just("sleep"), st.booleans()),
    st.tuples(st.just("led"), st.booleans()),
    st.tuples(st.just("kill"), st.booleans()),
    st.tuples(st.just("oneshot")),
)


class Sim:
    """The HA glue, reduced: time, sleep, availability, kill switch, a bridge LED."""

    def __init__(self, bedroom: bool, initial: tuple[str, tuple | None]) -> None:
        self.arb = PanelArbiter("p", bedroom)
        self.oracle = Oracle(bedroom=bedroom)
        self.now = 0.0
        self.sleeping = False
        self.led_up = True
        self.kill_on = False
        self.led = initial  # what the bridge reports (state, rgb)
        self.seq = 0

    def visible(self) -> tuple[str, tuple | None]:
        return ("unavailable", None) if not self.led_up else self.led

    def output_ok(self) -> bool:
        return self.led_up and not self.kill_on

    def floor(self):
        return self.arb.floor_rank(self.sleeping, SLEEP_MIN_LEVEL, BEDROOM_SLEEP_MIN_LEVEL)

    def _apply(self, cmd):
        if cmd.kind == "show":
            self.led = ("on", cmd.rgb)  # bridge publishes on+colour, no effect
            return ("show", (cmd.effect, cmd.rgb))
        if cmd.snapshot_state == "on":
            self.led = ("on", cmd.rgb)
            return ("restore", ("solid", cmd.rgb))
        self.led = ("off", None)
        return ("restore", ("off", None))

    def run(self, step: tuple, i: int) -> None:
        kind = step[0]
        if kind == "indicate":
            _, lid, level, ttl = step
            effect, rgb4 = LEVEL_EFFECT_MAP[level]
            self.seq += 1
            vis = self.visible()
            self.oracle.indicate(lid, level, self.now + ttl, vis)
            self.arb.indicate(
                Lease("o", lid, level, effect, rgb4, self.now + ttl, self.seq,
                      effect in ONE_SHOT_EFFECTS),
                lambda: normalise_snapshot(vis[0], vis[1]),
            )
        elif kind == "release":
            self.oracle.release(step[1])
            self.arb.release(lambda lease, lid=step[1]: lease.id == lid)
        elif kind == "tick":
            self.now += step[1]
            self.oracle.expire(self.now)
            self.arb.expire(self.now)
        elif kind == "sleep":
            self.sleeping = step[1]
        elif kind == "led":
            came_back = step[1] and not self.led_up
            self.led_up = step[1]
            if came_back:
                vis = self.visible()
                self.arb.led_returned(lambda: normalise_snapshot(vis[0], vis[1]))
                self.oracle.output_returned(vis)
        elif kind == "kill":
            turned_on = (not step[1]) and self.kill_on
            turned_off = step[1] and not self.kill_on
            self.kill_on = step[1]
            if turned_on:
                self.arb.mark_dirty()
                self.oracle.output_returned()
            if turned_off:
                emitted = []
                if self.led_up:
                    cmd = self.arb.silence_command()
                    if cmd is not None:
                        emitted.append(self._apply(cmd))
                self.oracle.silenced(emitted, f"step {i} {step}")
                return
        elif kind == "oneshot":
            last = self.arb.last_sent
            if last is None or not self.arb.one_shot_elapsed(last.lease):
                return

        cmd = self.arb.evaluate(self.floor(), self.output_ok())
        emitted = [self._apply(cmd)] if cmd is not None else []
        self.oracle.check_step(emitted, self.sleeping, self.output_ok(), f"step {i} {step}")


INITIAL = st.sampled_from(
    [("off", None), ("unknown", None), ("on", (0, 255, 0)), ("on", None)]
)


@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(bedroom=st.booleans(), initial=INITIAL, steps=st.lists(STEP, max_size=40))
def test_arbiter_invariants_hold_for_any_sequence(bedroom, initial, steps) -> None:
    sim = Sim(bedroom, initial)
    for i, step in enumerate(steps):
        sim.run(step, i)
        assert not sim.oracle.violations, sim.oracle.violations
    # drain: release everything with output possible -> must end on the restore
    sim.led_up, sim.kill_on = True, False
    sim.arb.mark_dirty()
    sim.oracle.output_returned()
    for lid in IDS:
        sim.run(("release", lid), 999)
    assert not sim.oracle.violations, sim.oracle.violations
    assert not sim.oracle.shown_since_restore
