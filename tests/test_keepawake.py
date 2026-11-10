import subprocess
from omlx_bench.keepawake import keep_awake


class FakeMac:
    def __init__(self, amph=True, active=False, script_ok=True):
        self.amph, self.active, self.script_ok, self.cmds = amph, active, script_ok, []

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 0 if self.amph else 1, "", "")
        script = cmd[2]
        if not self.script_ok:
            return subprocess.CompletedProcess(cmd, 1, "", "not authorized")
        if "session is active" in script:
            return subprocess.CompletedProcess(cmd, 0, "true\n" if self.active else "false\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def popen(self, cmd):
        self.cmds.append(cmd)
        class P:
            terminated = False
            def terminate(s): s.terminated = True
        self.proc = P()
        return self.proc


def scripts(m):
    return [c[2] for c in m.cmds if c[0] == "osascript"]


def test_starts_and_ends_our_own_amphetamine_session():
    m = FakeMac()
    with keep_awake(out=lambda s: None, run=m.run, popen=m.popen, platform="darwin") as how:
        assert how == "amphetamine"
    assert any("start new session" in s for s in scripts(m)) and "end session" in scripts(m)[-1]


def test_leaves_an_existing_session_alone():
    m = FakeMac(active=True)
    with keep_awake(out=lambda s: None, run=m.run, popen=m.popen, platform="darwin") as how:
        assert how == "amphetamine-existing"
    assert not any("start new" in s or "end session" in s for s in scripts(m))


def test_falls_back_to_caffeinate_and_never_launches_amphetamine():
    m = FakeMac(amph=False)
    with keep_awake(out=lambda s: None, run=m.run, popen=m.popen, platform="darwin") as how:
        assert how == "caffeinate"
    assert scripts(m) == [] and m.proc.terminated
    assert m.cmds[-1][:3] == ["caffeinate", "-i", "-w"]


def test_amphetamine_without_automation_permission_falls_back():
    m = FakeMac(script_ok=False)
    with keep_awake(out=lambda s: None, run=m.run, popen=m.popen, platform="darwin") as how:
        assert how == "caffeinate"


def test_noop_off_macos():
    with keep_awake(out=lambda s: None, platform="linux") as how:
        assert how == "none"
