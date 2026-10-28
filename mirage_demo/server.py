"""Local web server for the interactive demo.

Stdlib only (http.server), so the only third-party dependency the whole project
needs is `requests`. Serves the demo page and two endpoints:

  GET  /api/models    -> the models from models.yaml and whether each is usable
  POST /api/generate  -> {model, messages} -> {text}; calls the chosen backend

The deception mechanism runs in the page (the same JavaScript port used by the
published artifact); only text generation is delegated here, so the page works
against any local or hosted model you configure. Run:

    python -m mirage_demo.server --models models.yaml --port 8000
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .models import BackendError, StubBackend, load_models

HERE = Path(__file__).resolve().parent
WEBUI = HERE / "webui" / "index.html"


class State:
    registry = None
    stub = StubBackend()


def _to_backend_messages(messages):
    """The page sends sample-style turns (user/assistant). Promote a leading
    instruction turn to a system message so real models see it as a system
    prompt; a single-turn request (the attacker's self-assessment) stays user."""
    msgs = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]
    if len(msgs) > 1 and msgs[0]["role"] == "user":
        msgs[0]["role"] = "system"
    return msgs


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter console
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if WEBUI.exists():
                self._send(200, WEBUI.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(500, {"error": "web UI not built; run build_webui.py"})
            return
        if self.path == "/api/models":
            self._send(200, {"models": State.registry.describe() if State.registry else []})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/generate":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad JSON"})
            return
        model = req.get("model", "stub")
        api_key = req.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            self._send(400, {"error": "api_key must be a string"})
            return
        messages = _to_backend_messages(req.get("messages", []))
        if not messages:
            self._send(400, {"error": "no messages"})
            return
        try:
            backend = State.stub if model in ("stub", "offline") else State.registry.get(model)
        except KeyError:
            self._send(404, {"error": f"unknown model: {model}"})
            return
        try:
            text = backend.generate(messages, api_key=api_key or None)
            self._send(200, {"text": text, "model": model})
        except BackendError as e:
            # The page falls back to the offline stub; say why here, or it is lost.
            print(f"\n!!! model call failed ({model}): {e}", flush=True)
            self._send(502, {"error": str(e), "model": model})
        except Exception as e:  # e.g. a provider reply that is not JSON
            print(f"\n!!! model call failed ({model}): {type(e).__name__}: {e}", flush=True)
            self._send(502, {"error": f"{type(e).__name__}: {e}", "model": model})


def main():
    ap = argparse.ArgumentParser(description="MIRAGE local demo server")
    ap.add_argument("--models", default=str(HERE.parent / "models.yaml"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    State.registry = load_models(args.models)
    print(f"Models from {args.models}:")
    for m in State.registry.describe():
        flag = "ok" if m["available"] else f"needs: {m['reason']}"
        print(f"  - {m['name']:<22} {m['kind']:<10} {m['model']:<28} [{flag}]")
    print(f"  - {'(offline stub)':<22} {'stub':<10} {'-':<28} [ok]")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\nDemo running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
