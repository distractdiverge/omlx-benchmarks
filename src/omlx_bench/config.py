"""Configuration: project paths and the oMLX connection, from env vars and `.env`.

Real environment variables win over `.env`, so a one-off
`OMLX_URL=http://other:8000 uv run omlx-bench ...` works without editing files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "http://localhost:8000"


class ConfigError(Exception):
    pass


def parse_dotenv(text: str) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the directory holding plan.toml."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / "plan.toml").is_file():
            return d
    raise ConfigError(f"No plan.toml found in {here} or any parent; run from the project folder.")


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def plan(self) -> Path: return self.root / "plan.toml"
    @property
    def queue(self) -> Path: return self.root / "queue.csv"
    @property
    def results(self) -> Path: return self.root / "results"
    @property
    def index(self) -> Path: return self.results / "index.csv"
    @property
    def details(self) -> Path: return self.results / "details"
    @property
    def exports(self) -> Path: return self.results / "exports"
    @property
    def settings(self) -> Path: return self.root / "settings"
    @property
    def snapshots(self) -> Path: return self.settings / "snapshots"
    @property
    def changes_log(self) -> Path: return self.settings / "changes.log"


@dataclass(frozen=True)
class Config:
    url: str
    api_key: str
    paths: Paths


def load(start: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    root = find_root(start)
    env = dict(environ if environ is not None else os.environ)
    dotenv_path = root / ".env"
    fileenv = parse_dotenv(dotenv_path.read_text()) if dotenv_path.is_file() else {}
    def get(k: str, d: str = "") -> str:
        return env.get(k) or fileenv.get(k) or d

    key = get("OMLX_API_KEY")
    if not key:
        raise ConfigError("OMLX_API_KEY is empty. Put the MAIN oMLX API key in .env (sub-keys can't use admin routes).")
    return Config(url=get("OMLX_URL", DEFAULT_URL).rstrip("/"), api_key=key, paths=Paths(root))
