"""Read-only local gallery. Browser requests cannot install, download or launch."""
import base64
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import quote, unquote, urlsplit

from ..config import AppError
from ..gamevault.client import Client, entity_id
from ..storage.database import Database

STYLE = """
*{box-sizing:border-box}body{margin:0;background:#101622;color:#e9edf7;font:16px system-ui,sans-serif}
header,main,footer{padding:24px max(5vw,16px)}header{background:#19263a;border-bottom:1px solid #364258}
h1{margin:8px 0}p{line-height:1.55;color:#b9c7db}input,select,button{font:inherit;padding:12px;background:#24334a;color:#eef3ff;border:1px solid #465976;border-radius:8px}
.controls{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}input{flex:1;min-width:180px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:20px}
article{background:#1a2536;border:1px solid #364258;border-radius:14px;overflow:hidden}article[hidden]{display:none}.cover{height:180px;background:radial-gradient(circle at 85% 10%,#5875a6,transparent 60%),linear-gradient(145deg,#243f50,#192036);padding:20px;font-size:48px;display:flex;align-items:center;justify-content:center}.cover img{width:100%;height:100%;object-fit:contain}.body{padding:20px}.body h2{font-size:20px}.badge{display:inline-block;padding:4px 8px;background:#30445e;border-radius:6px;font-size:12px}a{color:#96d3ff}small{color:#b5c2d8}code,pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.body p{min-height:66px;font-size:14px}button{cursor:pointer}footer{color:#b9c7db;font-size:13px}.detail{max-width:1000px}label{display:flex;gap:6px;align-items:center}
"""
SCRIPT = """
const query=document.querySelector('#query'),category=document.querySelector('#category'),status=document.querySelector('#status'),architecture=document.querySelector('#architecture'),cards=[...document.querySelectorAll('article')];
function filter(){let count=0;for(const card of cards){let ok=card.dataset.search.includes(query.value.toLowerCase())&&(!category.value||card.dataset.category===category.value)&&(!status.value||card.dataset.status===status.value)&&(!architecture.value||card.dataset.arch===architecture.value);card.hidden=!ok;count+=ok;}document.querySelector('#count').textContent=count+' games';}
if(query){query.addEventListener('input',filter);category.addEventListener('change',filter);status.addEventListener('change',filter);architecture.addEventListener('change',filter);filter();}
document.querySelectorAll('[data-command]').forEach(button=>button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(button.dataset.command);button.textContent='Copied';}catch(e){window.prompt('Copy command',button.dataset.command);}}));
async function refresh(){try{const r=await fetch('/api/status');const state=await r.json();document.querySelectorAll('[data-game]').forEach(el=>{let value=state.downloads.find(d=>d.game_id===el.dataset.game);if(value)el.textContent='Download: '+value.state+' ('+value.received+' bytes)';});}catch(e){}}
if(query){refresh();setInterval(refresh,5000);}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def shell_command(game):
    import shlex
    return "arch-games " + ("install " if game.source == "pacman" else "download add ") + shlex.quote(game.package or game.id)


def document(body, title="Arch Games 2.0"):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + esc(title) + '</title><style>' + STYLE + '</style></head><body>' + body +
            '<footer>Local, read-only catalog. Repository availability is not gameplay verification. '
            'Fallback thumbnails are category illustrations. Run copied commands explicitly in your terminal.</footer><script>' +
            SCRIPT + '</script></body></html>').encode("utf-8")


def render(library, games, explanation):
    cards = []
    for game in games:
        report = library.reports.get(game.id, {})
        cover = game.metadata.get("remote", {}).get("metadata") or {}
        media = (cover.get("cover") or {}).get("id") if isinstance(cover, dict) else None
        artwork = '<img loading="lazy" src="/art/{}" alt="{} cover">'.format(esc(media), esc(game.title)) if str(media).isdecimal() else esc(game.art)
        status = report.get("status", "Unknown")
        cards.append('<article data-search="{}" data-category="{}" data-status="{}" data-arch="{}"><div class="cover">{}</div><div class="body"><small>{} / {}</small><h2><a href="/game/{}">{}</a></h2><span class="badge">{}</span><p>{}</p><small>{} / {}</small><p data-game="{}">Download: not queued</p><button data-command="{}">Copy {} command</button></div></article>'.format(
            esc((game.title + " " + game.description).lower()), esc(game.category), esc(status), esc(game.architecture), artwork,
            esc(game.source), esc(game.category), quote(game.id, safe=""), esc(game.title), esc(status), esc(game.description),
            esc(game.architecture), "Installed" if game.installed else "Not installed", esc(game.id), esc(shell_command(game)),
            "install" if game.source == "pacman" else "download"))
    def options(values):
        return ''.join('<option value="{}">{}</option>'.format(esc(v), esc(v)) for v in sorted(set(values)))
    body = '<header><small>YOUR LOCAL GAME LIBRARY</small><h1>Arch Games 2.0</h1><p>{} / {} games</p><p>{}</p><p>{}</p></header>'.format(
        esc(library.profile.get("os_architecture", "unknown")), len(games), esc(explanation), esc(json.dumps(library.sync)))
    body += '<main><div class="controls"><input id="query" aria-label="Search games" placeholder="Search your library"><select id="category" aria-label="Category"><option value="">All categories</option>{}</select><select id="status" aria-label="Compatibility"><option value="">All statuses</option>{}</select><select id="architecture" aria-label="Architecture"><option value="">All architectures</option>{}</select></div><p id="count"></p><div class="grid">{}</div></main>'.format(
        options(g.category for g in games), options(library.reports.get(g.id, {}).get("status", "Unknown") for g in games),
        options(g.architecture for g in games), ''.join(cards))
    return document(body)


def create_server(library, games, explanation, config, port=8765):
    page = render(library, games, explanation)
    by_id = {g.id: g for g in games}
    media_ids = set()
    for game in games:
        meta = game.metadata.get("remote", {}).get("metadata") or {}
        if isinstance(meta, dict) and isinstance(meta.get("cover"), dict) and str(meta["cover"].get("id")).isdecimal():
            media_ids.add(str(meta["cover"]["id"]))
    art_lock = threading.Lock()
    art_dir = config.cache_dir / "artwork"
    art_dir.mkdir(mode=0o700, exist_ok=True)
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_data(self, data, kind="text/html; charset=utf-8", code=200):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'sha256-" + script_hash + "'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            allowed = {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}
            if self.headers.get("Host") not in allowed:
                return self.send_data(b"Forbidden host", "text/plain", 403)
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in allowed}:
                return self.send_data(b"Forbidden origin", "text/plain", 403)
            path = urlsplit(self.path).path
            if path in {"/", "/index.html"}:
                return self.send_data(page)
            if path == "/api/status":
                db = Database(config.data_dir / "state.db")
                try:
                    downloads = [{k: r["value"].get(k) for k in ["game_id", "state", "received"]} for r in db.all("downloads")]
                finally:
                    db.close()
                return self.send_data(json.dumps({"downloads": downloads}).encode(), "application/json")
            if path.startswith("/game/"):
                ident = unquote(path[6:])
                if ident not in by_id:
                    return self.send_data(b"Not found", "text/plain", 404)
                game = by_id[ident]
                body = '<main class="detail"><a href="/">Back to catalog</a><h1>{}</h1><p>{}</p><pre>{}</pre><code>{}</code><p>{}</p></main>'.format(
                    esc(game.title), esc(game.description), esc(json.dumps(library.reports[ident], indent=2)),
                    esc(shell_command(game)), esc(game.metadata.get("analysis_source", "Repository metadata")))
                return self.send_data(document(body, game.title))
            if path.startswith("/art/"):
                ident = path[5:]
                if ident not in media_ids:
                    return self.send_data(b"Not found", "text/plain", 404)
                try:
                    with art_lock:
                        # Scope cached artwork to the configured server.
                        scope = hashlib.sha256(str(config.values.get("server_url")).encode()).hexdigest()[:16]
                        cached = art_dir / (scope + "-" + entity_id(ident))
                        meta_path = cached.with_suffix(".type")
                        if cached.is_file() and meta_path.is_file():
                            data, kind = cached.read_bytes(), meta_path.read_text()
                        else:
                            data, kind = Client(config, None).artwork(ident)
                            cached.write_bytes(data)
                            meta_path.write_text(kind)
                    return self.send_data(data, kind)
                except (AppError, OSError):
                    return self.send_data(b"Artwork unavailable", "text/plain", 404)
            return self.send_data(b"Not found", "text/plain", 404)

        def do_POST(self):
            self.send_data(b"Read-only gallery: use the terminal for actions", "text/plain", 405)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(library, games, explanation, config, port=8765):
    if not 0 <= port <= 65535:
        raise AppError("Port must be between 0 and 65535")
    server = create_server(library, games, explanation, config, port)
    print("Local gallery: http://127.0.0.1:{}/ (Ctrl+C to stop)".format(server.server_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
