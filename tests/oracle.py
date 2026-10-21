"""Independent oracle for invariant 2 (a)/(b)/(c), written from the plan, not the code.

The oracle keeps its own lease table and its own notion of "what the LED should
show". It never calls into custom_components.halo. Level effects and colours are
hard-coded literals here (the plan's LEVEL_EFFECT_MAP x17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RANK = {"info": 1, "warn": 2, "alert": 3, "critical": 4}
EFFECT = {
    "critical": ("alert", (255, 0, 0)),
    "alert": ("pulse", (255, 136, 0)),
    "warn": ("blink", (0, 0, 255)),
    "info": ("notify", (255, 255, 255)),
}


@dataclass
class OLease:
    level: str
    seq: int
    expires: float
    consumed: bool = False


@dataclass
class Oracle:
    bedroom: bool
    leases: dict[str, OLease] = field(default_factory=dict)
    seq: int = 0
    # LED as the hardware would look (state, rgb); None rgb when off/unknown
    pre_state: tuple[str, Any] | None = None
    shown_since_restore: bool = False
    last_cmd: tuple[str, Any] | None = None  # ("show", (effect, rgb)) | ("restore", payload)
    violations: list[str] = field(default_factory=list)
    resend_ok: bool = False  # LED came back / kill switch on: one re-send is legal

    def output_returned(self) -> None:
        self.resend_ok = True

    # -- inputs -------------------------------------------------------------
    def indicate(self, lid: str, level: str, expires: float, led_visible: tuple[str, Any]) -> None:
        if not self.leases and not self.shown_since_restore:
            self.pre_state = led_visible
        self.seq += 1
        old = self.leases.get(lid)
        if old is not None:
            consumed = old.consumed and old.level == level
            self.leases[lid] = OLease(level, old.seq, expires, consumed)
        else:
            self.leases[lid] = OLease(level, self.seq, expires)

    def release(self, lid: str) -> None:
        self.leases.pop(lid, None)

    def expire(self, now: float) -> None:
        for lid in [k for k, v in self.leases.items() if v.expires <= now]:
            del self.leases[lid]

    # -- queries ------------------------------------------------------------
    def floor(self, sleeping: bool) -> int:
        if not sleeping:
            return 0
        return RANK["critical"] if self.bedroom else RANK["alert"]

    def top(self, sleeping: bool) -> tuple[str, OLease] | None:
        fl = self.floor(sleeping)
        best = None
        for lid, lease in self.leases.items():
            if RANK[lease.level] < fl:
                continue
            if best is None or (RANK[lease.level], lease.seq) > (
                RANK[best[1].level],
                best[1].seq,
            ):
                best = (lid, lease)
        return best

    def restore_payload(self) -> tuple[str, Any]:
        state = self.pre_state
        if state is not None and state[0] == "on":
            return ("solid", tuple(state[1]) if state[1] else None)
        return ("off", None)

    # -- checks after one step ---------------------------------------------
    def check_step(
        self,
        emitted: list[tuple[str, Any]],
        sleeping: bool,
        output_ok: bool,
        where: str,
    ) -> None:
        """`emitted` = normalised commands this step: ("show",(effect,rgb)) | ("restore",(kind,rgb))."""
        if len(emitted) > 1:
            self.violations.append(f"{where}: {len(emitted)} commands in one step")
        top = self.top(sleeping)
        for cmd in emitted:
            if not output_ok:
                self.violations.append(f"{where}: command while output not possible")
            if cmd[0] == "show":
                if top is None:
                    self.violations.append(f"{where}: show {cmd} with no eligible lease (a/b)")
                    continue
                lid, lease = top
                if cmd[1] != EFFECT[lease.level]:
                    self.violations.append(
                        f"{where}: show {cmd[1]} but top eligible is {lease.level} (a/b)"
                    )
                if lease.consumed:
                    self.violations.append(f"{where}: replayed consumed one-shot {lid}")
                if lease.level == "info":
                    lease.consumed = True
                self.shown_since_restore = True
            else:
                if top is not None and not top[1].consumed:
                    self.violations.append(
                        f"{where}: restore while {top[1].level} eligible lease active (a)"
                    )
                if cmd[1] != self.restore_payload():
                    self.violations.append(
                        f"{where}: restore {cmd[1]} != snapshot {self.restore_payload()}"
                    )
                if (
                    not self.resend_ok
                    and self.last_cmd is not None
                    and self.last_cmd[0] == "restore"
                ):
                    self.violations.append(f"{where}: duplicate consecutive restore (c)")
                self.shown_since_restore = False
            self.last_cmd = cmd
            self.resend_ok = False
        if not output_ok:
            return
        # liveness: after the step the last command reflects the arbitration
        if top is not None and not top[1].consumed:
            want = ("show", EFFECT[top[1].level])
            if self.last_cmd is None or self.last_cmd[0] != "show" or self.last_cmd[1] != want[1]:
                self.violations.append(
                    f"{where}: top eligible {top[1].level} but last command {self.last_cmd}"
                )
        elif (
            top is not None
            and self.last_cmd is not None
            and self.last_cmd[0] == "show"
            and self.last_cmd[1] != EFFECT["info"]
        ):
            self.violations.append(
                f"{where}: only a consumed one-shot is eligible but {self.last_cmd} still shown (a)"
            )
        elif top is None and self.shown_since_restore:
            self.violations.append(
                f"{where}: nothing eligible but no restore after last show {self.last_cmd} (b/c)"
            )
        if not self.leases and not self.shown_since_restore:
            self.pre_state = None
