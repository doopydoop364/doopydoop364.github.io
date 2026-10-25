"""Minimal, bounded readers for Windows .lnk shortcuts and Wine .desktop entries (evidence only; never executed)."""

import os
from pathlib import Path
import re
import struct

MAX_LNK = 64 * 1024


def parse_lnk(path):
    """-> {"target": "C:\\\\Game\\\\Game.exe", "working_dir": ..., "arguments": ...} or None. Follows MS-SHLLINK for the local path only."""
    try:
        p = Path(path)
        if p.is_symlink() or p.stat().st_size > MAX_LNK:
            return None
        data = p.read_bytes()
        if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return None
        flags = struct.unpack_from("<I", data, 20)[0]
        pos = 76
        if flags & 0x1:  # HasLinkTargetIDList
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        target = None
        if flags & 0x2:  # HasLinkInfo
            start = pos
            size, header, info_flags = struct.unpack_from("<III", data, start)
            if info_flags & 0x1:
                local = struct.unpack_from("<I", data, start + 16)[0]
                end = data.index(b"\0", start + local)
                target = data[start + local : end].decode("cp1252", "replace")
                if header >= 0x24:
                    uni = struct.unpack_from("<I", data, start + 28)[0]
                    if uni:
                        raw = data[start + uni :]
                        for i in range(0, min(len(raw) - 1, 2048), 2):
                            if raw[i : i + 2] == b"\0\0":
                                target = raw[:i].decode("utf-16-le", "replace")
                                break
            pos = start + size
        unicode_strings = bool(flags & 0x80)
        strings = {}
        for bit, name in ((0x4, "name"), (0x8, "relative"), (0x10, "working_dir"), (0x20, "arguments")):
            if flags & bit:
                count = struct.unpack_from("<H", data, pos)[0]
                pos += 2
                raw = data[pos : pos + count * (2 if unicode_strings else 1)]
                strings[name] = raw.decode("utf-16-le" if unicode_strings else "cp1252", "replace").rstrip("\x00")  # Wine writes a trailing NUL
                pos += len(raw)
        if not target:
            return None
        return {"target": target, "working_dir": strings.get("working_dir"), "arguments": strings.get("arguments")}
    except (OSError, ValueError, struct.error):
        return None


def prefix_shortcuts(prefix, limit=400):
    """Targets of Start Menu / Desktop shortcuts inside the prefix's user profiles: {lnk path: parsed}."""
    found = {}
    users = Path(prefix) / "drive_c" / "users"
    if not users.is_dir():
        return found
    count = 0
    for user in sorted(users.iterdir()):
        if user.is_symlink() or not user.is_dir() or user.name.lower() == "public":
            continue
        for sub in ("Start Menu", "Desktop"):
            root = user / sub
            if not root.is_dir() or root.is_symlink():
                continue
            for base, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(base, d))]
                for name in names:
                    if name.lower().endswith(".lnk"):
                        count += 1
                        if count > limit:
                            return found
                        parsed = parse_lnk(os.path.join(base, name))
                        if parsed:
                            found[os.path.join(base, name)] = parsed
    return found


def desktop_entries_for_prefix(prefix, applications_dir=None):
    """Wine-generated .desktop files for this prefix -> the .lnk files they launch (then parsed by parse_lnk)."""
    folder = Path(applications_dir) if applications_dir else Path.home() / ".local/share/applications/wine"
    result = []
    if not folder.is_dir():
        return result
    for base, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(base, d))]
        for name in names[:2000]:
            if not name.endswith(".desktop"):
                continue
            try:
                text = Path(base, name).read_text(errors="replace")[:8192]
            except OSError:
                continue
            exec_line = next((l for l in text.splitlines() if l.startswith("Exec=")), "")
            if str(prefix) not in exec_line:
                continue
            m = re.search(r'/Unix\s+"?([^"\n]+?\.lnk)"?(?:\s|$)', exec_line.replace("\\\\", "\\"))
            result.append({"desktop": str(Path(base, name)), "name": next((l[5:] for l in text.splitlines() if l.startswith("Name=")), name), "lnk": m.group(1) if m else None})
    return result
