"""Halo runtime: the HA glue around the per-panel arbiters.

Every input (service call, lease timer, sleep flip, LED availability change,
kill-switch toggle, debounce end) is one *arbiter step*: it takes the runtime
lock, mutates arbiter inputs, then evaluates the affected panels and sends at
most one LED command per panel before releasing the lock (R2-13).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
import itertools
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .arbiter import Command, Lease, LeaseKey, PanelArbiter, normalise_snapshot
from .const import (
    BEDROOM_SLEEP_MIN_LEVEL,
    CONF_AREA_ID,
    CONF_BEDROOM,
    CONF_ENABLED,
    CONF_KS_ENTITY,
    CONF_LED_ENTITY,
    CONF_SLEEP_ENTITY,
    DEFAULT_BOOT_DEBOUNCE_S,
    INDICATION_IDLE,
    LED_CALL_TIMEOUT_S,
    LEVEL_EFFECT_MAP,
    LEVEL_RANK,
    ONE_SHOT_EFFECTS,
    RESULT_ACCEPTED,
    RESULT_NO_SURFACE,
    RESULT_SUPPRESSED_SLEEP,
    RESULT_UNAVAILABLE,
    SIGNAL_PANEL_UPDATED,
    SLEEP_MIN_LEVEL,
    STATUS_DISABLED,
    STATUS_KS_UNAVAILABLE,
    STATUS_LED_UNAVAILABLE,
    STATUS_OK,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUBENTRY_TYPE_PANEL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Panel:
    """One configured panel (a panel subentry)."""

    key: str  # subentry id
    area_id: str
    title: str
    led_entity: str
    ks_entity: str | None
    bedroom: bool
    enabled: bool
    arbiter: PanelArbiter
    last_cmd: Command | None = None
    last_cmd_at: datetime | None = None
    last_error: str | None = None
    timers: dict[LeaseKey, CALLBACK_TYPE] = field(default_factory=dict)


class HaloRuntime:
    """Owns panels, leases, timers, listeners and the snapshot store."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.sleep_entity: str | None = entry.data.get(CONF_SLEEP_ENTITY) or None
        self.led_enabled = True  # switch.halo_led (restored before start)
        self.boot_debounce_s: float = DEFAULT_BOOT_DEBOUNCE_S
        self.panels: dict[str, Panel] = {}
        self.by_area: dict[str, Panel] = {}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._persisted: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._seq = itertools.count(1)
        self._unsubs: list[CALLBACK_TYPE] = []
        self._debounce_unsub: CALLBACK_TYPE | None = None
        self._started_at: float = dt_util.utcnow().timestamp()
        self._started = False
        self._stopped = False

        for sub in entry.subentries.values():
            if sub.subentry_type != SUBENTRY_TYPE_PANEL:
                continue
            data = sub.data
            panel = Panel(
                key=sub.subentry_id,
                area_id=data[CONF_AREA_ID],
                title=sub.title,
                led_entity=data[CONF_LED_ENTITY],
                ks_entity=data.get(CONF_KS_ENTITY) or None,
                bedroom=bool(data.get(CONF_BEDROOM, False)),
                enabled=bool(data.get(CONF_ENABLED, True)),
                arbiter=PanelArbiter(sub.subentry_id, bool(data.get(CONF_BEDROOM, False))),
            )
            self.panels[panel.key] = panel
            if panel.enabled:
                self.by_area[panel.area_id] = panel

    # ------------------------------------------------------------ lifecycle
    async def async_load(self) -> None:
        """Load persisted snapshots (before platforms; no commands yet)."""
        stored = await self._store.async_load() or {}
        self._persisted = dict(stored)
        for key, snap in stored.items():
            panel = self.panels.get(key)
            if panel is not None and panel.enabled and isinstance(snap, dict):
                panel.arbiter.load_stale_snapshot(snap)

    async def async_start(self) -> None:
        """Subscribe and run the first step (after knob entities restored)."""
        self._started_at = dt_util.utcnow().timestamp()
        self._started = True
        if self.sleep_entity:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [self.sleep_entity], self._async_on_sleep_change
                )
            )
        watch = [p.led_entity for p in self.panels.values()]
        watch += [p.ks_entity for p in self.panels.values() if p.ks_entity]
        if watch:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, sorted(set(watch)), self._async_on_device_change
                )
            )
        self._schedule_debounce_end()
        async with self._lock:
            await self._evaluate(self.by_area.values())

    async def async_stop(self) -> None:
        """Restore every active panel, then cancel timers and listeners (R2-4)."""
        async with self._lock:
            self._stopped = True
            for panel in self.by_area.values():
                cmd = panel.arbiter.unload_command(self._output_ok(panel))
                if cmd is not None:
                    await self._send(panel, cmd)
            await self._persist()
            for panel in self.panels.values():
                for unsub in panel.timers.values():
                    unsub()
                panel.timers.clear()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None

    # ---------------------------------------------------------------- knobs
    async def async_set_led_enabled(self, enabled: bool) -> None:
        async with self._lock:
            was = self.led_enabled
            self.led_enabled = enabled
            if not self._started:
                return
            if enabled and not was:
                for panel in self.by_area.values():
                    panel.arbiter.mark_dirty()
            await self._evaluate(self.by_area.values())

    async def async_set_boot_debounce(self, seconds: float) -> None:
        async with self._lock:
            self.boot_debounce_s = max(0.0, float(seconds))
            if not self._started:
                return
            self._schedule_debounce_end()
            await self._evaluate(self.by_area.values())

    # ------------------------------------------------------------- services
    async def async_indicate(
        self,
        areas: Iterable[str],
        owner: str,
        lease_id: str,
        level: str,
        pattern: str | None,
        ttl_s: int,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        async with self._lock:
            now = dt_util.utcnow()
            expires = now.timestamp() + ttl_s
            effect, rgb4 = LEVEL_EFFECT_MAP[level]
            if pattern:
                effect = pattern
            touched: list[Panel] = []
            for area in dict.fromkeys(areas):
                panel = self.by_area.get(area)
                if panel is None:
                    result[area] = RESULT_NO_SURFACE
                    continue
                lease = Lease(
                    owner=owner,
                    id=lease_id,
                    level=level,
                    effect=effect,
                    rgb4=rgb4,
                    expires_at=expires,
                    seq=next(self._seq),
                    one_shot=effect in ONE_SHOT_EFFECTS,
                )
                panel.arbiter.indicate(lease, lambda p=panel: self._read_snapshot(p))
                self._arm_timer(panel, lease)
                touched.append(panel)
            await self._evaluate(touched)
            for panel in touched:
                if not self._led_available(panel):
                    result[panel.area_id] = RESULT_UNAVAILABLE
                else:
                    floor = self._floor(panel)
                    result[panel.area_id] = (
                        RESULT_SUPPRESSED_SLEEP
                        if floor is not None and LEVEL_RANK[level] < floor
                        else RESULT_ACCEPTED
                    )
        return result

    async def async_release(
        self, owner: str, lease_id: str | None, areas: Iterable[str] | None
    ) -> int:
        area_set = set(areas) if areas else None

        def pred(lease: Lease) -> bool:
            return lease.owner == owner and (lease_id is None or lease.id == lease_id)

        async with self._lock:
            return await self._release_where(pred, area_set)

    async def async_release_all(self) -> int:
        async with self._lock:
            return await self._release_where(lambda _lease: True, None)

    async def _release_where(
        self, pred: Callable[[Lease], bool], area_set: set[str] | None
    ) -> int:
        count = 0
        touched: list[Panel] = []
        for panel in self.by_area.values():
            if area_set is not None and panel.area_id not in area_set:
                continue
            gone = panel.arbiter.release(pred)
            for key in gone:
                unsub = panel.timers.pop(key, None)
                if unsub is not None:
                    unsub()
            if gone:
                count += len(gone)
                touched.append(panel)
        await self._evaluate(touched)
        return count

    # --------------------------------------------------------------- events
    def _arm_timer(self, panel: Panel, lease: Lease) -> None:
        old = panel.timers.pop(lease.key, None)
        if old is not None:
            old()

        async def _expired(_now: datetime, panel: Panel = panel) -> None:
            async with self._lock:
                if self._stopped:
                    return
                gone = panel.arbiter.expire(dt_util.utcnow().timestamp())
                for key in gone:
                    unsub = panel.timers.pop(key, None)
                    if unsub is not None and key != lease.key:
                        unsub()
                panel.timers.pop(lease.key, None)
                await self._evaluate([panel])

        panel.timers[lease.key] = async_track_point_in_utc_time(
            self.hass, _expired, dt_util.utc_from_timestamp(lease.expires_at)
        )

    async def _async_on_sleep_change(self, _event: Event[EventStateChangedData]) -> None:
        async with self._lock:
            if self._stopped:
                return
            await self._evaluate(self.by_area.values())

    async def _async_on_device_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        old = event.data["old_state"]
        new = event.data["new_state"]
        async with self._lock:
            if self._stopped:
                return
            affected = [p for p in self.by_area.values() if p.led_entity == entity_id]
            came_back = (old is None or old.state == STATE_UNAVAILABLE) and (
                new is not None and new.state != STATE_UNAVAILABLE
            )
            if came_back and affected:
                for panel in affected:
                    panel.arbiter.mark_dirty()
                await self._evaluate(affected)
        for panel in self.panels.values():
            if entity_id in (panel.led_entity, panel.ks_entity):
                async_dispatcher_send(self.hass, SIGNAL_PANEL_UPDATED.format(panel.key))

    def _schedule_debounce_end(self) -> None:
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None
        end = self._started_at + self.boot_debounce_s
        if end <= dt_util.utcnow().timestamp():
            return

        async def _debounce_over(_now: datetime) -> None:
            self._debounce_unsub = None
            async with self._lock:
                if self._stopped:
                    return
                await self._evaluate(self.by_area.values())

        self._debounce_unsub = async_track_point_in_utc_time(
            self.hass, _debounce_over, dt_util.utc_from_timestamp(end)
        )

    # ------------------------------------------------------------- queries
    def sleeping(self) -> bool:
        """Effective sleep: on, unknown, or inside the boot debounce (R2-5)."""
        if not self.sleep_entity:
            return False
        if dt_util.utcnow().timestamp() < self._started_at + self.boot_debounce_s:
            return True
        state = self.hass.states.get(self.sleep_entity)
        return state is None or state.state != STATE_OFF

    def _floor(self, panel: Panel) -> int | None:
        return panel.arbiter.floor_rank(
            self.sleeping(), SLEEP_MIN_LEVEL, BEDROOM_SLEEP_MIN_LEVEL
        )

    def _led_available(self, panel: Panel) -> bool:
        state = self.hass.states.get(panel.led_entity)
        return state is not None and state.state != STATE_UNAVAILABLE

    def _output_ok(self, panel: Panel) -> bool:
        return self.led_enabled and self._led_available(panel)

    def _read_snapshot(self, panel: Panel) -> dict[str, Any]:
        state = self.hass.states.get(panel.led_entity)
        if state is None:
            return normalise_snapshot(None, None)
        return normalise_snapshot(state.state, state.attributes.get("rgb_color"))

    def status(self, panel: Panel) -> str:
        if not panel.enabled:
            return STATUS_DISABLED
        if not self._led_available(panel):
            return STATUS_LED_UNAVAILABLE
        if panel.ks_entity:
            ks = self.hass.states.get(panel.ks_entity)
            if ks is None or ks.state == STATE_UNAVAILABLE:
                return STATUS_KS_UNAVAILABLE
        return STATUS_OK

    def indication(self, panel: Panel) -> str:
        top = panel.arbiter.winner(self._floor(panel))
        return top.level if top is not None else INDICATION_IDLE

    # ----------------------------------------------------------------- core
    async def _evaluate(self, panels: Iterable[Panel]) -> None:
        """One arbiter step for the given panels. Caller holds the lock."""
        sends: list[tuple[Panel, Command]] = []
        for panel in list(panels):
            cmd = panel.arbiter.evaluate(self._floor(panel), self._output_ok(panel))
            if cmd is not None:
                sends.append((panel, cmd))
        await self._persist()  # snapshot hits disk before the first show
        for panel, cmd in sends:
            await self._send(panel, cmd)
        for panel in self.panels.values():
            async_dispatcher_send(self.hass, SIGNAL_PANEL_UPDATED.format(panel.key))

    async def _persist(self) -> None:
        current = {
            key: p.arbiter.snapshot
            for key, p in self.panels.items()
            if p.arbiter.snapshot is not None
        }
        # A panel whose snapshot could not be restored at unload keeps it.
        if current != self._persisted:
            self._persisted = current
            await self._store.async_save(current)

    async def _send(self, panel: Panel, cmd: Command) -> None:
        if cmd.kind == "restore" and cmd.snapshot_state == "off":
            service, data = "turn_off", {"entity_id": panel.led_entity}
        else:
            service = "turn_on"
            data = {"entity_id": panel.led_entity, "effect": cmd.effect}
            if cmd.rgb is not None:
                data["rgb_color"] = list(cmd.rgb)
        panel.last_cmd = cmd
        panel.last_cmd_at = dt_util.utcnow()
        try:
            async with asyncio.timeout(LED_CALL_TIMEOUT_S):
                await self.hass.services.async_call(
                    "light", service, data, blocking=True
                )
            panel.last_error = None
        except (HomeAssistantError, TimeoutError) as err:
            panel.last_error = f"{type(err).__name__}: {err}"
            if cmd.kind == "restore" and not panel.arbiter.leases:
                # The episode already closed; reopen it so the restore is retried
                # (next step, LED return, or next setup via the persisted snapshot).
                panel.arbiter.load_stale_snapshot(
                    {
                        "state": cmd.snapshot_state,
                        "rgb_color": list(cmd.rgb) if cmd.rgb else None,
                    }
                )
                await self._persist()
            else:
                panel.arbiter.mark_dirty()  # retried on the next step
            _LOGGER.warning("Halo: LED command to %s failed: %s", panel.led_entity, err)
