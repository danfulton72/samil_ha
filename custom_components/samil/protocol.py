"""Samil Power inverter network protocol (synchronous).

Adapted from https://github.com/mhvis/solar (MIT License,
Copyright (c) 2016 Maarten Visscher). Vendored so the integration does not
pull in that package's CLI dependencies (paho-mqtt<2 conflicts with Home
Assistant).

Connection model: we listen on TCP port 1200 and send an "I AM SERVER"
message to UDP port 1300 (broadcast, or unicast to a known inverter IP).
The inverter then opens a TCP connection back to us on port 1200.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, BinaryIO

_LOGGER = logging.getLogger(__name__)

LISTEN_PORT = 1200
DISCOVERY_PORT = 1300


class InverterNotFoundError(Exception):
    """No inverter connected back after the discovery messages."""


class InverterConnectionError(Exception):
    """The connection with the inverter was lost or returned garbage."""


# ---------------------------------------------------------------------------
# Message framing
# ---------------------------------------------------------------------------


def calculate_checksum(message: bytes) -> bytes:
    """Return the 2-byte checksum of a message (without checksum)."""
    return (sum(message) & 0xFFFF).to_bytes(2, byteorder="big")


def construct_message(identifier: bytes, payload: bytes) -> bytes:
    """Build a message from a 3-byte identifier and a payload."""
    message = b"\x55\xaa" + identifier + len(payload).to_bytes(2, "big") + payload
    return message + calculate_checksum(message)


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if data is None or len(data) != size:
        raise InverterConnectionError("Connection closed by inverter")
    return data


def read_message(stream: BinaryIO) -> tuple[bytes, bytes]:
    """Read the next message, returning (identifier, payload)."""
    start = _read_exact(stream, 2)
    if start != b"\x55\xaa":
        raise InverterConnectionError(f"Invalid start of message: {start.hex()}")
    identifier = _read_exact(stream, 3)
    size_bytes = _read_exact(stream, 2)
    size = int.from_bytes(size_bytes, "big")
    if size > 4096:
        raise InverterConnectionError(f"Unexpected payload size {size}")
    payload = _read_exact(stream, size) if size else b""
    checksum = _read_exact(stream, 2)
    if checksum != calculate_checksum(start + identifier + size_bytes + payload):
        raise InverterConnectionError("Invalid checksum")
    return identifier, payload


def decode_string(val: bytes) -> str:
    """Decode a possibly NUL-terminated ASCII byte string."""
    return val.partition(b"\x00")[0].decode("ascii", errors="replace").strip()


# ---------------------------------------------------------------------------
# Status value decoding
# ---------------------------------------------------------------------------

OPERATION_MODES = {
    0: "Wait",
    1: "Normal",
    2: "Fault",
    3: "Permanent fault",
    4: "Check",
    5: "PV power off",
}

DEVICE_TYPES = {
    "1": "Single-phase inverter",
    "2": "Three-phase inverter",
    "3": "SolarEnvi Monitor",
    "4": "R-phase inverter of the three combined single-phase ones",
    "5": "S-phase inverter of the three combined single-phase ones",
    "6": "T-phase inverter of the three combined single-phase ones",
}


@dataclass(frozen=True)
class _Field:
    """A numeric status field made of one or more 16-bit words."""

    type_ids: tuple[int, ...]
    scale: int = 0
    signed: bool = False

    def value(self, fmt: bytes, payload: bytes) -> float | None:
        indices = [fmt.find(bytes([t])) for t in self.type_ids]
        if -1 in indices:
            return None
        raw = b"".join(payload[i * 2 : i * 2 + 2] for i in indices)
        if len(raw) != 2 * len(indices):
            return None
        num = int.from_bytes(raw, "big", signed=self.signed)
        return float(Decimal(num).scaleb(self.scale))


def _present(fmt: bytes, type_id: int) -> bool:
    return fmt.find(bytes([type_id])) != -1


def parse_status(fmt: bytes, payload: bytes) -> dict[str, Any]:
    """Decode a status payload using the inverter's status format."""
    out: dict[str, Any] = {}

    def put(name: str, *fields: _Field) -> None:
        for field in fields:
            val = field.value(fmt, payload)
            if val is not None:
                out[name] = val
                return

    mode = _Field((0x0C,)).value(fmt, payload)
    if mode is not None:
        out["operation_mode"] = OPERATION_MODES.get(int(mode), f"Unknown ({int(mode)})")

    put("total_operation_time", _Field((0x09, 0x0A)))
    put("pv1_input_power", _Field((0x27,)))
    put("pv2_input_power", _Field((0x28,)))
    put("pv1_voltage", _Field((0x01,), -1))
    put("pv2_voltage", _Field((0x02,), -1))
    put("pv1_current", _Field((0x04,), -1))
    put("pv2_current", _Field((0x05,), -1))
    put("output_power", _Field((0x0B,)), _Field((0x34,)))
    put("energy_today", _Field((0x11,), -2))
    put("energy_total", _Field((0x07, 0x08), -1), _Field((0x35, 0x36), -1))

    three_phase = _present(fmt, 0x51)
    suffix = "_r_phase" if three_phase else ""
    put(f"grid_voltage{suffix}", _Field((0x32,), -1))
    put(f"grid_current{suffix}", _Field((0x31,), -1))
    put(f"grid_frequency{suffix}", _Field((0x33,), -2))
    if three_phase:
        put("grid_voltage_s_phase", _Field((0x52,), -1))
        put("grid_current_s_phase", _Field((0x51,), -1))
        put("grid_frequency_s_phase", _Field((0x53,), -2))
        put("grid_voltage_t_phase", _Field((0x72,), -1))
        put("grid_current_t_phase", _Field((0x71,), -1))
        put("grid_frequency_t_phase", _Field((0x73,), -2))

    put("internal_temperature", _Field((0x00,), -1, True))
    put("heatsink_temperature", _Field((0x2F,), -1, True))
    return out


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class Inverter:
    """A connected inverter. Methods are blocking and not thread-safe."""

    def __init__(self, sock: socket.socket, addr: tuple[str, int]) -> None:
        self.sock = sock
        self.addr = addr
        self.sock.settimeout(15.0)
        self._file = sock.makefile("rwb")
        self._status_format: bytes | None = None

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for closer in (self._file.close, self.sock.close):
            try:
                closer()
            except OSError:
                pass

    def request(self, identifier: bytes, expected: bytes) -> bytes:
        message = construct_message(identifier, b"")
        try:
            self._file.write(message)
            self._file.flush()
            # Skip stray/late responses (e.g. to an earlier timed-out request).
            for _ in range(5):
                resp_id, payload = read_message(self._file)
                if resp_id.startswith(expected):
                    return payload
                _LOGGER.debug("Ignoring unexpected response %s", resp_id.hex())
        except (OSError, ValueError) as err:
            raise InverterConnectionError(str(err)) from err
        raise InverterConnectionError("No matching response from inverter")

    def model(self) -> dict[str, str]:
        p = self.request(b"\x01\x03\x02", b"\x01\x83")
        return {
            "device_type": DEVICE_TYPES.get(decode_string(p[0:1]), decode_string(p[0:1])),
            "va_rating": decode_string(p[1:7]),
            "firmware_version": decode_string(p[7:12]),
            "model_name": decode_string(p[12:28]),
            "manufacturer": decode_string(p[28:44]),
            "serial_number": decode_string(p[44:60]),
            "communication_version": decode_string(p[60:65]),
            "other_version": decode_string(p[65:70]),
        }

    def status(self) -> dict[str, Any]:
        if self._status_format is None:
            self._status_format = self.request(b"\x01\x00\x02", b"\x01\x80")
        payload = self.request(b"\x01\x02\x02", b"\x01\x82")
        if len(payload) != 2 * len(self._status_format):
            _LOGGER.debug(
                "Status payload/format size mismatch: format=%s payload=%s",
                self._status_format.hex(),
                payload.hex(),
            )
        return parse_status(self._status_format, payload)


