import time


class Clock:
    """Wall clock with optional speed-up (simulation / replay) or manual stepping (tests)."""

    def __init__(self, speed=1.0):
        self.speed = float(speed)
        self._r0 = time.time()
        self._v0 = self._r0
        self.manual = None

    def now(self):
        if self.manual is not None:
            return self.manual
        return self._v0 + (time.time() - self._r0) * self.speed

    def set_speed(self, sp):
        v = self.now()
        self._r0 = time.time()
        self._v0 = v
        self.speed = float(sp)

    def set_time(self, v):
        self._r0 = time.time()
        self._v0 = v


def clamp(x, a, b):
    return a if x < a else b if x > b else x


def hhmmss(t):
    return time.strftime("%H:%M:%S", time.localtime(t))
