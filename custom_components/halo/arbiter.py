"""Per-panel lease arbiter. Pure logic, no Home Assistant imports.

One `PanelArbiter` per panel. The HA glue (`runtime.py`) feeds it inputs and
sends whatever single command `evaluate()` returns. An *arbiter step* is one
input event followed by one `evaluate()` per affected panel (R2-13).

Invariants (docs/PLAN.md S1 invariant 2, rewritten by R2-5/R2-13):
 (a) while a higher-level eligible lease is active, no lower-level or restore
     command is emitted;
 (b) while sleeping, every command emitted is either the effect of the highest
     eligible (>= floor) lease or the restore;
 (c) in the step that removes the last lease, the restore is emitted (once)
     unless it is already the last command, provided anything was shown in the
     episode and output is possible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .const import FOUR_BIT_SCALE, LEVEL_RANK, RESTORE_ECHO_WINDOW_S

LeaseKey = tuple[str, str]  # (owner, id)
Snapshot = dict[str, Any]  # {"state": "on"|"off"|"unknown", "rgb_color": [r,g,b]|None}


@dataclass(frozen=True)
class Command:
    """One LED command."""

    kind: str  # "show" | "restore"
    effect: str | None = None
    rgb: tuple[int, int, int] | None = None  # 0-255, already x17
    level: str | None = None
    lease: LeaseKey | None = field(default=None, compare=False)
    snapshot_state: str | None = None  # restore only: "on" | "off"

    @property
    def output(self) -> tuple[Any, ...]:
        """What the hardware is asked to do (lease identity ignored)."""
        return (self.kind, self.effect, self.rgb, self.snapshot_state)


@dataclass
class Lease:
    """An owner's request that a panel show a level until released or expired."""

    owner: str
    id: str
    level: str
    effect: str
    rgb4: tuple[int, int, int]
    expires_at: float
    seq: int
    one_shot: bool
    consumed: bool = False

    @property
    def key(self) -> LeaseKey:
        return (self.owner, self.id)

    @property
    def rank(self) -> int:
        return LEVEL_RANK[self.level]


def rgb4_to_8(rgb4: tuple[int, int, int]) -> tuple[int, int, int]:
    """4-bit (0-15) channel triple to the 0-255 triple the bridge expects."""
    return tuple(max(0, min(15, int(c))) * FOUR_BIT_SCALE for c in rgb4)  # type: ignore[return-value]


def normalise_snapshot(state: str | None, rgb: Any) -> Snapshot:
    """Snapshot from an HA light state. unknown/unavailable/missing -> unknown."""
    if state == "on":
        color = list(rgb)[:3] if rgb else None
        return {"state": "on", "rgb_color": color}
    if state == "off":
        return {"state": "off", "rgb_color": None}
    return {"state": "unknown", "rgb_color": None}


def restore_command(snapshot: Snapshot | None) -> Command:
    """Explicit restore: solid in the snapshot colour, or off (R2-3, R2-7)."""
    if snapshot and snapshot.get("state") == "on":
        rgb = snapshot.get("rgb_color")
        return Command(
            kind="restore",
            effect="solid",
            rgb=tuple(rgb) if rgb else None,  # type: ignore[arg-type]
            snapshot_state="on",
        )
    return Command(kind="restore", snapshot_state="off")


def snapshot_of_restore(cmd: Command) -> Snapshot:
    if cmd.snapshot_state == "on":
        return {"state": "on", "rgb_color": list(cmd.rgb) if cmd.rgb else None}
    return {"state": "off", "rgb_color": None}


