"""EchoStream transport, serialized NID transactions and device supervision."""

from dataclasses import asdict, dataclass
import json
import logging
import re
import socket
from threading import Lock
from time import monotonic, time

from inovonics_echostream_processor import (
    AckEvent, AggregateMessageEvent, CoordinatorNetworkIdEvent,
    EchoStreamProtocol, NakEvent, NetworkStatusEvent, RepeaterResetEvent, SecurityMessageEvent,
)

LOG = logging.getLogger(__name__)
UID = re.compile(r"[0-9A-F]{8}")
MAX_OFFLINE_MINUTES = 10080
DEFAULT_OFFLINE_MINUTES = 120


@dataclass(frozen=True)
class Source:
    mode: str
    address: str
    port_or_baud: int

    @classmethod
    def from_options(cls, options):
        mode = options.get("connection", "tcp")
        if mode == "tcp":
            host = (options.get("processor_host") or "").strip()
            if not host:
                raise ValueError("Enter the coordinator IP address or hostname for TCP mode.")
            return cls(mode, host, options["processor_port"])
        if mode == "serial":
            port = (options.get("serial_port") or "").strip()
            if not port.startswith("/dev/"):
                raise ValueError("Select a serial device for Serial mode.")
            return cls(mode, port, options["serial_baud"])
        raise ValueError("Select TCP or Serial as the connection method.")

    @property
    def label(self):
        if self.mode == "serial":
            return f"{self.address} at {self.port_or_baud} baud"
        return f"{self.address}:{self.port_or_baud}"

    def open(self):
        if self.mode == "serial":
            from serial import Serial

            return Serial(self.address, baudrate=self.port_or_baud, timeout=0.25,
                          write_timeout=2, exclusive=True)
        connection = socket.create_connection((self.address, self.port_or_baud), timeout=3)
        connection.settimeout(0.25)
        return connection

    def read(self, connection):
        if self.mode == "serial":
            return connection.read(max(1, min(4096, connection.in_waiting)))
        try:
            chunk = connection.recv(4096)
        except socket.timeout:
            return b""
        if not chunk:
            raise ConnectionError("Coordinator closed the TCP connection")
        return chunk

    def send(self, connection, packet):
        if self.mode == "serial":
            if connection.write(packet) != len(packet):
                raise OSError("Incomplete serial command write")
        else:
            connection.sendall(packet)


@dataclass
class Change:
    kind: str
    target: int
    uid: str | None = None
    stage: str = "queued"
    sent_at: float = 0


@dataclass
class Device:
    kind: str
    model: str
    seen: float
    nid: int | None = None


