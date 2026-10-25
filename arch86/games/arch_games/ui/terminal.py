"""Standard-library keyboard UI with a deterministic ASCII/plain fallback."""

import os
import shutil
import sys
import unicodedata

SECTIONS = [
    "Browse Game Catalog",
    "My GameVault Library",
    "Compatible Games",
    "Installed Games",
    "Download Manager",
    "Compatibility Reports",
    "Wine Prefix Manager",
    "Settings",
]


def clean(value):
    return "".join(c for c in str(value) if unicodedata.category(c) not in {"Cc", "Cf", "Cs"})


def fit(value, width, ascii_only=False):
    text = clean(value)
    if ascii_only:
        text = text.encode("ascii", "replace").decode()
    output, used = [], 0
    for char in text:
        size = (
            0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        )
        if used + size > max(width, 0):
            break
        output.append(char)
        used += size
    return "".join(output)


def render_sections(profile, width=80, height=24, selected=0, ascii_only=False):
    arch = profile.get("os_architecture", "unknown")
    ram = profile.get("ram_total")
    summary = "{} | {} RAM".format(arch, "{:.1f} GiB".format(ram / 1024**3) if ram else "unknown")
    lines = ["ARCH GAMES 2.0", summary, ""]
    for index, name in enumerate(SECTIONS):
        lines.append(("> " if index == selected else "  ") + str(index + 1) + ". " + name)
    lines += ["", "Arrows/j/k: navigate | Enter: open | q: quit"]
    return [fit(line, width, ascii_only) for line in lines[: max(height, 1)]]


def choose_curses(profile):
    import curses

    def draw(screen):
        selected = 0
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            lines = render_sections(profile, max(width - 1, 1), max(height - 1, 1), selected)
            # Tiny consoles keep the selected item and key help visible.
            if height < 14:
                lines = [
                    fit("ARCH GAMES 2.0", width - 1),
                    fit(SECTIONS[selected], width - 1),
                    fit("j/k Enter q", width - 1),
                ][: max(height - 1, 1)]
            for y, line in enumerate(lines):
                try:
                    screen.addstr(y, 0, line)
                except (curses.error, UnicodeError):
                    pass
            screen.refresh()
            key = screen.getch()
            if key in {ord("q"), 27}:
                return None
            if key in {curses.KEY_DOWN, ord("j")}:
                selected = (selected + 1) % len(SECTIONS)
            elif key in {curses.KEY_UP, ord("k")}:
                selected = (selected - 1) % len(SECTIONS)
            elif ord("1") <= key <= ord("8"):
                return key - ord("1")
            elif key in {10, 13, curses.KEY_ENTER}:
                return selected

    return curses.wrapper(draw)


def menu(library, dispatch, plain=False, input_fn=input, output=print):
    use_curses = (
        not plain and sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "dumb") != "dumb"
    )
    while True:
        if use_curses:
            try:
                import curses

                selection = choose_curses(library.profile)
            except (ImportError, OSError):
                use_curses = False
                continue
            except curses.error:
                use_curses = False
                continue
        else:
            size = shutil.get_terminal_size((80, 24))
            for line in render_sections(library.profile, size.columns, 24, ascii_only=True):
                output(line)
            choice = input_fn("Section (1-8), /search, or q: ").strip()
            if choice.lower() in {"q", "quit", "exit"}:
                return
            if choice.startswith("/"):
                browse(library, dispatch, {"search": choice[1:]}, input_fn, output)
                continue
            if not choice.isdigit() or not 1 <= int(choice) <= 8:
                continue
            selection = int(choice) - 1
        if selection is None:
            return
        if selection < 4:
            filters = [
                {},
                {"source": "gamevault", "show_all": True},
                {"compatible": True},
                {"installed": True, "show_all": True},
            ][selection]
            browse(library, dispatch, filters, input_fn, output)
        else:
            dispatch(
                [["download", "list"], ["reports"], ["prefix", "list"], ["settings", "show"]][selection - 4]
            )
            prompts = {
                4: "r run | retry ID | cancel ID | cleanup ID | Enter back: ",
                5: "Enter to return: ",
                6: "create ID win32|win64 | check ID | backup ID | remove ID | Enter back: ",
                7: "connect | disconnect | set KEY VALUE | Enter back: ",
            }
            line = input_fn(prompts[selection]).strip()
            try:
                command = section_command(selection, line)
                if command:
                    dispatch(command)
                    from ..config import read_json

                    library.config.values = read_json(library.config.path)
                    library.load(offline=command[:2] != ["gamevault", "connect"])
            except ValueError as exc:
                output(str(exc))


def section_command(section, line):
    import shlex

    parts = shlex.split(line)
    if not parts:
        return None
    if section == 4:
        if parts == ["r"]:
            return ["download", "run"]
        if len(parts) == 2 and parts[0] in {"retry", "cancel", "cleanup"} and not parts[1].startswith("-"):
            return ["download", "cancel" if parts[0] == "cleanup" else parts[0], parts[1]] + (
                ["--cleanup"] if parts[0] == "cleanup" else []
            )
    if section == 6:
        if (
            len(parts) == 3
            and parts[0] == "create"
            and parts[2] in {"win32", "win64"}
            and not parts[1].startswith("-")
        ):
            return ["prefix", "create", parts[1], "--arch", parts[2]]
        if len(parts) == 2 and parts[0] in {"check", "backup", "remove"} and not parts[1].startswith("-"):
            return ["prefix"] + parts
    if section == 7:
        if len(parts) == 1 and parts[0] in {"connect", "disconnect"}:
            return ["gamevault"] + parts
        if len(parts) == 3 and parts[0] == "set" and all(not p.startswith("-") for p in parts):
            return ["settings"] + parts
    raise ValueError("Unrecognized action; use one of the displayed commands")


def browse(library, dispatch, filters, input_fn=input, output=print):
    page, per_page = 0, 12
    filters = dict(filters)
    while True:
        games, explanation = library.filtered(**filters)
        page = min(page, max((len(games) - 1) // per_page, 0))
        subset = games[page * per_page : (page + 1) * per_page]
        output(clean(explanation))
        for index, game in enumerate(subset, 1):
            status = library.reports.get(game.id, {}).get("status", "Unknown")
            output(
                fit(
                    "{:2}. {} [{}]{}".format(
                        index, game.title, status, " [installed]" if game.installed else ""
                    ),
                    max(shutil.get_terminal_size((80, 24)).columns - 1, 10),
                    True,
                )
            )
        output("Page {} | n/p pages | /query | a show all | number details | b back".format(page + 1))
        choice = input_fn("> ").strip()
        if choice in {"b", "q"}:
            return
        if choice == "n":
            page += 1
        elif choice == "p":
            page = max(0, page - 1)
        elif choice == "a":
            filters["show_all"] = True
        elif choice.startswith("/"):
            filters["search"] = choice[1:]
            page = 0
        elif choice.isdigit() and 1 <= int(choice) <= len(subset):
            game = subset[int(choice) - 1]
            dispatch(["details", game.id])
            action = input_fn("i install, d queue download, Enter return: ").strip()
            if action == "i":
                dispatch(["install", game.id])
            elif action == "d":
                dispatch(["download", "add", game.id])
            library.load(offline=True)
