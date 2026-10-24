"""LoRa packet parsing, CRC-8 verification and per-node session tracking."""
import re
from collections import deque


def crc8(data):
    """CRC-8 poly 0x07, init 0 - identical to the ESP32 firmware."""
    if isinstance(data, str):
        data = data.encode()
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def make_line(body):
    return "%s,C=%02X" % (body, crc8(body))


PKT = re.compile(r"(FROM=[A-Za-z0-9_]+,[^\r\n]*?),C=([0-9A-Fa-f]{2})")
RSSI = re.compile(r"RSSI\s*[=:]\s*(-?\d+)")
SNR = re.compile(r"SNR\s*[=:]\s*(-?\d+(?:\.\d+)?)")
NUMERIC = ("PKT", "RAIN", "SOIL", "HX", "PRESS", "UP", "MQ", "DIST")


def parse_line(line):
    m = PKT.search(line)
    if not m:
        return None
    body, c = m.group(1), int(m.group(2), 16)
    f = {}
    for kv in body.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in NUMERIC:
                try:
                    v = float(v) if k == "DIST" else int(v)
                except ValueError:
                    pass
            f[k] = v
    if "PRESS" in f and "HX" not in f:
        f["HX"] = f["PRESS"]  # FLOOD01 v2.4 names the pressure field PRESS
    r, s = RSSI.search(line), SNR.search(line)
    return dict(node=f.get("FROM"), fields=f, body=body, crc_rx=c, crc_ok=(crc8(body) == c),
                rssi=int(r.group(1)) if r else None, snr=float(s.group(1)) if s else None,
                raw=m.group(0))


class NodeLink:
    """Tracks one node's packet stream: loss, duplicates, replay, reboot, timestamp consistency.

    Uses the node's uptime (UP) as a monotonic session clock and compares it with the gateway
    receive clock. A replayed/delayed packet carries a stale UP -> timestamp skew.
    """

    SKEW_MAX = 20.0

    def __init__(self, node):
        self.node = node
        self.last_pkt = None
        self.last_up = None
        self.last_rx = None
        self.offset = None
        self.seen = deque(maxlen=200)
        self.received = self.lost = self.dups = self.crc_bad = self.replays = self.reboots = 0
        self.rssi = None
        self.snr = None

    def accept(self, p, rx):
        f = p["fields"]
        pkt, up = f.get("PKT"), f.get("UP")
        if not isinstance(pkt, int) or not isinstance(up, int):
            return "malformed"
        key = (pkt, up, p["crc_rx"])
        if key in self.seen:
            if self.last_pkt is not None and pkt < self.last_pkt - 2:
                self.replays += 1
                return "replay"
            self.dups += 1
            return "duplicate"
        if self.last_pkt is not None and pkt <= self.last_pkt:
            if pkt <= 3 and up <= 40:
                self.reboots += 1
                self.offset = rx - up
                self.last_pkt = None
            else:
                self.replays += 1
                self.seen.append(key)
                return "replay"
        if self.offset is not None:
            skew = up - (rx - self.offset)
            if abs(skew) > self.SKEW_MAX:
                self.replays += 1
                self.seen.append(key)
                return "stale_timestamp"
        else:
            self.offset = rx - up
        if self.last_pkt is not None:
            self.lost += max(0, pkt - self.last_pkt - 1)
        self.last_pkt, self.last_up, self.last_rx = pkt, up, rx
        self.received += 1
        self.seen.append(key)
        if p["rssi"] is not None:
            self.rssi, self.snr = p["rssi"], p["snr"]
        return "ok"

    def stats(self):
        tot = self.received + self.lost
        return dict(node=self.node, received=self.received, lost=self.lost, dups=self.dups,
                    crc_bad=self.crc_bad, replays=self.replays, reboots=self.reboots,
                    loss_pct=round(100.0 * self.lost / tot, 1) if tot else 0.0,
                    rssi=self.rssi, snr=self.snr, last_pkt=self.last_pkt)