class PanelArbiter:
    """Lease set + display bookkeeping for one panel."""

    def __init__(self, key: str, bedroom: bool) -> None:
        self.key = key
        self.bedroom = bedroom
        self.leases: dict[LeaseKey, Lease] = {}
        self.snapshot: Snapshot | None = None
        self.last_sent: Command | None = None
        self.episode_sent = False
        self.dirty = False
        # Last show ever sent (kept across episodes) -- see _take_snapshot.
        self.prev_show: Command | None = None
        # When the last restore reached the LED (set by the glue on success).
        self.restore_sent_at: float | None = None

    # ---------------------------------------------------------------- inputs
    def indicate(
        self,
        lease: Lease,
        snapshot_fn: Callable[[], Snapshot],
        now: float,
    ) -> None:
        """Add or renew a lease. Snapshot only on idle -> active (R2-3)."""
        if not self.leases and self.snapshot is None:
            self.snapshot = self._take_snapshot(snapshot_fn, now)
        existing = self.leases.get(lease.key)
        if existing is not None:
            # Renewal keeps the original sequence (a renewal is not a newer lease)
            # and the consumed flag if the output did not change.
            lease.seq = existing.seq
            if existing.one_shot and lease.one_shot and existing.level == lease.level:
                lease.consumed = existing.consumed
        self.leases[lease.key] = lease

    def release(self, pred: Callable[[Lease], bool]) -> list[LeaseKey]:
        gone = [k for k, lease in self.leases.items() if pred(lease)]
        for k in gone:
            del self.leases[k]
        return gone

    def expire(self, now: float) -> list[LeaseKey]:
        return self.release(lambda lease: lease.expires_at <= now)

    def _take_snapshot(self, snapshot_fn: Callable[[], Snapshot], now: float) -> Snapshot:
        """Read the LED, but never adopt our own colour as the snapshot (R2-3 / H2).

        HA state lags the bridge's MQTT echo: right after our restore the entity
        can still show the last show's colour. If the last command we sent was a
        restore and the live state still looks like the show before it, the
        restore's target is the truth.
        """
        live = snapshot_fn()
        if (
            self.restore_sent_at is None
            or now - self.restore_sent_at > RESTORE_ECHO_WINDOW_S
        ):
            return live
        last = self.last_sent
        prev = self.prev_show
        if (
            last is not None
            and last.kind == "restore"
            and prev is not None
            and live.get("state") == "on"
            and live.get("rgb_color") is not None
            and tuple(live["rgb_color"]) == prev.rgb
        ):
            return snapshot_of_restore(last)
        return live

    def led_returned(self, snapshot_fn: Callable[[], Snapshot], now: float) -> None:
        """LED back from unavailable (M3): re-read the snapshot if nothing was shown."""
        if self.leases and not self.episode_sent:
            self.snapshot = self._take_snapshot(snapshot_fn, now)
        self.dirty = True

    def one_shot_elapsed(self, key: LeaseKey) -> bool:
        """The one-shot's play time is over (M1): put the snapshot back explicitly."""
        last = self.last_sent
        if last is None or last.kind != "show" or last.lease != key:
            return False
        lease = self.leases.get(key)
        if lease is None or not lease.consumed:
            return False
        self.dirty = True
        return True

    def send_failed(self, cmd: Command) -> None:
        """The command did not reach the LED: undo what evaluate() assumed (H1)."""
        self.dirty = True
        if cmd.kind == "show":
            lease = self.leases.get(cmd.lease) if cmd.lease else None
            if lease is not None and lease.one_shot:
                lease.consumed = False
        elif not self.leases and self.snapshot is None:
            # The episode closed on this restore; reopen it so it is retried.
            self.snapshot = snapshot_of_restore(cmd)
            self.episode_sent = True
        if cmd.kind == "restore":
            self.last_sent = None

    def silence_command(self) -> Command | None:
        """Kill switch going off (M2): one restore if an effect may be running."""
        if not self.episode_sent:
            return None
        if not self.dirty and self.last_sent is not None and self.last_sent.kind == "restore":
            return None
        cmd = restore_command(self.snapshot)
        self.last_sent = cmd
        self.dirty = False
        if not self.leases:
            self._end_episode()
        return cmd

    def mark_dirty(self) -> None:
        """Hardware may have lost what we showed (LED came back / kill switch on)."""
        self.dirty = True

    def load_stale_snapshot(self, snapshot: Snapshot) -> None:
        """Persisted snapshot with no live leases: the ring may be stuck (R2-3)."""
        self.snapshot = snapshot
        self.episode_sent = True
        self.dirty = True

    # --------------------------------------------------------------- queries
    def floor_rank(self, sleeping: bool, sleep_min: str, bedroom_min: str) -> int | None:
        if not sleeping:
            return None
        return LEVEL_RANK[bedroom_min if self.bedroom else sleep_min]

    def winner(self, floor: int | None) -> Lease | None:
        eligible = [
            lease for lease in self.leases.values() if floor is None or lease.rank >= floor
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda lease: (lease.rank, lease.seq))

    # ------------------------------------------------------------- the step
    def evaluate(self, floor: int | None, output_ok: bool) -> Command | None:
        """Return the single command to send for this step, or None."""
        top = self.winner(floor)
        desired: Command | None
        if top is not None:
            show = Command(
                kind="show",
                effect=top.effect,
                rgb=rgb4_to_8(top.rgb4),
                level=top.level,
                lease=top.key,
            )
            if top.one_shot and top.consumed:
                # A one-shot already played; the bridge reverted on its own.
                # Hold if that is the last thing we sent and nothing was lost;
                # otherwise put the snapshot back explicitly (R2-7).
                if (
                    not self.dirty
                    and self.last_sent is not None
                    and self.last_sent.kind == "show"
                    and self.last_sent.lease == top.key
                ):
                    return None
                desired = restore_command(self.snapshot)
            else:
                desired = show
        elif self.episode_sent:
            if self.last_sent is not None and self.last_sent.kind == "restore":
                # The restore already reached the LED (send_failed clears
                # last_sent when it did not); nothing left to put back.
                desired = None
            else:
                desired = restore_command(self.snapshot)
        else:
            desired = None

        if desired is None:
            if not self.leases:
                self._end_episode()
            return None

        if (
            not self.dirty
            and self.last_sent is not None
            and self.last_sent.output == desired.output
        ):
            if desired.kind == "restore" and not self.leases:
                self._end_episode()
            return None

        if not output_ok:
            # Nothing reached the hardware; when output returns the glue marks
            # the panel dirty (LED back / kill switch on) and this re-evaluates.
            return None

        self.last_sent = desired
        self.dirty = False
        if desired.kind == "show":
            self.episode_sent = True
            self.prev_show = desired
            if top is not None and top.one_shot:
                top.consumed = True  # undone by send_failed() if it never arrived
        elif not self.leases:
            self._end_episode()
        return desired

    def unload_command(self, output_ok: bool) -> Command | None:
        """On unload: put the LED back if we changed it (R2-4)."""
        self.leases.clear()
        if not self.episode_sent or not output_ok:
            return None
        if not self.dirty and self.last_sent is not None and self.last_sent.kind == "restore":
            self._end_episode()
            return None
        cmd = restore_command(self.snapshot)
        self._end_episode()
        return cmd

    def _end_episode(self) -> None:
        # last_sent / prev_show are kept: dedupe and _take_snapshot need them.
        self.snapshot = None
        self.episode_sent = False
        self.dirty = False
