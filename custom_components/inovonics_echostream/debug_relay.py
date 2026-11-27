"""Optional raw EchoStream tap and single-client diagnostic passthrough."""

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
import json
import logging
from queue import Empty, Full, Queue
import select
import socket
from threading import Event, Lock, Thread

from inovonics_echostream_processor import EchoStreamProtocol

LOG = logging.getLogger(__name__)
DEBUG_PORT = 19001


@dataclass
class Peer:
    socket: socket.socket
    rx: Queue = field(default_factory=lambda: Queue(maxsize=128))
    tx: Queue = field(default_factory=lambda: Queue(maxsize=32))
    closed: Event = field(default_factory=Event)
    tx_frame: bytearray | None = field(default_factory=bytearray)

    def close(self):
        self.closed.set()
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def record_tx(self, data):
        # TCP can split a client command across reads. Only insert a query between frames.
        if self.tx_frame is None:
            return
        self.tx_frame.extend(data)
        while len(self.tx_frame) >= 2:
            length = self.tx_frame[1] + 1
            if len(self.tx_frame) < length:
                return
            if length < 3 or EchoStreamProtocol.checksum(self.tx_frame[:length - 1]) != self.tx_frame[length - 1]:
                self.tx_frame = None
                return
            del self.tx_frame[:length]


class DebugRelay:
    def __init__(self, control, mode, allowed, address=("0.0.0.0", DEBUG_PORT)):
        if mode not in ("receive", "passthrough", "trace"):
            raise ValueError("Choose Off, Receive, Passthrough or Trace for the debug socket.")
        try:
            self.allowed = IPv4Network(allowed or "", strict=False)
        except ValueError:
            raise ValueError("Set the allowed debug client IPv4 address or subnet.") from None
        if not self.allowed.prefixlen:
            raise ValueError("Use a specific IPv4 client or subnet for debugging.")

        self.control = control
        self.mode = mode
        self.address = address
        self.stop_event = Event()
        self.lock = Lock()
        self.peer = None
        self.listener = None
        self.thread = None

    def start(self):
        self.listener = socket.create_server(self.address, backlog=1)
        self.listener.settimeout(0.25)
        self.thread = Thread(target=self.serve, name="echo-debug", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.disconnect()
        if self.listener is not None:
            self.listener.close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def current(self):
        with self.lock:
            return self.peer

    def disconnect(self):
        peer = self.current()
        if peer:
            peer.close()

    def observe(self, direction, data):
        peer = self.current()
        if peer is None or peer.closed.is_set():
            return
        if self.mode == "trace":
            data = json.dumps({"direction": direction, "hex": data.hex()}).encode() + b"\n"
        elif direction != "rx":
            return
        try:
            peer.rx.put_nowait(data)
        except Full:
            # A stalled diagnostic consumer must never stop alarm processing.
            peer.close()

    def forward(self, send):
        if self.mode != "passthrough":
            return False
        peer = self.current()
        if peer is None:
            return False
        for _ in range(8):
            if peer.closed.is_set():
                break
            try:
                data = peer.tx.get_nowait()
            except Empty:
                break
            send(data)
            peer.record_tx(data)
        return not peer.closed.is_set() and peer.tx_frame == b""

    def serve(self):
        while not self.stop_event.is_set():
            try:
                client, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with client:
                if IPv4Address(address[0]) not in self.allowed:
                    LOG.warning("Rejected debug connection from %s.", address[0])
                    continue
                writable = self.mode == "passthrough"
                if not self.control.begin_debug(writable):
                    LOG.info("Debug connection rejected while the source is unavailable or an NID write is active.")
                    continue
                peer = Peer(client)
                try:
                    with self.lock:
                        self.peer = peer
                    client.settimeout(0.25)
                    self.exchange(peer, writable)
                except OSError:
                    LOG.debug("Debug client disconnected.", exc_info=True)
                finally:
                    peer.close()
                    with self.lock:
                        self.peer = None
                    self.control.end_debug(writable)

    def exchange(self, peer, writable):
        while not peer.closed.is_set() and not self.stop_event.is_set():
            readable, _, _ = select.select([peer.socket], [], [], 0.05)
            if readable:
                data = peer.socket.recv(4096)
                if not data or not writable:
                    return
                try:
                    peer.tx.put_nowait(data)
                except Full:
                    return
            while not peer.closed.is_set():
                try:
                    data = peer.rx.get_nowait()
                except Empty:
                    break
                peer.socket.sendall(data)
