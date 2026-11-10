from omlx_bench.queue import Queue


def test_add_dedupes_and_persists(tmp_path):
    q = Queue(tmp_path / "queue.csv")
    a = q.add("m1", "performance", "pp1024", "baseline", {"prompt_lengths": [1024], "b": 1})
    assert q.add("m1", "performance", "pp1024", "baseline", {"b": 1, "prompt_lengths": [1024]}) is None
    q.add("m1", "performance", "pp4096", "baseline", {"prompt_lengths": [4096]})
    q.save()
    q2 = Queue(tmp_path / "queue.csv")
    assert [s.step_id for s in q2.steps] == ["S0001", "S0002"]
    assert q2.get("S0001").body == {"model_id": "m1", "prompt_lengths": [1024], "b": 1}
    assert a.status == "pending"


def test_ordering_is_model_by_model_then_suite(tmp_path):
    q = Queue(tmp_path / "q.csv")
    q.add("b", "context", "c", "v", {"t": 1})
    q.add("a", "intelligence", "i", "v", {"t": 2})
    q.add("b", "performance", "p", "v", {"t": 3})
    q.add("a", "performance", "p", "v", {"t": 4})
    assert [(s.model, s.suite) for s in q.ordered(["b", "a"])] == [
        ("b", "performance"), ("b", "context"), ("a", "performance"), ("a", "intelligence")]


def test_retry_rules(tmp_path):
    q = Queue(tmp_path / "q.csv")
    s = q.add("m", "context", "c", "v", {})
    q.mark(s, "fail", attempts=3, last_error="boom")
    assert not s.runnable(3) and s.runnable(4)
    q.reset(s.step_id)
    assert Queue(tmp_path / "q.csv").get(s.step_id).runnable(3)
    assert q.counts() == {"pending": 1, "running": 0, "pass": 0, "fail": 0}


def test_no_temp_file_left_behind(tmp_path):
    q = Queue(tmp_path / "q.csv"); q.add("m", "x", "l", "v", {}); q.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["q.csv"]
