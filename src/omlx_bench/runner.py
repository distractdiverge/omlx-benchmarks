"""Run queued steps one at a time (ADR 0002), with the failure rules of ADR 0006
and the settings invariant of ADR 0003."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .client import ApiError, Client, StreamLost
from .config import Config
from .ledger import Ledger
from .queue import Queue, Step, now
from .settings import Settings, SettingsError

REATTACH_TRIES, REATTACH_WAIT = 5, 10
BUSY_POLL = 30


class StopRun(Exception):
    """Stop the whole run without charging the current step."""


class StepFailed(Exception):
    """The step itself failed: costs an attempt."""


@contextmanager
def run_lock(root: Path) -> Iterator[None]:
    lock = root / ".omlx-bench.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
            os.kill(pid, 0)
            raise StopRun(f"another omlx-bench run is active (pid {pid}); only one runner at a time")
        except (ValueError, ProcessLookupError):
            pass  # stale lock from a dead process
        except PermissionError:
            raise StopRun(f"lock held by pid {lock.read_text().strip()}") from None
    lock.write_text(str(os.getpid()))
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


class Runner:
    def __init__(self, cfg: Config, client: Client | None = None, out: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.client = client or Client(cfg.url, cfg.api_key)
        self.queue = Queue(cfg.paths.queue)
        self.ledger = Ledger(cfg.paths)
        self.settings = Settings(self.client, cfg.paths)
        self.out, self.sleep = out, sleep
        self.cfg_max_attempts = 3

    # -- family dispatch ----------------------------------------------------
    _START = {"performance": "perf_start", "intelligence": "accuracy_add", "context": "context_start"}
    _STREAM = {"performance": "perf_stream", "intelligence": "accuracy_stream", "context": "context_stream"}
    _CANCEL = {"performance": "perf_cancel", "intelligence": "accuracy_cancel", "context": "context_cancel"}
    _FINAL = {"performance": "perf_results", "context": "context_results"}

    def _client_call(self, step: Step, table: dict[str, str], *args, **kwargs):
        name = table.get(step.suite)
        if name is None:
            raise StepFailed(f"unknown suite {step.suite!r}")
        return getattr(self.client, name)(*args, **kwargs)

    def _start(self, step: Step) -> str:
        return self._client_call(step, self._START, step.body)

    def _stream(self, step: Step, bench_id: str):
        return self._client_call(step, self._STREAM, bench_id)

    def _cancel(self, step: Step, bench_id: str) -> None:
        try:
            self._client_call(step, self._CANCEL, bench_id)
        except ApiError as e:
            self.out(f"  (cancel: {e.detail})")

    def _final(self, step: Step, bench_id: str):
        name = self._FINAL.get(step.suite)
        if name is None:
            return None  # accuracy has no per-run results route; events carry everything
        try:
            return getattr(self.client, name)(bench_id)
        except ApiError:
            return None

    # -- waiting ------------------------------------------------------------
    def wait_idle(self) -> None:
        announced = False
        while True:
            try:
                busy = self.client.active()
            except ApiError as e:
                if e.status == 0:
                    raise StopRun(e.detail) from None
                raise
            if not busy:
                return
            if not announced:
                what = ", ".join(f"{k} {v.get('model_id') or ''} ({v.get('bench_id')})" for k, v in busy.items())
                self.out(f"  oMLX is busy with {what}; waiting (polling every {BUSY_POLL}s, Ctrl-C to stop)")
                announced = True
            self.sleep(BUSY_POLL)

    # -- one step -------------------------------------------------------------
    def follow(self, step: Step, bench_id: str, events: list[dict]) -> None:
        """Collect events into `events`, re-attaching on dropped streams."""
        last_msg = ""
        for tries in range(REATTACH_TRIES + 1):
            seen = 0
            try:
                for ev in self._stream(step, bench_id):
                    seen += 1
                    if seen <= len(events):
                        continue  # replayed from the start after a re-attach
                    events.append(ev)
                    t = ev.get("type")
                    if t == "progress":
                        msg = ev.get("message") or ev.get("phase") or ""
                        if msg and msg != last_msg:
                            self.out(f"  … {msg}")
                            last_msg = msg
                    elif t == "result":
                        self.out(f"  ✓ {_brief(step.suite, ev.get('data') or {})}")
                    elif t == "error":
                        raise StepFailed(ev.get("message") or "oMLX reported an error")
                return
            except StreamLost as e:
                if tries == REATTACH_TRIES:
                    raise StepFailed(f"lost the event stream {REATTACH_TRIES + 1} times: {e}") from None
                self.out(f"  stream dropped ({e}); re-attaching in {REATTACH_WAIT}s")
                self.sleep(REATTACH_WAIT)
            except ApiError as e:
                if e.status == 404:
                    raise StepFailed("oMLX no longer knows this run (server restarted?)") from None
                raise

    def run_step(self, step: Step, n: int, total: int) -> bool:
        q = self.queue
        self.wait_idle()
        prev_attempts = step.attempts
        attempt = prev_attempts + 1
        started = now()
        self.out(f"[{n}/{total}] {step.step_id} {step.model} · {step.suite} · {step.label} · "
                 f"variant {step.variant} · attempt {attempt}")
        snap = self.settings.snapshot(step.model, f"{step.step_id} attempt {attempt}")
        snap_rel = str(snap.relative_to(self.cfg.paths.root))
        q.mark(step, "running", attempts=attempt, bench_id="", last_error="")
        try:
            status, err, bench_id, events = self._attempt_step(step)
        finally:
            try:
                restored = self.settings.restore(snap)
                if restored:
                    self.out(f"  settings restored: {sorted({k for v in restored.values() for k in v})}")
            except (SettingsError, ApiError) as e:
                q.mark(step, "fail", last_error=f"SETTINGS NOT RESTORED: {e}")
                raise StopRun(f"settings restore failed; stopping so nothing stacks on an unknown state: {e}"
                              f"\n  snapshot: {snap_rel}  (fix, then: omlx-bench settings restore)") from None

        if status == "pass":
            final = self._final(step, bench_id)
            ids = self.ledger.record(step, attempt, bench_id, events, final, step.settings_dict, snap_rel, started)
            q.mark(step, "pass", result_ids=" ".join(ids))
            return True
        if status == "fail":
            rel = self.ledger.record_failure(step, attempt, bench_id, events, err, step.settings_dict, snap_rel, started)
            q.mark(step, "fail", last_error=err)
            left = self.cfg_max_attempts - attempt
            self.out(f"  ✗ {err}  ({'retry next run' if left > 0 else 'no attempts left: omlx-bench retry ' + step.step_id}; {rel})")
            return False
        # not the step's fault: undo the attempt
        q.mark(step, "pending", attempts=prev_attempts, last_error=err if status != "busy" else "")
        if status == "busy":
            return self.run_step(step, n, total)
        if status == "interrupted":
            raise KeyboardInterrupt
        raise StopRun(err)

    def _attempt_step(self, step: Step) -> tuple[str, str, str, list[dict]]:
        """Run one attempt: apply settings, start, stream, return (status, err, bench_id, events)."""
        bench_id, events = "", []
        try:
            self.settings.apply_model(step.model, step.settings_dict)
            try:
                bench_id = self._start(step)
            except ApiError as e:
                if e.status == 409:
                    raise StopRun("__busy__") from None
                if e.status == 0:
                    raise StopRun(e.detail) from None
                raise StepFailed(f"oMLX rejected the request: HTTP {e.status} {e.detail}") from None
            q = self.queue
            q.mark(step, "running", bench_id=bench_id)
            self.follow(step, bench_id, events)
            if not any(ev.get("type") == "result" for ev in events):
                raise StepFailed("benchmark finished without a result")
            return ("pass", "", bench_id, events)
        except SettingsError as e:
            return ("fail", f"settings: {e}", bench_id, events)
        except StepFailed as e:
            return ("fail", str(e), bench_id, events)
        except KeyboardInterrupt:
            if bench_id:
                self.out("  Ctrl-C: cancelling on the server…")
                self._cancel(step, bench_id)
            return ("interrupted", "cancelled by user", bench_id, events)
        except StopRun as e:
            return ("busy" if str(e) == "__busy__" else "stopped", str(e), bench_id, events)
        except ApiError as e:
            return (("stopped", e.detail) if e.status == 0 else ("fail", f"oMLX error: {e}")) + (bench_id, events)

    # -- whole run --------------------------------------------------------------
    def recover(self) -> None:
        for snap in self.settings.unrestored():
            self.out(f"Recovering: restoring settings from {snap.name} (left by an interrupted run)")
            self.settings.restore(snap)
        for step in [s for s in self.queue.steps if s.status == "running"]:
            active = {}
            try:
                active = self.client.active()
            except ApiError:
                pass
            mine = any(v.get("bench_id") == step.bench_id for v in active.values()) if step.bench_id else False
            if mine:
                self.out(f"Recovering: re-attaching to {step.step_id} ({step.bench_id}), still running on oMLX")
                snap = self.settings.snapshot(step.model, f"{step.step_id} re-attach")
                events: list[dict] = []
                try:
                    self.follow(step, step.bench_id, events)
                except (StepFailed, ApiError) as e:
                    self.out(f"  re-attach failed: {e}")
                finally:
                    self.settings.restore(snap)
                if any(ev.get("type") == "result" for ev in events):
                    ids = self.ledger.record(step, step.attempts, step.bench_id, events,
                                             self._final(step, step.bench_id), step.settings_dict,
                                             str(snap.relative_to(self.cfg.paths.root)), step.updated_at)
                    self.queue.mark(step, "pass", result_ids=" ".join(ids))
                    continue
            self.queue.mark(step, "fail", last_error="interrupted (runner stopped mid-step)")

    def run(self, model_order: list[str], max_attempts: int, only_model: str | None = None,
            only_suite: str | None = None, limit: int | None = None) -> dict[str, int]:
        self.cfg_max_attempts = max_attempts
        with run_lock(self.cfg.paths.root):
            self.recover()
            todo = [s for s in self.queue.ordered(model_order) if s.runnable(max_attempts)
                    and (only_model is None or s.model == only_model)
                    and (only_suite is None or s.suite == only_suite)]
            if limit is not None:
                todo = todo[:limit]
            if not todo:
                self.out("Nothing to run. (omlx-bench status shows the queue; omlx-bench plan adds steps.)")
                return {"pass": 0, "fail": 0}
            self.out(f"{len(todo)} step(s) to run, model by model. Ctrl-C cancels the current step cleanly.")
            tally = {"pass": 0, "fail": 0}
            for i, step in enumerate(todo, 1):
                ok = self.run_step(step, i, len(todo))
                tally["pass" if ok else "fail"] += 1
            self.out(f"Done: {tally['pass']} passed, {tally['fail']} failed.")
            return tally


def _brief(suite: str, d: dict) -> str:
    if suite == "performance":
        if d.get("test_type") == "batch":
            return f"batch {d.get('batch_size')}x: {d.get('total_throughput')} tok/s total"
        return (f"pp{d.get('pp')}: prefill {d.get('processing_tps')} tok/s, gen {d.get('gen_tps')} tok/s, "
                f"TTFT {d.get('ttft_ms')} ms")
    if suite == "intelligence":
        return f"{d.get('benchmark')}: {d.get('correct')}/{d.get('total')} ({round((d.get('accuracy') or 0) * 100, 1)}%)"
    return f"context {d.get('target_tokens')}: fits {d.get('verified_tokens')} tokens (capped by {d.get('capped_by')})"
