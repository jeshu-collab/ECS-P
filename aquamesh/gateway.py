"""Reads packet lines printed by the TTGO receiver over USB serial."""
import glob
import threading
import time


class SerialGateway(threading.Thread):
    def __init__(self, on_line, port=None, baud=115200, clock=None):
        super().__init__(daemon=True)
        self.on_line, self.port, self.baud, self.clock = on_line, port, baud, clock
        self.connected = False
        self.last_line_t = None
        self.error = ""

    def status(self):
        return dict(connected=self.connected, port=self.port, error=self.error,
                    last_line_t=self.last_line_t)

    def _find(self):
        c = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
        return c[0] if c else None

    def run(self):
        try:
            import serial
        except ImportError:
            self.error = "pyserial not installed (sudo apt install python3-serial)"
            return
        while True:
            port = self.port or self._find()
            if not port:
                self.connected, self.error = False, "no serial port found"
                time.sleep(2)
                continue
            try:
                with serial.Serial(port, self.baud, timeout=1) as s:
                    self.connected, self.error, self.port = True, "", port
                    while True:
                        raw = s.readline()
                        if not raw:
                            continue
                        line = raw.decode(errors="replace").strip()
                        self.last_line_t = time.time()
                        if line:
                            self.on_line(line, self.clock.now() if self.clock else None)
            except Exception as e:
                self.connected, self.error = False, "serial: %s" % str(e)[:80]
                time.sleep(2)
