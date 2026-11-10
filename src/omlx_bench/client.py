"""oMLX admin API client (verified against oMLX source, commit 2165e75).

All admin routes take `Authorization: Bearer <MAIN api key>`. Three benchmark
families, each with its own start / stream / results / cancel:

  performance   POST /admin/api/bench/start            -> {bench_id}
                GET  /admin/api/bench/{id}/stream       SSE, ends on upload_done | upload_skipped | error
  intelligence  POST /admin/api/bench/accuracy/queue/add -> queue status (current_bench_id)
                GET  /admin/api/bench/accuracy/{id}/stream  SSE, ends on done | error
  context       POST /admin/api/bench/context/start    -> {bench_id}
                GET  /admin/api/bench/context/{id}/stream   SSE, ends on done | error

Events are replayed from the start on every subscribe, so re-attaching after a
dropped connection never loses anything.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Iterator

# The server sends a keepalive at least every 60 s; three missed ones = dead.
STREAM_READ_TIMEOUT = 180
REQUEST_TIMEOUT = 60

TERMINAL = {
    "performance": {"upload_done", "upload_skipped", "error"},
    "intelligence": {"done", "error"},
    "context": {"done", "error"},
}


class ApiError(Exception):
    def __init__(self, status: int, detail: str, path: str = ""):
        super().__init__(f"HTTP {status} on {path}: {detail}")
        self.status, self.detail, self.path = status, detail, path


class StreamLost(Exception):
    """The SSE connection dropped or went silent; the run may still be going."""


class Client:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    # -- plumbing ---------------------------------------------------------
    def _open(self, method: str, path: str, body: Any = None, timeout: float = REQUEST_TIMEOUT):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except (ValueError, AttributeError):
                detail = raw
            if e.code == 401:
                detail = f"{detail} (is OMLX_API_KEY the MAIN key? sub-keys can't use admin routes)"
            raise ApiError(e.code, str(detail), path) from None
        except (urllib.error.URLError, ConnectionError, socket.timeout) as e:
            raise ApiError(0, f"cannot reach oMLX at {self.url}: {e}", path) from None

    def request(self, method: str, path: str, body: Any = None) -> Any:
        with self._open(method, path, body) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def stream(self, path: str, terminal: set[str]) -> Iterator[dict]:
        """Yield SSE `data:` events as dicts until a terminal event type."""
        resp = self._open("GET", path, timeout=STREAM_READ_TIMEOUT)
        try:
            with resp:
                for raw in resp:
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if not line.startswith("data:"):
                        continue  # keepalive comments and blank separators
                    ev = json.loads(line[5:].strip())
                    yield ev
                    if ev.get("type") in terminal:
                        return
        except (socket.timeout, ConnectionError, urllib.error.URLError, OSError) as e:
            raise StreamLost(str(e)) from None
        raise StreamLost("stream closed before a terminal event")

    # -- models & settings -------------------------------------------------
    def models(self) -> list[dict]:
        return self.request("GET", "/admin/api/models")["models"]

    def model(self, model_id: str) -> dict:
        for m in self.models():
            if m.get("id") == model_id:
                return m
        raise ApiError(404, f"Model not found: {model_id}", "/admin/api/models")

    def model_settings(self, model_id: str) -> dict:
        return dict(self.model(model_id).get("settings") or {})

    def put_model_settings(self, model_id: str, changes: dict) -> Any:
        return self.request("PUT", f"/admin/api/models/{model_id}/settings", changes)

    def global_settings(self) -> dict:
        return self.request("GET", "/admin/api/global-settings")

    def post_global_settings(self, changes: dict) -> Any:
        return self.request("POST", "/admin/api/global-settings", changes)

    # -- what is running ---------------------------------------------------
    def active(self) -> dict[str, dict]:
        """Running benchmark per family, e.g. {'performance': {...}} ({} when idle)."""
        out = {}
        p = self.request("GET", "/admin/api/bench/active")
        if p.get("running"):
            out["performance"] = p
        c = self.request("GET", "/admin/api/bench/context/active")
        if c.get("running"):
            out["context"] = c
        a = self.accuracy_status()
        if a.get("running"):
            out["intelligence"] = {"bench_id": a.get("current_bench_id"), "model_id": a.get("current_model")}
        return out

    # -- performance -------------------------------------------------------
    def perf_start(self, body: dict) -> str:
        return self.request("POST", "/admin/api/bench/start", body)["bench_id"]

    def perf_stream(self, bench_id: str) -> Iterator[dict]:
        return self.stream(f"/admin/api/bench/{bench_id}/stream", TERMINAL["performance"])

    def perf_results(self, bench_id: str) -> dict:
        return self.request("GET", f"/admin/api/bench/{bench_id}/results")

    def perf_cancel(self, bench_id: str) -> Any:
        return self.request("POST", f"/admin/api/bench/{bench_id}/cancel")

    # -- intelligence (accuracy) ------------------------------------------
    def accuracy_status(self) -> dict:
        return self.request("GET", "/admin/api/bench/accuracy/queue/status")

    def accuracy_add(self, body: dict) -> str:
        status = self.request("POST", "/admin/api/bench/accuracy/queue/add", body)
        bench_id = status.get("current_bench_id")
        if not bench_id or status.get("current_model") != body["model_id"]:
            raise ApiError(409, f"accuracy queue did not start our run (status: {status})",
                           "/admin/api/bench/accuracy/queue/add")
        return bench_id

    def accuracy_stream(self, bench_id: str) -> Iterator[dict]:
        return self.stream(f"/admin/api/bench/accuracy/{bench_id}/stream", TERMINAL["intelligence"])

    def accuracy_cancel(self) -> Any:
        return self.request("POST", "/admin/api/bench/accuracy/cancel")

    # -- context -----------------------------------------------------------
    def context_start(self, body: dict) -> str:
        return self.request("POST", "/admin/api/bench/context/start", body)["bench_id"]

    def context_stream(self, bench_id: str) -> Iterator[dict]:
        return self.stream(f"/admin/api/bench/context/{bench_id}/stream", TERMINAL["context"])

    def context_results(self, bench_id: str) -> dict:
        return self.request("GET", f"/admin/api/bench/context/{bench_id}/results")

    def context_cancel(self, bench_id: str) -> Any:
        return self.request("POST", f"/admin/api/bench/context/{bench_id}/cancel")
