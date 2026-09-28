"""A fake Samil inverter speaking the network protocol, for tests."""

import socket
import threading

from custom_components.samil.protocol import construct_message, read_message

# Status format: one type id per 16-bit word in the status payload.
FORMAT_IDS = [0x00, 0x01, 0x02, 0x04, 0x05, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C,
              0x11, 0x27, 0x28, 0x31, 0x32, 0x33]
VALUES = {
    0x00: 351,  # internal temp 35.1 C
    0x01: 5865, 0x02: 67,  # PV voltages 586.5 / 6.7
    0x04: 48, 0x05: 1,  # PV currents 4.8 / 0.1
    0x07: 0, 0x08: 77012,  # energy total 7701.2 kWh (split over two words)
    0x09: 0, 0x0A: 4512,  # operation hours
    0x0B: 2589,  # output power W
    0x0C: 1,  # Normal
    0x11: 2120,  # energy today 21.20 kWh
    0x27: 2822, 0x28: 0,
    0x31: 36, 0x32: 2426, 0x33: 5001,
}


def _model_payload(serial: str) -> bytes:
    def pad(s, n):
        return s.encode().ljust(n, b"\x00")
    return (b"1" + pad("4500", 6) + pad("V1.20", 5) + pad("SolarRiver 4500TL-D", 16)[:16]
            + pad("SamilPower", 16) + pad(serial, 16) + pad("V1.0", 5) + pad("V1.0", 5) + b"1")


class FakeInverter:
    def __init__(self, serial="DD2142L102", udp_port=1300):
        self.serial = serial
        self.online = True
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp.bind(("127.0.0.1", udp_port))
        self.udp.settimeout(0.2)
        self.conn = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                msg, addr = self.udp.recvfrom(1024)
            except TimeoutError:
                continue
            except OSError:
                return
            if not self.online or b"I AM SERVER" not in msg:
                continue
            try:
                conn = socket.create_connection((addr[0], 1200), timeout=5)
            except OSError:
                continue
            self.conn = conn
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        f = conn.makefile("rwb")
        try:
            while self.online:
                ident, _ = read_message(f)
                if ident == b"\x01\x00\x02":
                    resp = (b"\x01\x80\x02", bytes(FORMAT_IDS))
                elif ident == b"\x01\x02\x02":
                    payload = b"".join(VALUES[t].to_bytes(2, "big", signed=(t == 0))
                                       if t not in (0x07, 0x08) else
                                       ((VALUES[0x08] >> 16) if t == 0x07 else VALUES[0x08] & 0xFFFF).to_bytes(2, "big")
                                       for t in FORMAT_IDS)
                    resp = (b"\x01\x82\x02", payload)
                elif ident == b"\x01\x03\x02":
                    resp = (b"\x01\x83\x02", _model_payload(self.serial))
                else:
                    continue
                f.write(construct_message(*resp))
                f.flush()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def go_offline(self):
        """Simulate sunset: drop the connection and ignore discovery."""
        self.online = False
        if self.conn:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
                self.conn.close()
            except OSError:
                pass

    def close(self):
        self.go_offline()
        self._stop.set()
        self.udp.close()
        self.thread.join(2)
