"""Startup/memory benchmark: python3 tools/bench_startup.py PATH_TO_2.0_CHECKOUT_ROOT PATH_TO_THIS_games_DIR  (median of 5 runs, fresh --state-dir each)."""
import os
import subprocess
import sys
import tempfile
import time

def run(cwd, args, n=5):
    times, rss = [], 0
    for _ in range(n):
        with tempfile.TemporaryDirectory() as d:
            t = time.perf_counter()
            p = subprocess.Popen([sys.executable, "-m", "arch_games", "--state-dir", d] + args, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _, status, ru = os.wait4(p.pid, 0)
            times.append(time.perf_counter() - t)
            rss = max(rss, ru.ru_maxrss)
    times.sort()
    return "{:6.0f} ms (median of {}), peak RSS {:5.1f} MiB".format(times[len(times)//2]*1000, n, rss/1024)
v2 = sys.argv[1]
v3 = sys.argv[2]
for label, args in [("--version", ["--version"]), ("list --source pacman", ["list", "--source", "pacman"]), ("doctor", ["doctor"])]:
    print("{:22} 2.0: {}".format(label, run(v2+"/arch86/games", args)))
    print("{:22} 3.0: {}".format("", run(v3, args)))
