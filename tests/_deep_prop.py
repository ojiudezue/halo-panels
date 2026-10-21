"""Ad-hoc deep run (not collected): python -m tests._deep_prop N"""
import sys
from hypothesis import given, settings, strategies as st
from tests.test_property_arbiter import INITIAL, STEP, Sim, IDS

@settings(max_examples=int(sys.argv[1]) if len(sys.argv) > 1 else 20000, deadline=None)
@given(bedroom=st.booleans(), initial=INITIAL, steps=st.lists(STEP, max_size=80))
def run(bedroom, initial, steps):
    sim = Sim(bedroom, initial)
    for i, step in enumerate(steps):
        sim.run(step, i)
        assert not sim.oracle.violations, sim.oracle.violations
    sim.led_up, sim.kill_on = True, False
    sim.arb.mark_dirty(); sim.oracle.output_returned()
    for lid in IDS:
        sim.run(("release", lid), 999)
    assert not sim.oracle.violations, sim.oracle.violations
    assert not sim.oracle.shown_since_restore

run(); print("deep ok")
