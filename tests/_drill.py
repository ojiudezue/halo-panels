"""Mutation drill runner (not collected). python tests/_drill.py
Each mutant: write mutated source, clear __pycache__, run the suite, restore in finally."""

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
A = "custom_components/halo/arbiter.py"
R = "custom_components/halo/runtime.py"
C = "custom_components/halo/const.py"
S = "custom_components/halo/switch.py"

MUTANTS = [
    ("M1 winner by recency only (last-writer-wins)", A,
     "key=lambda lease: (lease.rank, lease.seq))", "key=lambda lease: (lease.seq,))"),
    ("M2 sleep floor ignored", A,
     "        if not sleeping:\n            return None\n        return LEVEL_RANK",
     "        if True:\n            return None\n        return LEVEL_RANK"),
    ("M3 bedroom rule ignored", A,
     "bedroom_min if self.bedroom else sleep_min", "sleep_min"),
    ("M4 snapshot retaken on every indicate", A,
     "if not self.leases and self.snapshot is None:", "if True:"),
    ("M5 one-shot hold removed", A,
     "                    return None\n                desired = restore_command(self.snapshot)",
     "                    pass\n                desired = restore_command(self.snapshot)"),
    ("M6 one-shot never consumed", A,
     "                top.consumed = True", "                pass"),
    ("M7 no restore at episode end", A,
     "        elif self.episode_sent:\n", "        elif False:\n"),
    ("M8 dedupe removed", A,
     "            not self.dirty\n            and self.last_sent is not None\n            and self.last_sent.output == desired.output",
     "            False"),
    ("M9 output guard removed", A,
     "        if not output_ok:\n            # Nothing", "        if False:\n            # Nothing"),
    ("M10 kill switch ignored", R,
     "return self.led_enabled and self._led_available(panel)", "return self._led_available(panel)"),
    ("M11 TTL timer never armed", R,
     "                self._arm_timer(panel, lease)\n", "                pass\n"),
    ("M12 release leaves TTL timer armed", R,
     "            for key in gone:\n                unsub = panel.timers.pop(key, None)\n                if unsub is not None:\n                    unsub()\n            if gone:",
     "            for key in gone:\n                pass\n            if gone:"),
    ("M13 sleep listener not subscribed", R,
     "        if self.sleep_entity:\n            self._unsubs.append(", "        if False:\n            self._unsubs.append("),
    ("M14 LED return not marked dirty", R,
     "                    panel.arbiter.mark_dirty()\n                await self._evaluate(affected)",
     "                    pass\n                await self._evaluate(affected)"),
    ("M15 unload does not restore", R,
     "                if cmd is not None:\n                    await self._send(panel, cmd)\n            await self._persist()",
     "                if False:\n                    await self._send(panel, cmd)\n            await self._persist()"),
    ("M16 stale snapshot not loaded", R,
     "                panel.arbiter.load_stale_snapshot(snap)", "                pass"),
    ("M17 snapshot never persisted", R,
     "            await self._store.async_save(current)", "            pass"),
    ("M18 boot debounce ignored", R,
     "        if dt_util.utcnow().timestamp() < self._started_at + self.boot_debounce_s:\n            return True",
     "        if False:\n            return True"),
    ("M19 unknown sleep treated as awake", R,
     "return state is None or state.state != STATE_OFF", "return state is not None and state.state == 'on'"),
    ("M20 4-bit scale 16", C, "FOUR_BIT_SCALE: Final = 17", "FOUR_BIT_SCALE: Final = 16"),
    ("M21 amber changed", C, "LEVEL_ALERT: (\"pulse\", (15, 8, 0))", "LEVEL_ALERT: (\"pulse\", (15, 9, 0))"),
    ("M22 renewal takes a new seq", A,
     "            lease.seq = existing.seq\n", "            pass\n"),
    ("M23 kill switch restore-state ignored", S,
     "            await self._runtime.async_set_led_enabled(False)", "            pass"),
    ("M24 failed send not retried", R,
     "                panel.arbiter.mark_dirty()  # retried on the next step", "                pass"),
    ("M25 suppressed_sleep never reported", R,
     "if floor is not None and LEVEL_RANK[level] < floor", "if False"),
    ("M26 unload restores even when LED unavailable", A,
     "        if not self.episode_sent or not output_ok:", "        if not self.episode_sent:"),
    ("M27 kill-switch-on does not mark dirty", R,
     "                for panel in self.by_area.values():\n                    panel.arbiter.mark_dirty()\n            await self._evaluate",
     "                for panel in self.by_area.values():\n                    pass\n            await self._evaluate"),
    ("M28 debounce end not scheduled", R,
     "        self._debounce_unsub = async_track_point_in_utc_time(\n            self.hass, _debounce_over",
     "        return\n        self._debounce_unsub = async_track_point_in_utc_time(\n            self.hass, _debounce_over"),
    ("M29 sleep off ignored (floor while awake)", R,
     "        state = self.hass.states.get(self.sleep_entity)\n        return state is None",
     "        return True\n        return state is None"),
]


def clear_pyc() -> None:
    for p in ROOT.rglob("__pycache__"):
        if ".venv" not in p.parts:
            shutil.rmtree(p, ignore_errors=True)


def main() -> None:
    only = sys.argv[1:]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for name, rel, old, new in MUTANTS:
        if only and name.split()[0] not in only:
            continue
        path = ROOT / rel
        orig = path.read_text()
        assert orig.count(old) == 1, f"{name}: anchor count {orig.count(old)}"
        try:
            path.write_text(orig.replace(old, new))
            clear_pyc()
            out = subprocess.run(
                [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "-x" if False else "-q", "tests/", "--hypothesis-seed=0"],
                cwd=ROOT, env=env, capture_output=True, text=True,
            ).stdout
        finally:
            path.write_text(orig)
            clear_pyc()
        failed = sorted({line.split("::")[1].split(" ")[0].split("[")[0]
                         for line in out.splitlines() if line.startswith("FAILED")})
        verdict = "RED" if failed else "GREEN(!!)"
        print(f"{name}: {verdict} {failed[:6]}{' +' + str(len(failed) - 6) if len(failed) > 6 else ''}",
              flush=True)


if __name__ == "__main__":
    main()
