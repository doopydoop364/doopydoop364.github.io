"""Frame-rate measurement only through mechanisms that really measure frames. Otherwise: unavailable."""

import csv
from pathlib import Path
import shutil


def mechanism(profile=None, which=None):
    """The first available real frame-rate source, or None. Nothing is simulated."""
    which = which or shutil.which
    if which("mangohud"):
        return "mangohud"
    renderer = ((profile or {}).get("graphics") or {}).get("renderer", "")
    if any(word in renderer.lower() for word in ("llvmpipe", "mesa", "radeonsi", "iris", "zink")):
        return "gallium-hud"
    return None


def environment(kind, directory):
    """Environment that makes the chosen mechanism write frame data into `directory`."""
    directory = str(directory)
    if kind == "mangohud":
        return {"MANGOHUD": "1", "MANGOHUD_CONFIG": "autostart_log=1,log_interval=100,output_folder={},no_display".format(directory)}
    if kind == "gallium-hud":
        return {"GALLIUM_HUD": "fps", "GALLIUM_HUD_DUMP_DIR": directory, "GALLIUM_HUD_VISIBLE": "false"}
    return {}


def parse_mangohud_csv(path):
    """MangoHud logs: a few header lines, then a CSV with an 'fps' column. Returns the list of fps values."""
    values = []
    with open(path, newline="", errors="replace") as stream:
        lines = stream.read().splitlines()
    start = next((i for i, line in enumerate(lines) if line.lower().startswith("fps,")), None)
    if start is None:
        return values
    for row in csv.DictReader(lines[start:]):
        try:
            values.append(float(row["fps"]))
        except (KeyError, ValueError, TypeError):
            continue
    return values


def parse_gallium_dump(path):
    """Gallium HUD dumps: one 'value' per line (one file per graph)."""
    values = []
    with open(path, errors="replace") as stream:
        for line in stream:
            try:
                values.append(float(line.split()[-1]))
            except (ValueError, IndexError):
                continue
    return values


def summarize(kind, directory):
    """Result dict from the files a mechanism produced; honest 'unavailable' when nothing usable exists."""
    directory = Path(directory)
    values = []
    if kind == "mangohud":
        for file in sorted(directory.glob("*.csv")):
            if not file.name.endswith("_summary.csv"):
                values += parse_mangohud_csv(file)
    elif kind == "gallium-hud":
        for file in sorted(directory.glob("*fps*")):
            values += parse_gallium_dump(file)
    values = [v for v in values if 0 <= v < 100000]
    if not values:
        return {"available": False, "mechanism": kind, "reason": "no frame data was produced (the game may not use the instrumented graphics API)"}
    ordered = sorted(values)
    return {
        "available": True, "mechanism": kind, "samples": len(values),
        "fps_avg": round(sum(values) / len(values), 1), "fps_min": round(ordered[0], 1), "fps_max": round(ordered[-1], 1),
        "fps_p1_low": round(ordered[max(0, int(len(ordered) * 0.01))], 1),
    }


UNAVAILABLE = {"available": False, "mechanism": None, "reason": "no frame-rate source installed (MangoHud) or applicable (Mesa HUD needs an OpenGL path on a Mesa driver); not estimated"}
