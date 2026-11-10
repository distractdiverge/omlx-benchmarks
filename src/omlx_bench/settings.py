"""Settings guardrails and snapshot -> apply -> restore (ADR 0003).

Per-model settings: read from GET /admin/api/models (each model's `settings`),
written with PUT /admin/api/models/{id}/settings, which rejects unknown keys and
treats an explicit null as "clear to default".

Global settings: GET /admin/api/global-settings returns nested sections
({"memory": {"prefill_memory_guard": ...}, ...}) while POST takes flat keys
("memory_prefill_memory_guard"). `resolve_global` maps one to the other; a flat key
it can't find can't be snapshotted, so the tool refuses to change it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .client import ApiError, Client
from .config import Paths
from .files import write_json
from .ledger import safe_name
from .queue import now

# Stored on the model but not accepted by the update endpoint (oMLX 2165e75).
NOT_UPDATABLE = {"active_profile_name", "description", "display_name"}
# Never changed by the tool: they change how the model is addressed.
FORBIDDEN_MODEL_KEYS = {"model_alias", "model_type_override"}
# Never changed by the tool: server binding, access, and where models live.
FORBIDDEN_GLOBAL_KEYS = {"host", "port", "api_key", "sub_keys", "skip_api_key_verification",
                         "model_dirs", "model_dir", "ssd_cache_dir", "auth", "server_aliases"}
_MISSING = object()


class SettingsError(Exception):
    pass


def resolve_global(nested: dict, flat_key: str):
    """Current value for a flat POST key, or _MISSING."""
    for section, values in nested.items():
        if not isinstance(values, dict):
            continue
        if flat_key in values:
            return values[flat_key]
        if flat_key.startswith(section + "_") and flat_key[len(section) + 1:] in values:
            return values[flat_key[len(section) + 1:]]
    return nested.get(flat_key, _MISSING)


def parse_assignments(items: list[str]) -> dict:
    """['temperature=0.2', 'enable_thinking=true', 'max_context_window=null'] -> dict (JSON values)."""
    out = {}
    for it in items:
        if "=" not in it:
            raise SettingsError(f"expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except ValueError:
            out[k.strip()] = v  # bare strings: profile=speed
    return out


class Settings:
    def __init__(self, client: Client, paths: Paths):
        self.client, self.paths = client, paths

    # -- logging ----------------------------------------------------------
    def log(self, action: str, target: str, changes: dict, note: str = "") -> None:
        self.paths.changes_log.parent.mkdir(parents=True, exist_ok=True)
        line = f"{now()}\t{action}\t{target}\t{json.dumps(changes, sort_keys=True)}"
        with open(self.paths.changes_log, "a") as f:
            f.write(line + (f"\t{note}" if note else "") + "\n")

    # -- snapshots --------------------------------------------------------
    def snapshot(self, model: str | None, reason: str, global_keys: list[str] = ()) -> Path:
        """Save current settings before any change. Returns the snapshot file."""
        snap = {"taken_at": now(), "reason": reason, "restored": False, "model": model,
                "model_settings": self.client.model_settings(model) if model else None,
                "global": {}}
        if global_keys:
            nested = self.client.global_settings()
            for k in global_keys:
                v = resolve_global(nested, k)
                if v is _MISSING:
                    raise SettingsError(f"global setting {k!r} not found in GET /admin/api/global-settings; "
                                        "can't snapshot it, so won't change it")
                snap["global"][k] = v
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.paths.snapshots / f"{stamp}_{safe_name(model or 'global')}.json"
        write_json(path, snap)
        return path

    def restore(self, path: Path) -> dict:
        """Put back everything that differs from the snapshot. Returns what was restored."""
        snap = json.loads(path.read_text())
        restored: dict = {}
        model = snap.get("model")
        if model and snap.get("model_settings") is not None:
            before, current = snap["model_settings"], self.client.model_settings(model)
            diff = {k: before.get(k) for k in set(before) | set(current)
                    if k not in NOT_UPDATABLE and before.get(k) != current.get(k)}
            if diff:
                self._put_model(model, diff)
                after = self.client.model_settings(model)
                still = {k for k in diff if after.get(k) != before.get(k)}
                if still:
                    raise SettingsError(f"restore of {model} incomplete; still differ: {sorted(still)} "
                                        f"(snapshot {path.name})")
                restored["model"] = diff
        if snap.get("global"):
            nested = self.client.global_settings()
            gdiff = {k: v for k, v in snap["global"].items() if resolve_global(nested, k) != v}
            if gdiff:
                self.client.post_global_settings(gdiff)
                self.log("restore-global", "global", gdiff, path.name)
                restored["global"] = gdiff
        snap["restored"], snap["restored_at"] = True, now()
        write_json(path, snap)
        return restored

    def unrestored(self) -> list[Path]:
        if not self.paths.snapshots.is_dir():
            return []
        out = []
        for p in sorted(self.paths.snapshots.glob("*.json")):
            try:
                if not json.loads(p.read_text()).get("restored", True):
                    out.append(p)
            except ValueError:
                continue
        return out

    # -- changes ----------------------------------------------------------
    def _put_model(self, model: str, changes: dict) -> None:
        self.client.put_model_settings(model, changes)
        self.log("set-model", model, changes)

    def apply_model(self, model: str, changes: dict) -> None:
        bad = FORBIDDEN_MODEL_KEYS & set(changes)
        if bad:
            raise SettingsError(f"refusing to change {sorted(bad)}: the tool never touches them")
        bad = NOT_UPDATABLE & set(changes)
        if bad:
            raise SettingsError(f"{sorted(bad)} can't be set through oMLX's settings API")
        if changes:
            try:
                self._put_model(model, changes)
            except ApiError as e:
                raise SettingsError(f"oMLX rejected settings {changes} for {model}: {e.detail}") from None

    def apply_global(self, changes: dict) -> None:
        bad = FORBIDDEN_GLOBAL_KEYS & set(changes)
        if bad:
            raise SettingsError(f"refusing to change {sorted(bad)}: binding/auth/model-folder settings are off limits")
        if changes:
            self.client.post_global_settings(changes)
            self.log("set-global", "global", changes)
