"""Packet parsing and validation.  Bad packets are counted and dropped; they never reach the reasoning engine."""
import re

KV = re.compile(r"([A-Za-z0-9_]+)=([^,\s]*)")

# per node: fields -> (min, max).  HX is a signed 24-bit ADC count.
SCHEMA = {
    "FLOOD01": {"RAIN": (0, 4095), "HX": (-8388608, 8388607)},
    "GAS01":   {"MQ": (0, 4095), "SOIL": (0, 4095), "OVF": (0, 1),
                "AX": (-32768, 32767), "AY": (-32768, 32767), "AZ": (-32768, 32767),
                "GX": (-32768, 32767), "GY": (-32768, 32767), "GZ": (-32768, 32767)},
    # sim-only / future hardware (accepted only when the twin allows them)
    "DRAIN01": {"LVL": (0, 100), "FLOW": (0, 1000)},
    "RIVER01": {"LVL": (0, 500)},
}
OPTIONAL = {"RSSI", "SNR", "VBAT", "UP", "FW", "FLAGS", "C", "PKT", "STATUS", "FROM", "T"}
FUTURE_NODES = {"DRAIN01", "RIVER01"}


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def parse_line(msg):
    """Find the payload in a serial line.  Returns (dict|None, payload_text|None)."""
    i = msg.find("FROM=")
    if i < 0:
        return None, None
    text = msg[i:]
    d = {k.upper(): v for k, v in KV.findall(text)}
    return (d, text) if "FROM" in d else (None, None)


def validate(d, text, allow_future=False):
    """Returns (packet|None, errors[], flags[]).  packet has typed values."""
    errs, flags = [], []
    hw = d.get("FROM", "").upper()
    if hw not in SCHEMA or (hw in FUTURE_NODES and not allow_future):
        return None, [f"unknown node id '{hw}'"], flags
    pkt = {"hw": hw, "values": {}, "status": (d.get("STATUS") or "NORMAL").upper()}
    if pkt["status"] not in ("NORMAL", "ALERT"):
        errs.append(f"bad STATUS '{pkt['status']}'")
    try:
        pkt["pkt"] = int(float(d["PKT"])) if "PKT" in d else None
    except ValueError:
        errs.append("bad PKT")
        pkt["pkt"] = None
    if pkt["pkt"] is not None and not (0 <= pkt["pkt"] < 2 ** 32):
        errs.append("PKT out of range")
    for k, (lo, hi) in SCHEMA[hw].items():
        if k not in d:
            continue
        try:
            v = int(float(d[k]))
        except ValueError:
            errs.append(f"{k} not numeric")
            continue
        if not lo <= v <= hi:
            errs.append(f"{k}={v} outside [{lo},{hi}]")
            continue
        pkt["values"][k] = v
    if not pkt["values"]:
        errs.append("no sensor fields")
    for k in ("RSSI", "SNR", "VBAT"):
        try:
            pkt[k.lower()] = float(d[k]) if k in d else None
        except ValueError:
            pkt[k.lower()] = None
    # optional integrity check from the improved firmware:  ...,C=3F   (CRC-8 of text before ",C=")
    if "C" in d:
        j = text.find(",C=")
        try:
            ok = j > 0 and crc8(text[:j].encode()) == int(d["C"], 16)
        except ValueError:
            ok = False
        if not ok:
            errs.append("CRC mismatch")
        else:
            flags.append("CRC_OK")
    else:
        flags.append("NO_CRC")
    pkt["flags"] = flags
    return (None if errs else pkt), errs, flags