class InverterFinder:
    """Owns the TCP listener on port 1200 and sends discovery messages."""

    def __init__(self, interface_ip: str = "", inverter_host: str = "") -> None:
        self.interface_ip = interface_ip or ""
        self.inverter_host = inverter_host or ""
        self._listen: socket.socket | None = None

    def open(self) -> None:
        if self._listen is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.interface_ip, LISTEN_PORT))
            sock.listen()
        except OSError:
            sock.close()
            raise
        self._listen = sock

    def close(self) -> None:
        if self._listen is not None:
            try:
                self._listen.close()
            finally:
                self._listen = None

    def find(self, attempts: int = 3, interval: float = 5.0) -> Inverter:
        """Send discovery messages until an inverter connects back."""
        self.open()
        assert self._listen is not None
        target = (self.inverter_host or "<broadcast>", DISCOVERY_PORT)
        message = construct_message(b"\x00\x40\x02", b"I AM SERVER")
        self._listen.settimeout(interval)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp.bind((self.interface_ip, 0))
            deadline_attempts = attempts
            while deadline_attempts > 0:
                deadline_attempts -= 1
                _LOGGER.debug("Sending discovery message to %s:%s", *target)
                try:
                    udp.sendto(message, target)
                except OSError as err:
                    _LOGGER.debug("Discovery send failed: %s", err)
                end = time.monotonic() + interval
                while (remaining := end - time.monotonic()) > 0:
                    self._listen.settimeout(remaining)
                    try:
                        sock, addr = self._listen.accept()
                    except TimeoutError:
                        break
                    except OSError as err:
                        raise InverterNotFoundError(str(err)) from err
                    if self.inverter_host and addr[0] != self.inverter_host:
                        _LOGGER.debug("Ignoring connection from %s", addr[0])
                        sock.close()
                        continue
                    _LOGGER.debug("Inverter connected from %s", addr[0])
                    time.sleep(1.0)  # Inverter needs a moment before requests
                    return Inverter(sock, addr)
        raise InverterNotFoundError
