from pathlib import Path

import pytest

from doc_extract.service.config import ConfigError, Settings, parse_api_keys

KEY = "k" * 32


def test_reads_environment():
    settings = Settings.from_env(
        {
            "DOC_EXTRACT_API_KEYS": f"alice:{KEY}",
            "DOC_EXTRACT_DB": "/srv/db.sqlite",
            "DOC_EXTRACT_SECONDARY": "none",
            "DOC_EXTRACT_REPAIR": "0",
            "DOC_EXTRACT_MAX_UPLOAD_MB": "2",
        }
    )

    assert settings.api_keys == {KEY: "alice"}
    assert settings.db_path == Path("/srv/db.sqlite")
    assert settings.secondary is None and settings.repair is False
    assert settings.max_upload_bytes == 2 * 1024 * 1024


def test_refuses_to_start_open_by_accident():
    with pytest.raises(ConfigError, match="DOC_EXTRACT_API_KEYS"):
        Settings.from_env({})

    assert Settings.from_env({"DOC_EXTRACT_ALLOW_ANONYMOUS": "1"}).allow_anonymous


@pytest.mark.parametrize("raw", ["alice", "alice:short", f":{KEY}"])
def test_rejects_malformed_or_weak_keys(raw):
    with pytest.raises(ConfigError):
        parse_api_keys(raw)


def test_rejects_same_method_twice():
    with pytest.raises(ConfigError, match="different method"):
        Settings(allow_anonymous=True, primary="text", secondary="text")
