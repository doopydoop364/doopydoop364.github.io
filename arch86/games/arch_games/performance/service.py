"""Optional monitoring and explicit benchmarks. Benchmarks never run unless the user asks for one."""

from datetime import datetime, timezone
import tempfile
import uuid

from ..compatibility import intelligence
from ..config import AppError
from ..platform import apply_overrides, system_profile
from . import fps
from .sampler import Sampler, xdotool_probe

NAMESPACE = "performance"
MAX_BENCHMARK = 600


def monitor_factory(interval=1.0):
    probe = xdotool_probe()
    return lambda pid: Sampler(pid, interval, probe=probe).start()


def save(db, config, game_id, kind, record, profile, extra=None):
    machine = intelligence.machine_identity(config)
    entry = {
        "id": uuid.uuid4().hex, "game_id": game_id, "kind": kind, "at": datetime.now(timezone.utc).isoformat(),
        "machine": {k: machine[k] for k in ("id", "name", "architecture")},
        "renderer": (profile.get("graphics") or {}).get("renderer"), "accelerated": (profile.get("graphics") or {}).get("accelerated"),
        "wine_version": (profile.get("wine") or {}).get("version") or None,
        "exit_code": record.get("exit_code"), "state": record.get("state"), "launch_id": record.get("id"),
        "metrics": record.get("performance"),
        "fps": (extra or {}).get("fps", fps.UNAVAILABLE),
        "requested_seconds": (extra or {}).get("seconds"),
    }
    db.put(NAMESPACE, entry["id"], entry)
    return entry


def history(db, game_id):
    rows = [r["value"] for r in db.all(NAMESPACE) if r["value"]["game_id"] == game_id]
    return sorted(rows, key=lambda r: r["at"])


def aggregate(rows):
    metrics = [r["metrics"] for r in rows if r.get("metrics") and r["metrics"].get("samples")]
    if not metrics:
        return None
    def avg(key):
        values = [m[key] for m in metrics if m.get(key) is not None]
        return round(sum(values) / max(1, len(values)), 1)

    return {"runs": len(metrics), "cpu_percent_avg": avg("cpu_percent_avg"), "rss_mib_peak": round(max(m["rss_bytes_peak"] or 0 for m in metrics) / 2**20, 1)}


def benchmark(config, db, launcher, game_id, seconds, say=print, profile=None):
    """Launch the registered game, measure for `seconds`, stop it. Frame rate only if a real mechanism exists."""
    if not 5 <= seconds <= MAX_BENCHMARK:
        raise AppError("Benchmark duration must be 5..{} seconds".format(MAX_BENCHMARK))
    profile = profile if profile is not None else apply_overrides(system_profile(config.data_dir), config)
    kind = fps.mechanism(profile)
    with tempfile.TemporaryDirectory(prefix="arch-games-bench-", dir=config.cache_dir) as tmp:
        env = fps.environment(kind, tmp) if kind else {}
        plan = launcher.plan_registered(game_id, extra_env=env)
        say("Launching {} for {} s; frame-rate source: {}".format(game_id, seconds, kind or "none (CPU/memory only)"))
        record = launcher.launch(plan, timeout=seconds, monitor=monitor_factory(1.0))
        result = fps.summarize(kind, tmp) if kind else dict(fps.UNAVAILABLE)
    return save(db, config, game_id, "benchmark", record, profile, {"fps": result, "seconds": seconds})
