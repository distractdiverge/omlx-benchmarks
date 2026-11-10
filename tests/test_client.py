import pytest
from omlx_bench.client import ApiError, Client, StreamLost
from tests.fake_omlx import KEY


def test_wrong_key_explains_main_key(fake):
    with pytest.raises(ApiError) as e:
        Client(fake.url, "sub-key").models()
    assert e.value.status == 401 and "MAIN" in str(e.value)


def test_unreachable_server_is_status_0():
    with pytest.raises(ApiError) as e:
        Client("http://127.0.0.1:9", KEY).models()
    assert e.value.status == 0


def test_perf_stream_ends_on_upload_done(client):
    bid = client.perf_start({"model_id": "m1", "prompt_lengths": [1024], "batch_sizes": []})
    types = [e["type"] for e in client.perf_stream(bid)]
    assert types[-1] == "upload_done" and "result" in types


def test_conflict_is_409(client, fake):
    fake.running["context"] = "bX"
    with pytest.raises(ApiError) as e:
        client.perf_start({"model_id": "m1", "prompt_lengths": [1024]})
    assert e.value.status == 409


def test_dropped_stream_raises_streamlost_and_replay_works(client, fake):
    bid = client.context_start({"model_id": "m1", "target_tokens": 32768})
    fake.drop_stream_once.add(bid)
    with pytest.raises(StreamLost):
        list(client.context_stream(bid))
    assert [e["type"] for e in client.context_stream(bid)][-1] == "done"


def test_accuracy_add_returns_bench_id_and_active(client):
    bid = client.accuracy_add({"model_id": "m1", "benchmarks": {"mmlu_pro": 1000}})
    assert client.active()["intelligence"]["bench_id"] == bid


def test_model_settings_roundtrip(client):
    client.put_model_settings("m1", {"temperature": 0.2})
    assert client.model_settings("m1")["temperature"] == 0.2