class Coordinator:
    """Keep one NID operation active and require device-reported confirmation."""

    def __init__(self, source, data_dir):
        self.source = source
        self.path = data_dir / "supervision.json"
        self.lock = Lock()
        self.connected = False
        self.last_rx = None
        self.outage_since = None
        self.nid = None
        self.pending = None
        self.debug_writes = False
        self.notice = "No NID change requested."
        self.devices = {}
        self.seen_this_session = set()
        self.windows = {"device": DEFAULT_OFFLINE_MINUTES, "repeater": DEFAULT_OFFLINE_MINUTES}
        self.overrides = {}
        self.next_poll = 0
        self.last_checkpoint = monotonic()
        self.dirty = False
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            self.windows.update(stored["windows"])
            self.overrides = stored["overrides"]
            self.devices = {uid: Device(**values) for uid, values in stored["devices"].items()}
            if stored["source"] != asdict(source):
                for device in self.devices.values():
                    device.nid = None
                self.dirty = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            LOG.warning("Unable to read saved device supervision data.")

    def save_locked(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "source": asdict(self.source), "windows": self.windows, "overrides": self.overrides,
            "devices": {uid: asdict(device) for uid, device in self.devices.items()},
        }), encoding="utf-8")
        temporary.replace(self.path)
        self.last_checkpoint = monotonic()
        self.dirty = False

    def checkpoint(self, *, force=False):
        with self.lock:
            if self.dirty and (force or monotonic() - self.last_checkpoint >= 30):
                try:
                    self.save_locked()
                except OSError:
                    self.last_checkpoint = monotonic()
                    LOG.warning("Unable to save device supervision data.", exc_info=True)

    def online_locked(self, uid, device, now):
        minutes = self.overrides.get(uid, self.windows[device.kind])
        return None if not minutes else now - device.seen < minutes * 60

    def snapshot(self):
        with self.lock:
            now = time()
            return {
                "connected": self.connected and self.outage_since is None,
                "last_rx": self.last_rx, "outage_since": self.outage_since,
                "nid": self.nid, "notice": self.notice,
                "busy": self.pending is not None or self.debug_writes,
                "debug_writes": self.debug_writes, "windows": self.windows.copy(),
                "devices": [
                    {"uid": uid, **asdict(device), "online": self.online_locked(uid, device, now),
                     "override": self.overrides.get(uid), "heard": uid in self.seen_this_session}
                    for uid, device in sorted(self.devices.items())
                ],
            }

    def set_windows(self, windows=None, uid=None, override=None):
        values = list(windows.values()) if windows is not None else [override]
        if any(value is not None and not 0 <= value <= MAX_OFFLINE_MINUTES for value in values):
            raise ValueError(f"Enter a time from 0 through {MAX_OFFLINE_MINUTES} minutes.")
        with self.lock:
            before = self.windows.copy(), self.overrides.copy()
            if windows is not None:
                self.windows.update(windows)
            elif uid not in self.devices:
                raise ValueError("That device has not been heard.")
            elif override is None:
                self.overrides.pop(uid, None)
            else:
                self.overrides[uid] = override
            try:
                self.save_locked()
            except OSError:
                self.windows, self.overrides = before
                raise ValueError("Unable to save supervision settings.") from None

    def request(self, target, uid=None):
        if not 0 <= target <= 31:
            raise ValueError("The NID must be from 0 through 31.")
        with self.lock:
            if not self.connected or self.nid is None:
                raise ValueError("Wait for the coordinator to report its current NID.")
            if self.debug_writes:
                raise ValueError("Disconnect the passthrough client before changing a NID here.")
            if self.pending is not None:
                raise ValueError("An NID operation is already in progress.")
            if uid is None:
                if target == self.nid:
                    self.notice = "The coordinator already reports that NID."
                    return self.notice
            else:
                device = self.devices.get(uid)
                if (not UID.fullmatch(uid) or not device or device.kind != "repeater"
                        or uid not in self.seen_this_session or self.online_locked(uid, device, time()) is False):
                    raise ValueError("The targeted repeater must be heard on this connection.")
                if target != self.nid:
                    raise ValueError("A repeater can only adopt the coordinator's current NID.")
                if device.nid == target:
                    self.notice = "This repeater already reports the coordinator's NID."
                    return self.notice
            self.pending = Change("coordinator" if uid is None else "repeater", target, uid)
            self.notice = "NID change queued."
            return self.notice

    def begin_debug(self, writable):
        with self.lock:
            if not self.connected or (writable and self.pending is not None):
                return False
            if writable:
                self.debug_writes = True
            return True

    def end_debug(self, writable):
        if writable:
            with self.lock:
                self.debug_writes = False
                self.nid = None
                self.next_poll = 0

    def open_session(self):
        with self.lock:
            self.connected = True
            self.next_poll = 0

    def note_receive(self):
        with self.lock:
            self.last_rx = time()
            self.outage_since = None

    def close_session(self):
        with self.lock:
            self.connected = False
            if self.outage_since is None:
                self.outage_since = monotonic()
            self.nid = None
            self.seen_this_session.clear()
            if self.pending:
                self.finish_locked("Connection lost during the NID operation. Read the reported NID after reconnecting.")

    def finish_locked(self, message):
        self.notice = message
        self.pending = None
        self.next_poll = 0

    def tick(self, send, *, now=None, poll_interval=30):
        """Run in the link thread so commands and reads share one transport."""
        now = monotonic() if now is None else now
        with self.lock:
            if self.debug_writes:
                return
            change = self.pending
            if change and change.stage == "queued":
                if change.kind == "repeater":
                    if self.nid != change.target:
                        self.finish_locked("Coordinator NID changed before sending. Select the repeater again.")
                        return
                    body = bytes((0x20, 0x07, 0x00)) + bytes.fromhex(change.uid)
                else:
                    body = bytes((0x34, 0x04, 0x02, change.target))
                send(body + bytes((EchoStreamProtocol.checksum(body),)))
                change.stage = "sent"
                change.sent_at = now
                self.next_poll = now + 1
                self.notice = "Command sent. Waiting for the hardware to report the selected NID."
                return
            if change and now >= change.sent_at + (20 if change.kind == "coordinator" else 120):
                self.finish_locked("NID change was not verified before timeout. Check the reported NID before retrying.")
                return
            if now >= self.next_poll and (not change or change.kind == "coordinator"):
                send(EchoStreamProtocol.REQUEST_COORDINATOR_NETWORK_ID)
                self.next_poll = now + (poll_interval if not change and self.nid is not None else min(3, poll_interval))

    def observe(self, event):
        with self.lock:
            change = self.pending
            active = change and change.stage != "queued"
            if isinstance(event, CoordinatorNetworkIdEvent):
                self.nid = event.network_id
                if active and change.kind == "coordinator" and event.network_id == change.target:
                    self.finish_locked(f"Coordinator confirmed NID {change.target}.")
                elif active and change.kind == "repeater" and event.network_id != change.target:
                    self.finish_locked("The coordinator NID changed. The repeater update could not be verified.")
            elif active and isinstance(event, NakEvent) and change.stage == "sent":
                self.finish_locked(f"Coordinator returned NAK {event.error_code}. Check the reported NID before retrying.")
            elif active and isinstance(event, AckEvent) and change.stage == "sent":
                change.stage = "accepted"
                self.notice = (
                    "Coordinator accepted the request. Awaiting NID readback." if change.kind == "coordinator"
                    else "Coordinator accepted the request. Awaiting this repeater's reset report."
                )

            if isinstance(event, SecurityMessageEvent):
                self.heard_locked(event.device_uid_hex, "device", event.device_model)
            if isinstance(event, (AggregateMessageEvent, NetworkStatusEvent, RepeaterResetEvent)):
                uid = event.device_uid_hex
                if is_repeater(uid):
                    nid = event.network_id if isinstance(event, RepeaterResetEvent) else None
                    self.heard_locked(uid, "repeater", getattr(event, "device_model", None), nid)
                    if active and change.kind == "repeater" and change.uid == uid and change.target == nid:
                        self.finish_locked(f"Repeater {uid} confirmed NID {nid}.")
            hop = getattr(event, "first_hop_hex", None)
            if is_repeater(hop):
                self.heard_locked(hop, "repeater")

    def heard_locked(self, uid, kind, model=None, nid=None):
        existing = self.devices.get(uid)
        if existing is None:
            existing = self.devices[uid] = Device(kind, model or "Unknown", time())
        existing.seen = time()
        if model and model != "UNKNOWN":
            existing.model = model
        if nid is not None:
            existing.nid = nid
        self.seen_this_session.add(uid)
        self.dirty = True


def is_repeater(uid):
    return bool(uid and uid.startswith("01") and UID.fullmatch(uid))
