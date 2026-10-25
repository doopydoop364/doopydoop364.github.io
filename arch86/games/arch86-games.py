#!/usr/bin/env python3
"""Arch Games: repository-aware catalog for Arch Linux 32 and x86_64."""
import html
import os
import pathlib
import platform
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
ARCH = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip() or platform.machine()
SUPPORTED = ARCH in ("i686", "pentium4", "x86_64")
CATALOG = []
for line in (HERE / "catalog.txt").read_text(encoding="utf-8").splitlines():
    parts = line.split("|", 4)
    if len(parts) == 5:
        title, pkg, genre, art, desc = parts
        CATALOG.append(dict(title=title, pkg=pkg, genre=genre, art=art, desc=desc))

def run(*args):
    return subprocess.run(args, capture_output=True, text=True)

def packages():
    """Use configured pacman sync DB, not assumptions about 32-/64-bit support."""
    sync = run("pacman", "-Slq")
    if sync.returncode:
        print("Unable to read pacman repositories. Run 'sudo pacman -Sy' if databases are missing.", file=sys.stderr)
        return None
    return set(sync.stdout.splitlines())

def installed():
    result = run("pacman", "-Qq")
    return set(result.stdout.splitlines()) if result.returncode == 0 else set()

def visible():
    available = packages()
    if available is None:
        return []
    owned = installed()
    return [dict(g, installed=g["pkg"] in owned) for g in CATALOG if g["pkg"] in available or g["pkg"] in owned]

def details(g):
    print("\n{} {} — {}\n{}\nPackage: {}\nStatus: {}".format(g["art"], g["title"], g["genre"], g["desc"], g["pkg"], "Installed" if g["installed"] else "Available"))

def install(g):
    details(g)
    if g["installed"]:
        print("Already installed.")
        return
    if input("Install using pacman? [y/N] ").strip().lower() == "y":
        subprocess.run(["sudo", "pacman", "-S", "--needed", g["pkg"]])

def menu(games):
    selection = games
    while True:
        print("\nARCH GAMES [{}] — {} compatible of {} catalog entries".format(ARCH, len(games), len(CATALOG)))
        for i, g in enumerate(selection, 1):
            print("{:3}  {} {}{} [{}]".format(i, g["art"], g["title"], " ✓" if g["installed"] else "", g["genre"]))
        print("\nType a number, /query to search, a for all, w for visual gallery, q to quit")
        choice = input("> ").strip()
        if choice.lower() == "q":
            return
        if choice.lower() == "a":
            selection = games
        elif choice.lower() == "w":
            gallery(games)
            return
        elif choice.startswith("/"):
            term = choice[1:].lower()
            selection = [g for g in games if term in " ".join([g["title"], g["genre"], g["desc"]]).lower()]
        elif choice.isdigit() and 1 <= int(choice) <= len(selection):
            install(selection[int(choice) - 1])

