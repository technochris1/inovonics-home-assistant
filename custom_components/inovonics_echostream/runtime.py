"""Run the coordinator in one transport thread and update HA from its event loop."""

from asyncio import Lock, sleep, timeout
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from pathlib import Path
from threading import Event, Thread
from time import monotonic

from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from inovonics_echostream_processor import (
    EchoStreamProcessorCore, EchoStreamProtocol, SecurityMessageEvent, security_event_to_bit_state_updates,
)

from .const import DEFAULT_ALERT_SECONDS, DEFAULT_WATCHDOG_SECONDS, DOMAIN
from .control import NID_READ_TIMEOUT, Coordinator, Source, read_coordinator_serial
from .debug_relay import DebugRelay

LOG = logging.getLogger(__name__)
CLASSES = {"case_wall_tamper": "tamper", "eol_tamper": "tamper", "low_battery": "battery", "clean_me": "problem"}


class Link:
    def __init__(self, control, on_security, relay, watchdog_seconds):
        self.control = control
        self.on_security = on_security
        self.relay = relay
        self.watchdog_seconds = watchdog_seconds
        self.poll_interval = min(30, watchdog_seconds / 3) if watchdog_seconds else 30
        self.read_in_session = False
        self.stop_event = Event()
        self.thread = Thread(target=self.receive, name="inovonics-link", daemon=True)

    def start(self):
        if self.relay is not None:
            self.relay.start()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.relay is not None:
            self.relay.stop()
        if self.thread.ident is not None:
            self.thread.join(6)
        self.control.checkpoint(force=True)

    def receive(self):
        source = self.control.source
        delay = 1
        while not self.stop_event.is_set():
            self.read_in_session = False
            try:
                with source.open() as connection:
                    if self.control.serial is None:
                        source.send(connection, EchoStreamProtocol.REQUEST_COORDINATOR_SERIAL_NUMBER)
                    elif read_coordinator_serial(source, connection) != self.control.serial:
                        raise ValueError("The connection belongs to a different physical coordinator.")
                    self.control.open_session()
                    self.receive_session(source, connection)
            except (OSError, ValueError) as error:
                if not self.stop_event.is_set():
                    LOG.warning("EchoStream connection unavailable: %s", error)
            finally:
                self.control.close_session()
                if self.relay is not None:
                    self.relay.disconnect()
            if self.read_in_session:
                delay = 1
            if self.stop_event.wait(delay):
                return
            delay = min(delay * 2, 30)

    def receive_session(self, source, connection):
        core = EchoStreamProcessorCore(auto_request_coordinator_metadata=False)

        def send(packet):
            source.send(connection, packet)
            if self.relay is not None:
                self.relay.observe("tx", packet)

        last_rx = monotonic()
        while not self.stop_event.is_set():
            data = source.read(connection)
            now = monotonic()
            if data:
                last_rx = now
                self.read_in_session = True
                self.control.note_receive()
                if self.relay is not None:
                    self.relay.observe("rx", data)
            elif self.watchdog_seconds and now - last_rx >= self.watchdog_seconds:
                raise TimeoutError(f"No coordinator data for {self.watchdog_seconds} seconds")
            for event in core.feed(data).events:
                generation = self.control.observe(event)
                if isinstance(event, SecurityMessageEvent):
                    self.on_security(event, generation)
            passthrough_idle = self.relay is not None and self.relay.forward(send)
            self.control.tick(send, poll_interval=self.poll_interval, passthrough_idle=passthrough_idle)
            self.control.checkpoint()


