import pytest
from omlx_bench import config


def test_parse_dotenv_handles_comments_quotes_export():
    env = config.parse_dotenv('# c\nA=1\nexport B="two words"\nC=x # trailing\nD=\'q\'\nbad line\n')
    assert env == {"A": "1", "B": "two words", "C": "x", "D": "q"}


def test_load_reads_dotenv_and_env_wins(tmp_path):
    (tmp_path / "plan.toml").write_text("")
    (tmp_path / ".env").write_text("OMLX_API_KEY=fromfile\nOMLX_URL=http://a:1/\n")
    sub = tmp_path / "results"; sub.mkdir()
    c = config.load(sub, environ={})
    assert (c.api_key, c.url, c.paths.root) == ("fromfile", "http://a:1", tmp_path.resolve())
    assert config.load(tmp_path, environ={"OMLX_API_KEY": "env"}).api_key == "env"


def test_missing_key_is_a_clear_error(tmp_path):
    (tmp_path / "plan.toml").write_text("")
    with pytest.raises(config.ConfigError, match="MAIN"):
        config.load(tmp_path, environ={})