def gallery(games):
    colors = ["#80b8fb", "#a7df7e", "#bca2fa", "#f4bb70", "#67daca", "#f58fb2"]
    cards = []
    for i, g in enumerate(games):
        esc = lambda v: html.escape(str(v), quote=True)
        cmd = "arch86-games install " + g["pkg"]
        search = (g["title"] + " " + g["genre"] + " " + g["desc"]).lower()
        label = "Installed" if g["installed"] else "Available"
        cards.append('<article data-search="{}" data-category="{}"><div class="cover" style="--accent:{}"><span class="glyph">{}</span><strong>{}</strong></div><div class="body"><small>{} · {}</small><h2>{}</h2><p>{}</p><code>{}</code><button data-command="{}">Copy install command</button></div></article>'.format(
            esc(search), esc(g["genre"]), colors[i % len(colors)], esc(g["art"]), esc(g["title"]),
            esc(g["genre"]), label, esc(g["title"]), esc(g["desc"]), esc(g["pkg"]), esc(cmd)))
    opts = "".join('<option value="{}">{}</option>'.format(html.escape(x, quote=True), html.escape(x)) for x in sorted(set(g["genre"] for g in games)))
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Arch Games</title><style>
*{box-sizing:border-box}body{margin:0;background:#0c1322;color:#f2f5fd;font:16px system-ui,sans-serif}
header{padding:2.4rem 5vw;background:#16243a}h1{margin:0}header p,footer{color:#b6c4da}
main{padding:1.5rem 5vw}input,select{background:#1a2a42;color:white;border:1px solid #465671;border-radius:9px;font:inherit;padding:12px;margin:0 8px 16px 0}input{width:min(60vw,500px)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:18px}
article{background:#19263a;border:1px solid #34465f;border-radius:15px;overflow:hidden}
.cover{height:155px;padding:15px;background:radial-gradient(circle at 80% 25%,var(--accent),transparent 42%),linear-gradient(135deg,#28496b,#0d1828);display:flex;flex-direction:column;justify-content:space-between}
.glyph{font-size:48px}.cover strong{font-size:22px;text-shadow:0 2px 4px #111}
.body{padding:15px;display:flex;flex-direction:column;min-height:207px}.body small{color:#8dc7ff}.body h2{font-size:18px;margin:7px 0}.body p{font-size:14px;line-height:1.5;color:#c4d0e1;flex:1}.body code{font-size:12px;color:#89e2d5}
button{margin-top:12px;padding:11px;border:0;border-radius:8px;background:#3262b9;color:#fff;cursor:pointer}article[hidden]{display:none}footer{padding:22px 5vw}
</style><header><h1>🎮 Arch Games</h1><p>__ARCH__ · __COUNT__ locally compatible games · __TOTAL__ catalog entries</p></header><main>
<input id="query" placeholder="Search games and descriptions"><select id="genre"><option value="">All genres</option>__OPTIONS__</select><p id="count"></p><div class="grid">__CARDS__</div></main>
<footer>Availability comes from this VM's configured Pacman repositories. Thumbnails are original category illustrations, not game screenshots. Some games require additional purchased game data or hardware acceleration. Install commands run only in your terminal, not from the browser.</footer>
<script>
const q=document.querySelector("#query"),f=document.querySelector("#genre"),cards=[...document.querySelectorAll("article")];
function filter(){let n=0;for(const c of cards){let ok=c.dataset.search.includes(q.value.toLowerCase())&&(!f.value||c.dataset.category===f.value);c.hidden=!ok;n+=ok}document.querySelector("#count").textContent=n+" matches"}
q.oninput=filter;f.onchange=filter;filter();
document.querySelectorAll("button").forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.dataset.command);b.textContent="Copied!"}catch(e){prompt("Copy command:",b.dataset.command)}});
</script></html>"""
    page = page.replace("__ARCH__", html.escape(ARCH)).replace("__COUNT__", str(len(games))).replace("__TOTAL__", str(len(CATALOG))).replace("__OPTIONS__", opts).replace("__CARDS__", "".join(cards)).encode("utf-8")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Local gallery: http://127.0.0.1:8765/ — Ctrl+C to stop.", flush=True)
    server.serve_forever()

def main():
    if not SUPPORTED:
        sys.exit("Unsupported architecture: " + ARCH)
    games = visible()
    action = sys.argv[1] if len(sys.argv) > 1 else "menu"
    if action == "menu":
        menu(games)
    elif action == "web":
        gallery(games)
    elif action == "list":
        for g in games:
            print("{}\t{}\t{}".format(g["pkg"], g["title"], "installed" if g["installed"] else "available"))
    elif action == "info":
        print("System: {}, visible: {}, catalog: {}".format(ARCH, len(games), len(CATALOG)))
    elif action == "install" and len(sys.argv) > 2:
        game = next((g for g in games if g["pkg"] == sys.argv[2]), None)
        if game:
            install(game)
        else:
            print("Game not in this VM's repositories or installed packages: " + sys.argv[2])
    else:
        print("Usage: arch86-games [menu|web|list|info|install PACKAGE]")
if __name__ == "__main__":
    main()
