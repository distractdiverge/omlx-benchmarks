"""omlx-bench: drive oMLX performance, intelligence and context benchmarks one test at a time.

  uv run omlx-bench models                 list models on the server
  uv run omlx-bench plan                   plan.toml -> new steps in queue.csv
  uv run omlx-bench run                    run pending + retryable steps, model by model
  uv run omlx-bench status                 queue summary and failures
  uv run omlx-bench retry S0007 [S0008…]   reset steps that used up their attempts
  uv run omlx-bench settings show|set|global|restore
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from . import config, steps
from .client import ApiError, Client
from .keepawake import keep_awake
from .queue import Queue
from .runner import Runner, StopRun
from .settings import Settings, SettingsError, parse_assignments


def _cfg():
    return config.load()


def _client(cfg):
    return Client(cfg.url, cfg.api_key)


def cmd_models(a) -> int:
    cfg = _cfg()
    for m in _client(cfg).models():
        if m.get("virtual"):
            continue
        s = m.get("settings") or {}
        print(f"{m['id']:48} {m.get('model_type') or '':6} {'loaded' if m.get('loaded') else '':6} "
              f"{m.get('estimated_size_formatted') or '':>9}  ctx={s.get('max_context_window') or 'default'}")
    return 0


def cmd_plan(a) -> int:
    cfg = _cfg()
    plan = steps.load(cfg.paths.plan)
    if not plan.models:
        print("plan.toml lists no models yet. Add model ids (see: omlx-bench models).")
        return 1
    try:
        known = {m["id"] for m in _client(cfg).models()}
        missing = [m for m in plan.models if m not in known]
        if missing:
            print(f"Not on the oMLX server: {missing}. Fix plan.toml (ids from: omlx-bench models).")
            return 1
    except ApiError as e:
        print(f"(couldn't check model ids against oMLX: {e.detail})")
    q = Queue(cfg.paths.queue)
    added = [s for p in plan.steps if (s := q.add(p.model, p.suite, p.label, p.variant, p.params, p.settings))]
    q.save()
    by = Counter((s.model, s.suite) for s in added)
    print(f"Added {len(added)} step(s); {len(q.steps) - len(added)} already planned.")
    for (m, suite), n in by.items():
        print(f"  {m}: {n} {suite}")
    return 0


def cmd_run(a) -> int:
    cfg = _cfg()
    plan = steps.load(cfg.paths.plan)
    runner = Runner(cfg)
    try:
        if a.no_keep_awake:
            tally = runner.run(plan.models, plan.max_attempts, a.model, a.suite, a.limit)
        else:
            with keep_awake():
                tally = runner.run(plan.models, plan.max_attempts, a.model, a.suite, a.limit)
    except StopRun as e:
        print(f"Stopped: {e}")
        return 1
    except KeyboardInterrupt:
        print("Interrupted. The current step was cancelled on oMLX, settings restored, and it will run again next time.")
        return 130
    return 0 if tally["fail"] == 0 else 2


def cmd_status(a) -> int:
    cfg = _cfg()
    plan = steps.load(cfg.paths.plan)
    q = Queue(cfg.paths.queue)
    c = q.counts()
    print(f"{len(q.steps)} steps: {c['pass']} pass · {c['fail']} fail · {c['pending']} pending · {c['running']} running")
    for model in dict.fromkeys(s.model for s in q.ordered(plan.models)):
        ms = [s for s in q.steps if s.model == model]
        mc = Counter(s.status for s in ms)
        print(f"  {model}: {mc['pass']}/{len(ms)} done" + (f", {mc['fail']} failed" if mc["fail"] else ""))
    fails = [s for s in q.steps if s.status == "fail"]
    if fails:
        print("Failures:")
        for s in fails:
            left = plan.max_attempts - s.attempts
            how = f"retries next run ({left} left)" if left > 0 else f"needs: omlx-bench retry {s.step_id}"
            print(f"  {s.step_id} {s.model} · {s.suite} · {s.label} [{s.variant}]: {s.last_error}  ({how})")
    stuck = [s for s in q.steps if s.status == "running"]
    if stuck:
        print(f"Running/interrupted: {', '.join(s.step_id for s in stuck)} (the next run recovers these)")
    return 0


def cmd_retry(a) -> int:
    cfg = _cfg()
    q = Queue(cfg.paths.queue)
    ids = [s.step_id for s in q.steps if s.status == "fail"] if a.all_failed else a.step_ids
    if not ids:
        print("Nothing to reset.")
        return 0
    for sid in ids:
        try:
            q.reset(sid)
            print(f"{sid} -> pending")
        except KeyError as e:
            print(e.args[0])
            return 1
    return 0


def cmd_settings(a) -> int:
    cfg = _cfg()
    client = _client(cfg)
    st = Settings(client, cfg.paths)
    if a.action == "show":
        data = client.model_settings(a.model) if a.model else client.global_settings()
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if a.action in ("set", "global"):
        changes = parse_assignments(a.assignments)
        model = a.model if a.action == "set" else None
        st.check_model(changes) if model else st.check_global(changes)  # refuse before touching anything
        snap = st.snapshot(model, "manual change via `settings " + a.action + "`",
                           [] if model else list(changes))
        # Deliberate changes are kept; the snapshot is a backup, not a pending restore.
        data = json.loads(snap.read_text()); data["restored"] = True; data["manual_backup"] = True
        snap.write_text(json.dumps(data, indent=2))
        (st.apply_model(model, changes) if model else st.apply_global(changes))
        print(f"Changed {sorted(changes)}. Undo with: omlx-bench settings restore {snap.name}")
        return 0
    if a.action == "restore":
        if a.snapshot:
            path = cfg.paths.snapshots / a.snapshot
            if not path.is_file():
                print(f"No snapshot {a.snapshot} in settings/snapshots/")
                return 1
            paths = [path]
        else:
            paths = st.unrestored()
            if not paths:
                print("Nothing pending: every snapshot has been restored. Name one to roll back a manual change.")
                return 0
        for p in paths:
            done = st.restore(p)
            print(f"{p.name}: {'restored ' + json.dumps(done) if done else 'already matched'}")
        return 0
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omlx-bench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models", help="list models on the oMLX server").set_defaults(fn=cmd_models)
    sub.add_parser("plan", help="add steps from plan.toml to queue.csv").set_defaults(fn=cmd_plan)
    r = sub.add_parser("run", help="run pending and retryable steps")
    r.add_argument("--model", help="only this model")
    r.add_argument("--suite", choices=["performance", "intelligence", "context"], help="only this suite")
    r.add_argument("--limit", type=int, help="stop after N steps (handy for a smoke test)")
    r.add_argument("--no-keep-awake", action="store_true", help="don't hold the Mac awake")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("status", help="queue summary and failures").set_defaults(fn=cmd_status)
    rt = sub.add_parser("retry", help="reset failed steps to pending")
    rt.add_argument("step_ids", nargs="*")
    rt.add_argument("--all-failed", action="store_true")
    rt.set_defaults(fn=cmd_retry)
    s = sub.add_parser("settings", help="show, change or restore oMLX settings")
    ss = s.add_subparsers(dest="action", required=True)
    sh = ss.add_parser("show"); sh.add_argument("model", nargs="?")
    se = ss.add_parser("set"); se.add_argument("model"); se.add_argument("assignments", nargs="+")
    sg = ss.add_parser("global"); sg.add_argument("assignments", nargs="+")
    sr = ss.add_parser("restore"); sr.add_argument("snapshot", nargs="?")
    s.set_defaults(fn=cmd_settings)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except (config.ConfigError, steps.PlanError, SettingsError) as e:
        print(f"Error: {e}")
        return 1
    except ApiError as e:
        print(f"oMLX: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
