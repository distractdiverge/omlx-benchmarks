import json
import pytest
from omlx_bench.config import Paths
from omlx_bench.settings import Settings, SettingsError, parse_assignments, resolve_global


@pytest.fixture
def st(client, tmp_path):
    return Settings(client, Paths(tmp_path))


def test_resolve_global_maps_flat_to_nested():
    nested = {"memory": {"prefill_memory_guard": True, "memory_guard_tier": "safe"}, "scheduler": {"chunked_prefill": False}}
    assert resolve_global(nested, "memory_prefill_memory_guard") is True
    assert resolve_global(nested, "memory_guard_tier") == "safe"
    assert resolve_global(nested, "chunked_prefill") is False


def test_restore_undoes_variant_and_bench_side_effect(st, fake):
    snap = st.snapshot("m2", "test")
    st.apply_model("m2", {"temperature": 0.1})
    fake.models["m2"]["max_context_window"] = 70000   # what the context bench does
    restored = st.restore(snap)
    assert fake.models["m2"] == {"max_context_window": 65536, "temperature": 0.7}
    assert set(restored["model"]) == {"temperature", "max_context_window"}
    assert json.loads(snap.read_text())["restored"] is True and st.unrestored() == []


def test_restore_sends_null_for_previously_unset(st, fake):
    snap = st.snapshot("m1", "t")
    fake.models["m1"]["max_context_window"] = 69632
    st.restore(snap)
    put = [c for c in fake.calls if c[0] == "PUT"][-1]
    assert put[2] == {"max_context_window": None} and fake.models["m1"]["max_context_window"] is None


def test_unrestored_snapshots_are_found_after_crash(st):
    p = st.snapshot("m1", "crash")
    assert st.unrestored() == [p]


def test_guardrails(st):
    with pytest.raises(SettingsError, match="never touches"):
        st.apply_model("m1", {"model_alias": "x"})
    with pytest.raises(SettingsError, match="off limits"):
        st.apply_global({"port": 9000})
    with pytest.raises(SettingsError, match="not found"):
        st.snapshot(None, "g", ["no_such_key"])


def test_global_snapshot_and_restore(st, fake):
    snap = st.snapshot(None, "g", ["memory_prefill_memory_guard"])
    st.apply_global({"memory_prefill_memory_guard": False})
    assert fake.globals["memory"]["prefill_memory_guard"] is False
    st.restore(snap)
    assert fake.globals["memory"]["prefill_memory_guard"] is True


def test_changes_log_records_everything(st, tmp_path):
    snap = st.snapshot("m1", "t"); st.apply_model("m1", {"temperature": 0.3}); st.restore(snap)
    lines = (tmp_path / "settings" / "changes.log").read_text().splitlines()
    assert [l.split("\t")[1] for l in lines] == ["set-model", "set-model"]


def test_parse_assignments():
    assert parse_assignments(["a=1", "b=true", "c=null", "d=speed", "e=0.5"]) == \
        {"a": 1, "b": True, "c": None, "d": "speed", "e": 0.5}
