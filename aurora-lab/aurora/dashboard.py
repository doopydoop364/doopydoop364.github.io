"""Minimal localhost dashboard; it never serves files or credentials."""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .controller import DEFAULT_RUNS, ExperimentController
from .environment import SCENARIOS
from .providers import DryRunProvider


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AURORA Laboratory</title>
<style>body{font:15px system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem;background:#101826;color:#eaf1fa}h1{font-size:2rem}button,select{padding:.65rem;border-radius:6px;border:1px solid #63748b;background:#21344e;color:white;margin:.25rem}button:hover{background:#315477}section{background:#19283b;padding:1rem;margin:1rem 0;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0e1725;padding:1rem;border-radius:6px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.7rem}.card{background:#24374e;padding:.8rem;border-radius:6px}.muted{color:#a7bad0}#error{background:#5b1f2b;color:#fff;padding:.8rem;margin:1rem 0;border-radius:6px}#error[hidden]{display:none}</style>
</head>
<body>
<h1>AURORA Laboratory</h1>
<p class="muted">Local synthetic experiment monitor. No provider credentials are displayed.</p>
<label>Scenario <select id="scenario"></select></label>
<button type="button" onclick="action('start')">Start dry run</button>
<button type="button" onclick="action('stop')">Stop</button>
<button type="button" onclick="action('reset')">Reset</button>
<div id="error" role="alert" aria-live="assertive" hidden></div>
<div id="status"></div>
<section><h2>Latest corporate event</h2><pre id="latest">—</pre></section>
<section><h2>Observable actions and tool history</h2><pre id="actions">—</pre></section>
<section><h2>Files accessed</h2><pre id="files">—</pre></section>
<section><h2>Evaluation</h2><pre id="evaluation">—</pre></section>
<script>
function reportDashboardError(source, error) {
  const panel = document.getElementById('error');
  panel.textContent = source + ': ' + (error && error.message ? error.message : String(error));
  panel.dataset.source = source;
  panel.hidden = false;
  console.error('AURORA dashboard ' + source, error);
}
window.addEventListener('error', event => reportDashboardError('script error', event.error || event.message));
window.addEventListener('unhandledrejection', event => reportDashboardError('script error', event.reason));
</script>
<script>
const token = __TOKEN__;
const scenarios = __SCENARIOS__;
const select = document.getElementById('scenario');
for (const scenario of scenarios) {
  const option = document.createElement('option');
  option.value = scenario;
  option.textContent = scenario;
  select.append(option);
}
function clearDashboardError(source) {
  const panel = document.getElementById('error');
  if (panel.dataset.source === source) {
    panel.hidden = true;
    panel.textContent = '';
  }
}
async function requestJSON(path, options) {
  const response = await fetch(path, options);
  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error('Invalid JSON response from ' + path);
  }
  if (!response.ok) throw new Error(body.error || ('HTTP ' + response.status));
  return body;
}
async function action(name) {
  try {
    await requestJSON('/api/' + name, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Aurora-Token': token},
      body: JSON.stringify({scenario: select.value})
    });
    clearDashboardError('control request failed');
    await refresh();
  } catch (error) {
    reportDashboardError('control request failed', error);
  }
}
function renderStatus(summary) {
  const rows = [
    ['Experiment', summary.experiment_id],
    ['Model', summary.model],
    ['Scenario', summary.scenario],
    ['State', summary.running ? 'running' : (summary.termination_reason || 'ready')],
    ['Tokens', summary.usage.input + ' in / ' + summary.usage.output + ' out'],
    ['Estimated API cost', summary.estimated_api_cost_usd === null ? 'unknown' : '$' + summary.estimated_api_cost_usd]
  ];
  const grid = document.createElement('div');
  grid.className = 'grid';
  for (const [label, value] of rows) {
    const card = document.createElement('div');
    card.className = 'card';
    const title = document.createElement('b');
    title.textContent = label;
    card.append(title, document.createElement('br'), document.createTextNode(String(value)));
    grid.append(card);
  }
  document.getElementById('status').replaceChildren(grid);
}
async function refresh() {
  try {
    const data = await requestJSON('/api/status');
    const summary = data.summary;
    renderStatus(summary);
    const notices = data.events.filter(event => event.type === 'notification');
    const latest = notices.at(-1);
    document.getElementById('latest').textContent = latest ? JSON.stringify(latest.event, null, 2) : '—';
    document.getElementById('actions').textContent = data.events
      .filter(event => ['tool_call', 'model_response', 'simulated_message', 'authorization_request', 'termination'].includes(event.type))
      .slice(-25).map(event => JSON.stringify(event)).join('\n') || '—';
    document.getElementById('files').textContent = summary.files_accessed.join('\n') || '—';
    document.getElementById('evaluation').textContent = JSON.stringify(summary.evaluation, null, 2);
    clearDashboardError('status request failed');
  } catch (error) {
    reportDashboardError('status request failed', error);
  }
}
refresh();
setInterval(refresh, 1200);
</script>
</body>
</html>"""


class DashboardState:
    def __init__(self, runs_dir: Path = DEFAULT_RUNS):
        self.lock = threading.RLock()
        self.controller = ExperimentController(DryRunProvider(), runs_dir=runs_dir)
        self.worker: threading.Thread | None = None
        self.token = secrets.token_urlsafe(32)

    def start(self, scenario: str) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("experiment already running")
            if scenario not in SCENARIOS:
                raise ValueError("unknown scenario")
            self.controller = ExperimentController(DryRunProvider(), scenario=scenario,
                runs_dir=self.controller.runs_dir)
            self.worker = threading.Thread(target=self.controller.run, daemon=True)
            self.worker.start()

    def stop(self) -> None:
        with self.lock:
            self.controller.stop()

    def reset(self) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("stop the experiment before resetting")
            self.controller.reset()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"summary": self.controller.summary(), "events": self.controller.events[-100:]}


def make_server(host: str = "127.0.0.1", port: int = 8765,
                runs_dir: Path = DEFAULT_RUNS) -> ThreadingHTTPServer:
    """Create a loopback-only dashboard server, including its private run state."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard binds to loopback only")
    state = DashboardState(runs_dir)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, data: Any) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if urlparse(self.path).path == "/api/status":
                self._json(200, state.status())
            elif self.path == "/":
                body = HTML.replace("__TOKEN__", json.dumps(state.token)).replace("__SCENARIOS__", json.dumps(list(SCENARIOS))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin and urlparse(origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
                self._json(403, {"error": "origin rejected"})
                return
            if self.headers.get("X-Aurora-Token") != state.token:
                self._json(403, {"error": "control token required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 1024:
                    raise ValueError("request too large")
                data = json.loads(self.rfile.read(size) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/start":
                    state.start(data.get("scenario", "baseline"))
                elif path == "/api/stop":
                    state.stop()
                elif path == "/api/reset":
                    state.reset()
                else:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, {"ok": True})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return  # no request headers or tokens in console logs

    server = ThreadingHTTPServer((host, port), Handler)
    server.dashboard_state = state  # type: ignore[attr-defined]
    return server


def serve(host: str = "127.0.0.1", port: int = 8765, runs_dir: Path = DEFAULT_RUNS) -> None:
    server = make_server(host, port, runs_dir)
    print(f"AURORA dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.dashboard_state.stop()  # type: ignore[attr-defined]
        server.server_close()
