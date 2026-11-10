import pytest
from tests.fake_omlx import FakeOmlx, KEY
from omlx_bench.client import Client


@pytest.fixture
def fake():
    f = FakeOmlx()
    yield f
    f.close()


@pytest.fixture
def client(fake):
    return Client(fake.url, KEY)
