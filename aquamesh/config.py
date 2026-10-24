import copy
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "period_s": 5,
    "nodes": {"FLOOD01": "flood", "GAS01": "gas"},
    # Calibration: raw ADC -> 0..1.  ASSUMPTION: higher raw = wetter for both sensors
    # (matches the node's ALERT_RAW logic).  Use the dashboard "Set dry/wet" buttons to recalibrate.
    "cal": {"rain": {"dry": 100, "wet": 1500}, "soil": {"dry": 300, "wet": 2500}},
    "th": {
        "rain_high_n": 0.30,
        "soil_rise_slope": 0.05,      # per minute (normalised units)
        "soil_sat_n": 0.70,
        "hx_slope_min": 300.0,        # raw counts / minute
        "river_cover_slope": 0.06,    # fraction / minute
        "river_flow_z": 3.0,
        "dist_z": 3.0,
        "drain_level_slope": 0.04,    # fraction / minute
        "drain_level_high": 0.80,
        "drain_flow_floor": 0.05,
        "drain_flow_rel_slope": 0.25, # relative change / minute
        "land_still_slope": 0.008,    # fraction / minute
        "land_wet_present": 0.03,
        "z_min": 2.5,                 # trend significance (slope / std-error)
        "on_s": 12,                   # persistence before an event is asserted
        "off_s": 30,                  # hysteresis before it is retracted
    },
    "terrain": {
        "elev": {"river": 5.0, "drain": 4.0, "land": 2.0},   # cm above a datum
        "tol": 0.5,
        "connected": {"river>drain": True, "river>land": True, "drain>land": True},
    },
    "gas_in_inference": False,
}


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


class Config:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "config.json")
        self.d = copy.deepcopy(DEFAULTS)
        if os.path.exists(self.path):
            try:
                _merge(self.d, json.load(open(self.path)))
            except Exception:
                pass

    def save(self):
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w"), indent=1)
        os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self.d[k]
