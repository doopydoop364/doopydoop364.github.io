"""Low-overhead process-tree sampler for /proc. Measures CPU, memory and lifetime; never estimates frame rates."""

import os
from pathlib import Path
import threading
import time


def read_stat(proc_root, pid):
    """(ppid, ticks) from /proc/<pid>/stat, or None if gone. The command name may contain spaces/parens."""
    try:
        text = (Path(proc_root) / str(pid) / "stat").read_text()
        rest = text[text.rindex(")") + 2 :].split()
        return int(rest[1]), int(rest[11]) + int(rest[12])
    except (OSError, ValueError, IndexError):
        return None


def rss_bytes(proc_root, pid, page):
    try:
        return int((Path(proc_root) / str(pid) / "statm").read_text().split()[1]) * page
    except (OSError, ValueError, IndexError):
        return 0


def tree(proc_root, root_pid, limit=2000):
    """root_pid and its descendants, from a bounded scan of /proc."""
    parents = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return []
    for name in names[:50000]:
        if name.isdigit():
            st = read_stat(proc_root, name)
            if st:
                parents[int(name)] = st[0]
    if root_pid not in parents:
        return []
    children = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found, stack = [], [root_pid]
    while stack and len(found) < limit:
        pid = stack.pop()
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


class Sampler:
    """Samples a process tree every `interval` seconds on a daemon thread."""

    def __init__(self, pid, interval=1.0, proc_root="/proc", clock=time.monotonic, sleep=time.sleep, clk_tck=None, page=None, cpus=None, probe=None):
        self.pid, self.interval, self.root = pid, max(0.2, float(interval)), proc_root
        self.clock, self.sleep = clock, sleep
        self.tck = clk_tck or os.sysconf("SC_CLK_TCK")
        self.page = page or os.sysconf("SC_PAGE_SIZE")
        self.cpus = cpus or os.cpu_count() or 1
        self.samples = []
        self.started = clock()
        self.ended = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="arch-games-sampler")
        self._ticks = {}  # pid -> ticks; finished children keep their last value so their CPU is not lost
        self._last = None
        self.probe = probe  # callable(pids) -> bool: has the game shown a window yet?
        self.first_window = None

    def start(self):
        self._thread.start()
        return self

    def sample(self):
        now = self.clock()
        pids = tree(self.root, self.pid)
        if not pids:
            return False
        rss = 0
        for pid in pids:
            st = read_stat(self.root, pid)
            if st:
                self._ticks[pid] = st[1]
                rss += rss_bytes(self.root, pid, self.page)
        if self.probe and self.first_window is None:
            try:
                if self.probe(pids):
                    self.first_window = round(now - self.started, 2)
            except Exception:
                self.probe = None  # a broken probe must never disturb the game
        total = sum(self._ticks.values())
        cpu = None
        if self._last:
            dt, dticks = now - self._last[0], total - self._last[1]
            if dt > 0:
                cpu = max(0.0, dticks / self.tck / dt * 100.0)  # percent of one core
        self._last = (now, total)
        self.samples.append({"t": round(now - self.started, 3), "cpu_percent": cpu, "rss_bytes": rss, "processes": len(pids)})
        return True

    def _run(self):
        while not self._stop.is_set():
            if not self.sample():
                break
            self._stop.wait(self.interval)
        self.ended = self.clock()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=self.interval * 3 + 1)
        if self.ended is None:
            self.ended = self.clock()
        return self.summary()

    def summary(self):
        cpu = [s["cpu_percent"] for s in self.samples if s["cpu_percent"] is not None]
        rss = [s["rss_bytes"] for s in self.samples]
        end = self.ended if self.ended is not None else self.clock()
        return {
            "samples": len(self.samples),
            "interval_seconds": self.interval,
            "monitored_seconds": round(end - self.started, 2),
            "first_window_seconds": self.first_window,
            "cpu_percent_avg": round(sum(cpu) / len(cpu), 1) if cpu else None,
            "cpu_percent_peak": round(max(cpu), 1) if cpu else None,
            "cpu_percent_note": "percent of one core, summed over the process tree; {} logical CPUs".format(self.cpus),
            "rss_bytes_avg": int(sum(rss) / len(rss)) if rss else None,
            "rss_bytes_peak": max(rss) if rss else None,
            "rss_note": "resident set size summed over processes; shared pages are counted once per process",
        }


def xdotool_probe(environ=None, which=None, run=None):
    """Window-appearance probe via xdotool, or None when it cannot be done reliably (no xdotool/X11 display)."""
    import shutil
    import subprocess

    env = environ if environ is not None else os.environ
    which = which or shutil.which
    run = run or subprocess.run
    if not which("xdotool") or not env.get("DISPLAY"):
        return None

    def probe(pids):
        for pid in pids[:50]:
            result = run(["xdotool", "search", "--onlyvisible", "--pid", str(pid)], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return True
        return False
    return probe
