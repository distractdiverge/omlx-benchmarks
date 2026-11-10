"""A fake oMLX admin server for tests: real routes, scripted event sequences."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "main-key"


class FakeOmlx:
    def __init__(self):
        self.models = {"m1": {"max_context_window": None, "temperature": None},
                       "m2": {"max_context_window": 65536, "temperature": 0.7}}
        self.globals = {"memory": {"prefill_memory_guard": True, "memory_guard_tier": "balanced"},
                        "scheduler": {"chunked_prefill": True}}
        self.calls: list[tuple[str, str, object]] = []
        self.runs: dict[str, dict] = {}          # bench_id -> {"family","events","body"}
        self.script: list = []                   # per start: list of events, or ("http", code, detail)
        self.running: dict[str, str] = {}        # family -> bench_id (cleared when stream ends)
        self._n = 0
        self.drop_stream_once: set[str] = set()  # bench_ids whose first stream drops mid-way
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # default event scripts per family, used when self.script is empty
    def default_events(self, family, body):
        if family == "performance":
            pp = body["prompt_lengths"][0]
            res = [{"type": "result", "data": {"test_type": "single", "pp": pp, "tg": body.get("generation_length", 128),
                    "gen_tps": 50.0, "processing_tps": 800.0, "ttft_ms": 1200.0, "peak_memory_bytes": 20 * 2**30}}]
            for bs in body.get("batch_sizes", []):
                res.append({"type": "result", "data": {"test_type": "batch", "batch_size": bs, "pp": 1024,
                            "tg": 128, "total_throughput": 120.0 * bs, "gen_tps": 30.0 * bs}})
            return [{"type": "progress", "message": "x"}, *res, {"type": "done", "summary": {}},
                    {"type": "upload_done", "data": {"owner_hash": "abc", "total": 1, "success": 1, "failed": 0,
                     "results": [{"context_length": pp, "id": "r1", "url": "https://omlx.ai/r/r1"}]}}]
        if family == "intelligence":
            name = next(iter(body["benchmarks"]))
            n = body["benchmarks"][name]
            data = {"model_id": body["model_id"], "external": False, "benchmark": name, "accuracy": 0.6,
                    "thinking_used": body.get("enable_thinking", False), "total": n, "correct": int(n * 0.6),
                    "time_s": 12.3, "dataset_total": 9999, "sampling_profile": "deterministic",
                    "question_results": [{"id": "q1", "correct": True, "expected": "A", "predicted": "A",
                                          "question": "?", "raw_response": "A", "category": "c",
                                          "time_s": 0.1, "finish_reason": "stop", "prompt_tokens": 5,
                                          "completion_tokens": 2}],
                    "truncated_count": 0, "truncated_correct_count": 0, "finished_count": n,
                    "finished_accuracy": 0.6, "category_scores": {"c": 0.6}}
            return [{"type": "progress"}, {"type": "result", "data": data},
                    {"type": "upload", "data": {"model_id": body["model_id"], "benchmark": name, "url": "https://omlx.ai/i/1"}},
                    {"type": "done", "summary": {}}]
        res = {"model_id": body["model_id"], "target_tokens": body["target_tokens"], "measured_tokens": 70000,
               "verified_tokens": 69632, "applied_tokens": 69632, "applied": True, "capped_by": "memory", "duration_s": 5}
        self.models[body["model_id"]]["max_context_window"] = 69632  # the real side effect
        return [{"type": "progress"}, {"type": "result", "data": res}, {"type": "done", "summary": {}}]

    def _handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def _auth(self):
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    self._json(401, {"detail": "Unauthorized"})
                    return False
                return True

            def _start(self, family, body):
                if fake.running:
                    fam, bid = next(iter(fake.running.items()))
                    return self._json(409, {"detail": f"A {fam} benchmark is already running (bench_id={bid})."})
                step = fake.script.pop(0) if fake.script else None
                if isinstance(step, tuple) and step[0] == "http":
                    return self._json(step[1], {"detail": step[2]})
                fake._n += 1
                bid = f"b{fake._n}"
                events = step if step is not None else fake.default_events(family, body)
                fake.runs[bid] = {"family": family, "events": events, "body": body}
                fake.running[family] = bid
                if family == "intelligence":
                    return self._json(200, {"running": True, "current_model": body["model_id"],
                                            "current_bench_id": bid, "queue": []})
                return self._json(200, {"bench_id": bid, "status": "started"})

            def _stream(self, bid):
                run = fake.runs.get(bid)
                if not run:
                    return self._json(404, {"detail": "Benchmark not found"})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                drop = bid in fake.drop_stream_once
                fake.drop_stream_once.discard(bid)
                for i, ev in enumerate(run["events"]):
                    if drop and i == 1:
                        return  # connection closes mid-run
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                fake.running.pop(run["family"], None)

            def do_GET(self):
                fake.calls.append(("GET", self.path, None))
                if not self._auth():
                    return
                p = self.path
                if p == "/admin/api/models":
                    return self._json(200, {"models": [{"id": k, "settings": dict(v)} for k, v in fake.models.items()]})
                if p == "/admin/api/global-settings":
                    return self._json(200, fake.globals)
                if p == "/admin/api/bench/active":
                    bid = fake.running.get("performance")
                    return self._json(200, {"running": bool(bid), "bench_id": bid})
                if p == "/admin/api/bench/context/active":
                    bid = fake.running.get("context")
                    return self._json(200, {"running": bool(bid), "bench_id": bid})
                if p == "/admin/api/bench/accuracy/queue/status":
                    bid = fake.running.get("intelligence")
                    return self._json(200, {"running": bool(bid), "current_bench_id": bid,
                                            "current_model": fake.runs[bid]["body"]["model_id"] if bid else None})
                if p.endswith("/stream"):
                    return self._stream(p.split("/")[-2])
                if p.startswith("/admin/api/bench/") and p.endswith("/results"):
                    bid = p.split("/")[-2]
                    evs = fake.runs[bid]["events"]
                    return self._json(200, {"bench_id": bid, "status": "completed",
                                            "results": [e["data"] for e in evs if e.get("type") == "result"],
                                            "result": next((e["data"] for e in evs if e.get("type") == "result"), None)})
                self._json(404, {"detail": "no route"})

            def do_POST(self):
                body = self._body()
                fake.calls.append(("POST", self.path, body))
                if not self._auth():
                    return
                p = self.path
                if p == "/admin/api/bench/start":
                    return self._start("performance", body)
                if p == "/admin/api/bench/context/start":
                    return self._start("context", body)
                if p == "/admin/api/bench/accuracy/queue/add":
                    return self._start("intelligence", body)
                if p.endswith("/cancel"):
                    fam = "intelligence" if "accuracy" in p else ("context" if "/context/" in p else "performance")
                    fake.running.pop(fam, None)
                    return self._json(200, {"status": "cancelled"})
                if p == "/admin/api/global-settings":
                    for k, v in body.items():
                        for sec in fake.globals.values():
                            for sk in list(sec):
                                if sk == k or f"{next(n for n, s in fake.globals.items() if s is sec)}_{sk}" == k:
                                    sec[sk] = v
                    return self._json(200, {"success": True})
                self._json(404, {"detail": "no route"})

            def do_PUT(self):
                body = self._body()
                fake.calls.append(("PUT", self.path, body))
                if not self._auth():
                    return
                parts = self.path.split("/")
                mid = parts[4]
                if mid not in fake.models:
                    return self._json(404, {"detail": f"Model not found: {mid}"})
                fake.models[mid].update(body)
                return self._json(200, {"success": True})
        return H
