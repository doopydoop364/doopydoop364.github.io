"""Local dashboard; credentials and controller state remain server-side."""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import RESOURCE_PRESETS, RunSettings
from .controller import DEFAULT_RUNS, ExperimentController
from .environment import SCENARIOS
from .providers import AnthropicProvider, DryRunProvider, OpenAIProvider, ProviderError

HTML = Path(__file__).with_name("dashboard_page.html").read_text(encoding="utf-8")
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def operator_rates() -> tuple[float, float] | None:
    try:
        rates = (float(os.environ["AURORA_INPUT_USD_PER_MILLION"]),
                 float(os.environ["AURORA_OUTPUT_USD_PER_MILLION"]))
    except (KeyError, ValueError):
        return None
    return rates if all(math.isfinite(x) and x >= 0 for x in rates) else None


class DashboardState:
    def __init__(self, runs_dir: Path = DEFAULT_RUNS):
        self.lock = threading.RLock()
        self.runs_dir = runs_dir
        self.controller = ExperimentController(DryRunProvider(), runs_dir=runs_dir)
        self.worker: threading.Thread | None = None
        self.token = secrets.token_urlsafe(32)
        self.model_cache: dict[str, tuple[float, list[str]]] = {}
        self.connection = {"openai": "not checked", "anthropic": "not checked"}
        self.pending_confirmation: tuple[str, dict[str, Any], float] | None = None

    def public_config(self) -> dict[str, Any]:
        return {"scenarios": list(SCENARIOS), "defaults": RunSettings().as_dict(),
                "resource_presets": RESOURCE_PRESETS,
                "providers": {name: {"credentials_configured": bool(os.getenv(key)),
                                     "connection_status": self.connection[name]}
                              for name, key in (("openai", "OPENAI_API_KEY"),
                                                ("anthropic", "ANTHROPIC_API_KEY"))},
                "pricing_configured": operator_rates() is not None}

    def models(self, name: str, *, force: bool = False) -> list[str]:
        if name == "dry-run":
            return ["dry-run-script-v1"]
        cls = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}.get(name)
        if cls is None:
            raise ValueError("unknown provider")
        with self.lock:
            cached = self.model_cache.get(name)
            if cached and not force and time.monotonic() - cached[0] < 300:
                return cached[1]
        try:
            models = cls("discovery", timeout=15, retries=1).models()
        except ProviderError:
            with self.lock:
                self.connection[name] = "error"
            raise
        with self.lock:
            self.model_cache[name] = (time.monotonic(), models)
            self.connection[name] = "connected"
        return models

    def _check_model(self, settings: RunSettings) -> tuple[float, float]:
        cached = self.model_cache.get(settings.provider)
        if not cached or time.monotonic() - cached[0] >= 300:
            raise ValueError("refresh the provider model list before starting")
        if settings.model not in cached[1]:
            raise ValueError("model identifier was not returned by provider discovery")
        key = "OPENAI_API_KEY" if settings.provider == "openai" else "ANTHROPIC_API_KEY"
        if not os.getenv(key):
            raise ValueError("provider API credentials are not configured")
        rates = operator_rates()
        if rates is None:
            raise ValueError("operator token prices are required for a paid run")
        return rates

    def prepare(self, raw: dict[str, Any]) -> dict[str, Any]:
        settings = RunSettings.parse(raw)
        if settings.provider == "dry-run":
            raise ValueError("dry runs do not require paid confirmation")
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("experiment already running")
            rates = self._check_model(settings)
            nonce = secrets.token_urlsafe(24)
            self.pending_confirmation = (nonce, settings.as_dict(), time.monotonic() + 300)
            return {"confirmation_token": nonce, "provider": settings.provider,
                    "model": settings.model, "scenario": settings.scenario,
                    "seed": settings.seed, "notebook_visibility": settings.notebook_visibility,
                    "resource_preset": settings.resource_preset,
                    "cost_budget_usd": settings.cost_budget_usd,
                    "combined_token_budget": settings.combined_token_budget,
                    "max_steps": settings.max_steps, "wall_seconds": settings.wall_seconds,
                    "request_timeout": settings.request_timeout,
                    "pricing_usd_per_million": {"input": rates[0], "output": rates[1]}}

    def start(self, scenario: str | dict[str, Any], confirmation_token: str | None = None) -> None:
        raw = {"scenario": scenario} if isinstance(scenario, str) else scenario
        settings = RunSettings.parse(raw)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("experiment already running")
            rates = None
            if settings.provider != "dry-run":
                rates = self._check_model(settings)
                pending = self.pending_confirmation
                if (not pending or not secrets.compare_digest(str(confirmation_token), pending[0]) or
                    pending[1] != settings.as_dict() or time.monotonic() > pending[2]):
                    raise ValueError("explicit paid-run confirmation is required")
                self.pending_confirmation = None
                cls = OpenAIProvider if settings.provider == "openai" else AnthropicProvider
                provider = cls(settings.model, max_output_tokens=settings.max_output_tokens,
                               temperature=settings.temperature, timeout=settings.request_timeout,
                               retries=settings.retries)
            else:
                provider = DryRunProvider()
            self.controller = ExperimentController(
                provider, scenario=settings.scenario, runs_dir=self.runs_dir,
                max_steps=settings.max_steps, max_tool_calls=settings.max_tool_calls,
                token_budget=settings.combined_token_budget,
                input_token_budget=settings.input_token_budget,
                output_token_budget=settings.output_token_budget,
                cost_budget_usd=settings.cost_budget_usd, wall_seconds=settings.wall_seconds,
                anomaly_step=settings.anomaly_step, decision_step=settings.decision_step,
                simulation_start=settings.simulation_start,
                seed=settings.seed,
                notebook_visibility=settings.notebook_visibility,
                resource_preset=settings.resource_preset,
                input_rate=rates[0] if rates else None, output_rate=rates[1] if rates else None)
            controller = self.controller
            def run_worker() -> None:
                try:
                    controller.run()
                except Exception as exc:
                    controller.running = False
                    controller.termination_reason = "controller_error"
                    controller.run_dir = None
                    controller._log("error", detail=f"run could not be completed: {type(exc).__name__}")
            self.worker = threading.Thread(target=run_worker, daemon=True)
            self.worker.start()

    def stop(self) -> None:
        with self.lock:
            self.controller.stop()

    def reset(self) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("stop the experiment before resetting")
            self.controller.reset()
            self.worker = None
            self.pending_confirmation = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"summary": self.controller.summary(), "events": list(self.controller.events[-200:]),
                    "result_saved": bool(self.controller.run_dir and
                        (self.controller.run_dir / "summary.json").is_file()),
                    "connection": dict(self.connection),
                    "credentials_configured": {"openai": bool(os.getenv("OPENAI_API_KEY")),
                                               "anthropic": bool(os.getenv("ANTHROPIC_API_KEY"))},
                    "will_incur_api_charges": not isinstance(self.controller.provider, DryRunProvider),
                    "pricing_configured": operator_rates() is not None}

    def _run_directory(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid experiment identifier")
        directory = self.runs_dir / run_id
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("experiment not found")
        return directory

    def saved(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        rows = []
        for directory in self.runs_dir.iterdir():
            if directory.is_symlink() or not RUN_ID.fullmatch(directory.name):
                continue
            path = directory / "summary.json"
            if path.is_symlink() or not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("experiment_id") != directory.name:
                    continue
                rows.append((path.stat().st_mtime, {key: data.get(key) for key in
                             ("experiment_id", "provider", "model", "scenario", "termination_reason",
                              "outcome", "configuration", "usage", "estimated_api_cost_usd")}))
            except (OSError, ValueError):
                continue
        return [row for _, row in sorted(rows, key=lambda item: item[0], reverse=True)]

    def result(self, run_id: str) -> dict[str, Any]:
        path = self._run_directory(run_id) / "summary.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("result unavailable")
        return json.loads(path.read_text(encoding="utf-8"))

    def log_bytes(self, run_id: str) -> bytes:
        path = self._run_directory(run_id) / "events.jsonl"
        if path.is_symlink() or not path.is_file():
            raise ValueError("log unavailable")
        return path.read_bytes()

    def compare(self, ids: list[str]) -> list[dict[str, Any]]:
        if not 2 <= len(ids) <= 5 or len(set(ids)) != len(ids):
            raise ValueError("select two to five distinct experiments")
        return [{key: data.get(key) for key in
                 ("experiment_id", "provider", "model", "scenario", "termination_reason", "outcome",
                  "usage", "estimated_api_cost_usd", "evaluation", "configuration")}
                for data in (self.result(run_id) for run_id in ids)]


def make_server(host: str = "127.0.0.1", port: int = 8765,
                runs_dir: Path = DEFAULT_RUNS) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard binds to loopback only")
    state = DashboardState(runs_dir)

    class Handler(BaseHTTPRequestHandler):
        def _body(self, code: int, body: bytes, content_type: str, *, download: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: Any) -> None:
            self._body(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            path = url.path
            try:
                if path == "/":
                    body = HTML.replace("__TOKEN__", json.dumps(state.token)).replace(
                        "__SCENARIOS__", json.dumps(list(SCENARIOS))).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/status":
                    self._json(200, state.status())
                elif path == "/api/config":
                    self._json(200, state.public_config())
                elif path == "/api/models":
                    name = parse_qs(url.query).get("provider", ["dry-run"])[0]
                    force = parse_qs(url.query).get("refresh", ["0"])[0] == "1"
                    self._json(200, {"provider": name, "models": state.models(name, force=force)})
                elif path == "/api/runs":
                    self._json(200, {"runs": state.saved()})
                elif path.startswith("/api/runs/"):
                    parts = path.split("/")
                    if len(parts) == 4:
                        self._json(200, state.result(parts[3]))
                    elif len(parts) == 5 and parts[4] == "events":
                        self._body(200, state.log_bytes(parts[3]), "application/x-ndjson; charset=utf-8",
                                   download=f"aurora-{parts[3]}.jsonl")
                    else:
                        self._json(404, {"error": "not found"})
                elif path == "/api/compare":
                    self._json(200, {"results": state.compare(parse_qs(url.query).get("id", []))})
                else:
                    self._json(404, {"error": "not found"})
            except (ValueError, ProviderError, OSError) as exc:
                self._json(400, {"error": str(exc)})

        def do_POST(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin and urlparse(origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
                self._json(403, {"error": "origin rejected"})
                return
            if not secrets.compare_digest(self.headers.get("X-Aurora-Token", ""), state.token):
                self._json(403, {"error": "control token required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 8192:
                    raise ValueError("request size limit exceeded")
                data = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("request must be an object")
                path = urlparse(self.path).path
                if path == "/api/prepare":
                    self._json(200, state.prepare(data.get("settings", data)))
                    return
                if path == "/api/start":
                    state.start(data.get("settings", {"scenario": data.get("scenario", "baseline")}),
                                data.get("confirmation_token"))
                elif path == "/api/stop":
                    state.stop()
                elif path == "/api/reset":
                    state.reset()
                else:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, {"ok": True})
            except (ValueError, ProviderError, OSError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return  # Never log headers or tokens.

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
