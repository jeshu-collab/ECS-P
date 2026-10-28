"""Hysteresis: stops alerts flickering around a threshold, while validated critical events trigger instantly."""
RANK = {"UNASSESSED": -1, "NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}


class Hysteresis:
    def __init__(self, init="UNASSESSED", up_s=10.0, down_s=60.0, to_unassessed_s=15.0, from_unassessed_s=5.0):
        self.cur, self.up_s, self.down_s = init, up_s, down_s
        self.tu, self.fu = to_unassessed_s, from_unassessed_s
        self.pending, self.since = None, None
        self.dwell_needed = 0.0

    def _need(self, target):
        if target == "CRITICAL":
            return 0.0                       # immediate
        if target == "UNASSESSED":
            return self.tu
        if self.cur == "UNASSESSED":
            return self.fu
        return self.up_s if RANK[target] > RANK[self.cur] else self.down_s

    def step(self, candidate, now):
        """Returns (state, transition|None)."""
        if candidate == self.cur:
            self.pending = self.since = None
            return self.cur, None
        if candidate != self.pending:
            self.pending, self.since = candidate, now
        self.dwell_needed = self._need(candidate)
        if now - self.since >= self.dwell_needed:
            old, self.cur = self.cur, candidate
            self.pending = self.since = None
            return self.cur, {"from": old, "to": self.cur, "dwell_s": round(self.dwell_needed, 1)}
        return self.cur, None

    def info(self, now):
        return {"state": self.cur, "pending": self.pending,
                "pending_for_s": None if self.since is None else round(now - self.since, 1),
                "needs_s": self.dwell_needed if self.pending else None}