@dataclass
class Runtime:
    hass: object
    entry: object
    control: Coordinator
    store: Store
    view: dict
    points: dict = field(default_factory=dict)
    pinned_points: set = field(default_factory=set)
    removal_lock: Lock = field(default_factory=Lock)
    listeners: list = field(default_factory=list)
    dirty: bool = False
    active: bool = True
    next_save: float = 0
    link: Link | None = None
    cancel_refresh: object = None
    alert_active: bool | None = None

    @classmethod
    async def create(cls, hass, entry):
        source = Source.from_options(entry.data)
        folder = Path(hass.config.path(".storage"))
        # Store device supervision data separately for each config entry.
        folder = folder / f"{DOMAIN}.{entry.entry_id}"
        await hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))
        control = await hass.async_add_executor_job(Coordinator, source, folder, entry.data.get("coordinator_serial"))
        store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.points")
        saved = await store.async_load() or {}
        runtime = cls(hass, entry, control, store, await hass.async_add_executor_job(control.snapshot))
        if saved.get("source") == source.label:
            generations = runtime.view["device_generations"]
            saved_generations = saved.get("device_generations", {})
            runtime.pinned_points = {
                point for point in saved.get("pinned_points", ())
                if saved_generations.get(uid := point.partition(".")[0], 0) == generations.get(uid, 0)
            }
            runtime.points = {
                uid: {suffix: value for suffix, value in points.items() if suffix.startswith("STAT")}
                for uid, points in saved.get("points", {}).items()
                if saved_generations.get(uid, 0) == generations.get(uid, 0)
            }
        relay = None
        mode = entry.options.get("debug_mode", "off")
        if mode != "off":
            relay = DebugRelay(control, mode, entry.options.get("debug_client"),
                               ("0.0.0.0", entry.options.get("debug_port", 19001)))
        runtime.link = Link(
            control, runtime.queue_security, relay,
            entry.options.get("watchdog_seconds", DEFAULT_WATCHDOG_SECONDS),
        )
        return runtime

    @property
    def devices(self):
        return {device["uid"]: device for device in self.view["devices"]}

    @property
    def identity_prefix(self):
        # The original single-coordinator entry keeps its registry IDs.
        return "" if self.entry.unique_id in (None, DOMAIN) else f"{self.entry.entry_id}:"

    async def start(self):
        await self.hass.async_add_executor_job(self.configure_and_start)
        self.cancel_refresh = async_track_time_interval(self.hass, self.refresh, timedelta(seconds=1))

    def configure_and_start(self):
        self.control.set_windows({
            "device": self.entry.options.get("device_minutes", 120),
            "repeater": self.entry.options.get("repeater_minutes", 120),
        })
        self.link.start()

    async def stop(self):
        if not self.active:
            return
        self.active = False
        if self.cancel_refresh is not None:
            self.cancel_refresh()
        await self.hass.async_add_executor_job(self.link.stop)
        if self.dirty:
            await self.save()

    async def forget_device(self, uid):
        async with self.removal_lock:
            if not self.active:
                return False
            try:
                generation = await self.hass.async_add_executor_job(self.control.forget_device, uid)
            except OSError:
                LOG.exception("Unable to forget Inovonics device %s", uid)
                return False
            if generation is None:
                return False
            self.view["device_generations"][uid] = generation
            self.view["devices"] = [device for device in self.view["devices"] if device["uid"] != uid]
            self.points.pop(uid, None)
            self.pinned_points = {point for point in self.pinned_points if not point.startswith(f"{uid}.")}
            self.dirty = True
            self.notify()
            return True

    async def read_coordinator_nid(self, *, for_change=False):
        after = await self.hass.async_add_executor_job(self.control.request_nid_read, for_change)
        try:
            async with timeout(NID_READ_TIMEOUT):
                while self.active:
                    nid = await self.hass.async_add_executor_job(self.control.nid_read_result, after)
                    if nid is not None:
                        return nid
                    await sleep(0.2)
        except TimeoutError:
            pass
        raise ValueError("The coordinator did not report its NID.")

    def queue_security(self, event, generation):
        if self.active:
            self.hass.loop.call_soon_threadsafe(self.accept_security, event, generation)

    @callback
    def accept_security(self, event, generation):
        uid = event.device_uid_hex
        if not self.active or generation != self.view["device_generations"].get(uid, 0):
            return
        hide = self.entry.options.get("hide_unsupported", True)
        previous = self.points.get(uid, {})
        current = {}
        for bit in security_event_to_bit_state_updates(event):
            suffix = f"STAT{bit.stat_group}.{bit.bit}"
            filtered = (
                bit.reserved or bit.supported is False
                or (bit.supported is None and bit.stat_group == 1 and bit.bit > 4)
            ) and f"{uid}.{suffix}" not in self.pinned_points
            if hide and filtered:
                continue
            current[suffix] = {
                "value": bit.value, "name": bit.status_label or suffix,
                "class": CLASSES.get(bit.status_name), "model": event.device_model,
                "filtered": filtered,
            }
        if previous != current:
            self.points[uid] = current
            self.dirty = True
            self.notify()

    async def refresh(self, _now):
        view = await self.hass.async_add_executor_job(self.control.snapshot)
        if not self.active or view["device_generations"] != self.view["device_generations"]:
            return
        if view != self.view:
            self.view = view
            self.notify()
        if view["serial"] is not None and self.entry.data.get("coordinator_serial") is None:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, "coordinator_serial": view["serial"]},
            )
        self.update_outage_alert(view["outage_since"])
        if self.dirty and monotonic() >= self.next_save:
            self.next_save = monotonic() + 15
            await self.save()

    @callback
    def update_outage_alert(self, outage_since):
        delay = self.entry.options.get("alert_seconds", DEFAULT_ALERT_SECONDS)
        notification_id = f"{DOMAIN}.{self.entry.entry_id}.coordinator_offline"
        if not delay or outage_since is None:
            if self.alert_active is not False:
                persistent_notification.async_dismiss(self.hass, notification_id)
                self.alert_active = False
        elif not self.alert_active and monotonic() - outage_since >= delay:
            persistent_notification.async_create(
                self.hass,
                f"No data has been received from the Inovonics coordinator at "
                f"{self.control.source.label} since the connection failed. "
                "Home Assistant is still trying to reconnect.",
                title="Inovonics coordinator offline",
                notification_id=notification_id,
            )
            self.alert_active = True

    async def save(self, *, strict=False):
        data = {"source": self.control.source.label,
                "points": {uid: points.copy() for uid, points in self.points.items()},
                "device_generations": self.view["device_generations"].copy(),
                "pinned_points": sorted(self.pinned_points)}
        self.dirty = False
        try:
            await self.store.async_save(data)
        except OSError:
            self.dirty = True
            if strict:
                raise
            LOG.exception("Unable to save Inovonics alarm values")

    @callback
    def subscribe(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    @callback
    def notify(self):
        for listener in tuple(self.listeners):
            listener()
