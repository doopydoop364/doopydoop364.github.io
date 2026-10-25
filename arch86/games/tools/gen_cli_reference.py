"""Generate the command reference block of docs/CLI_3.md from the real argparse parser (so it cannot drift).

    python3 tools/gen_cli_reference.py          # print the block
    python3 tools/gen_cli_reference.py --write  # update docs/CLI_3.md between the markers
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arch_games.cli import make_parser  # noqa: E402

BEGIN, END = "<!-- BEGIN GENERATED REFERENCE -->", "<!-- END GENERATED REFERENCE -->"


def walk(parser, prefix, out):
    subparsers = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    options = [a for a in parser._actions if not isinstance(a, (argparse._SubParsersAction, argparse._HelpAction)) and a.dest != "help"]
    if prefix:
        usage = parser.format_usage().replace("usage: ", "").strip().replace("\n", " ")
        out.append("#### `{}`\n".format(" ".join(prefix)))
        helptext = getattr(parser, "_ag_help", None)
        if helptext:
            out.append(helptext + "\n")
        out.append("```\n{}\n```".format(" ".join(usage.split())))
        rows = []
        for a in options:
            flag = ", ".join(a.option_strings) if a.option_strings else a.dest
            if a.help and a.help != argparse.SUPPRESS:
                rows.append("- `{}` — {}".format(flag, a.help))
        if rows:
            out.append("\n".join(rows))
        out.append("")
    if subparsers:
        helps = {c.dest: c.help for c in subparsers._choices_actions}
        for name, sub in subparsers.choices.items():
            sub._ag_help = helps.get(name)
            walk(sub, prefix + [name], out)


def reference():
    out = []
    walk(make_parser(), [], out)
    return "\n".join(out).rstrip() + "\n"


if __name__ == "__main__":
    block = reference()
    if "--write" in sys.argv:
        path = Path(__file__).resolve().parents[1] / "docs" / "CLI_3.md"
        text = path.read_text()
        head, rest = text.split(BEGIN)
        _, tail = rest.split(END)
        path.write_text(head + BEGIN + "\n\n" + block + "\n" + END + tail)
    else:
        print(block)
