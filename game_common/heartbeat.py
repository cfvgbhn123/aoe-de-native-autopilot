import threading
import time

class HeartbeatPump:
    """Keep the native watchdog fed while a bounded controller step runs.

    Only the controller renews the lease. A stuck controller therefore still
    loses its bridge after the lease and the configured native timeout.
    """
    def __init__(self, write, *, interval=0.5, lease_seconds=15.0, clock=time.monotonic):
        self.write = write
        self.interval = interval
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.deadline = 0.0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="aoe-heartbeat", daemon=True)

    def refresh(self):
        with self.lock:
            if self.stopped.is_set():
                return
            self.deadline = self.clock() + self.lease_seconds
            self.write()

    def _pulse(self):
        with self.lock:
            if not self.stopped.is_set() and self.clock() < self.deadline:
                self.write()

    def _run(self):
        while not self.stopped.wait(self.interval):
            self._pulse()

    def start(self):
        self.refresh()
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join()
