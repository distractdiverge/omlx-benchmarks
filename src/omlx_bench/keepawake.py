"""Keep the Mac awake during a run.

Amphetamine (if it's running) via its AppleScript dictionary, documented at
https://iffy.freshdesk.com/support/solutions/articles/48000078223 :
  - `session is active` (must be inside a tell block)
  - `start new session with options {...}`. Note: this REPLACES a running session,
    so we only start one when none is active, and only end the one we started.
Otherwise macOS's built-in `caffeinate -i -w <our pid>`, which exits with us.
`caffeinate` can't keep a closed-lid laptop on battery awake; Amphetamine's
closed-display mode can.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from typing import Callable, Iterator

Run = Callable[..., subprocess.CompletedProcess]


def _osascript(script: str, run: Run) -> str | None:
    try:
        r = run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def amphetamine_running(run: Run) -> bool:
    # pgrep, not AppleScript: telling a closed app anything would launch it.
    try:
        return run(["pgrep", "-x", "Amphetamine"], capture_output=True, text=True).returncode == 0
    except OSError:
        return False


@contextmanager
def keep_awake(out: Callable[[str], None] = print, run: Run = subprocess.run,
               popen: Callable[..., subprocess.Popen] = subprocess.Popen,
               platform: str = sys.platform) -> Iterator[str]:
    """Yields which mechanism is holding the Mac awake: amphetamine / amphetamine-existing / caffeinate / none."""
    if platform != "darwin":
        yield "none"
        return
    if amphetamine_running(run):
        active = _osascript('tell application "Amphetamine"\nsession is active\nend tell', run)
        if active == "true":
            out("Keep-awake: your Amphetamine session is already active; leaving it alone.")
            yield "amphetamine-existing"
            return
        started = _osascript('tell application "Amphetamine" to start new session with options '
                             '{duration:0, interval:minutes, displaySleepAllowed:true}', run) is not None
        if started:
            out("Keep-awake: started an Amphetamine session (display may sleep); it ends with this run.")
            try:
                yield "amphetamine"
            finally:
                _osascript('tell application "Amphetamine" to end session', run)
                out("Keep-awake: ended the Amphetamine session.")
            return
        out("Keep-awake: Amphetamine didn't accept AppleScript (check Automation permission); using caffeinate.")
    if shutil.which("caffeinate") or platform == "darwin":
        try:
            proc = popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            out("Keep-awake: caffeinate unavailable; the Mac may sleep during the run.")
            yield "none"
            return
        out("Keep-awake: caffeinate is holding the Mac awake (not with the lid closed on battery).")
        try:
            yield "caffeinate"
        finally:
            proc.terminate()
        return
    yield "none"
