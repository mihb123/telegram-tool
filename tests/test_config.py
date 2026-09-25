from __future__ import annotations

import stat
from pathlib import Path

import pytest

from tele_cli.config import (
    ConfigError,
    Credentials,
    default_media_root,
    load_credentials,
    save_credentials,
    session_file_path,
    validate_credentials,
)


def test_save_and_load_credentials_with_private_permissions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("TELE_API_ID", raising=False)
    monkeypatch.delenv("TELE_API_HASH", raising=False)
    path = save_credentials(Credentials(12345, "hash-value"))
    assert load_credentials() == Credentials(12345, "hash-value")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_environment_credentials_must_be_complete(monkeypatch) -> None:
    monkeypatch.setenv("TELE_API_ID", "123")
    monkeypatch.delenv("TELE_API_HASH", raising=False)
    with pytest.raises(ConfigError, match="both"):
        load_credentials()


def test_session_override_expands_to_session_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TELE_SESSION", str(tmp_path / "custom"))
    assert session_file_path() == tmp_path / "custom.session"


@pytest.mark.parametrize("api_id", ["", "abc", "0", "-2"])
def test_invalid_api_id(api_id: str) -> None:
    with pytest.raises(ConfigError):
        validate_credentials(api_id, "hash")


def test_default_media_root_defaults_to_home_telegram_files(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TELE_MEDIA_DIR", raising=False)
    monkeypatch.delenv("TELE_DOWNLOAD_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    assert default_media_root() == (tmp_path / "userhome" / "telegram-files").resolve()


def test_default_media_root_environment_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TELE_MEDIA_DIR", str(tmp_path / "env-media"))
    assert default_media_root() == (tmp_path / "env-media").resolve()

    monkeypatch.delenv("TELE_MEDIA_DIR", raising=False)
    monkeypatch.setenv("TELE_DOWNLOAD_DIR", str(tmp_path / "env-download"))
    assert default_media_root() == (tmp_path / "env-download").resolve()


def test_default_media_root_config_json_override(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TELE_MEDIA_DIR", raising=False)
    monkeypatch.delenv("TELE_DOWNLOAD_DIR", raising=False)
    cfg_dir = tmp_path / "config" / "tele"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text('{"media_dir": "~/custom-tele"}', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert default_media_root() == Path("~/custom-tele").expanduser().resolve()
