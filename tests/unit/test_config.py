from pathlib import Path

import pytest

from local_tts_reader.config import ConfigurationError, Settings, default_data_dir


def test_default_settings_use_the_hidden_home_directory() -> None:
    assert Settings().data_dir == default_data_dir()
    assert default_data_dir().name == ".local-natural-tts-reader"
    assert Settings().chunk_target_chars == 280
    assert Settings().chunk_hard_limit_chars == 360


def test_loading_a_missing_explicit_config_creates_a_scoped_default(tmp_path: Path) -> None:
    config_path = tmp_path / "profile" / "config.toml"

    settings = Settings.load(config_path)

    assert config_path.is_file()
    assert settings.config_path == config_path
    assert settings.data_dir == config_path.parent
    assert 'data_dir = "' in config_path.read_text(encoding="utf-8")


def test_toml_configuration_overrides_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """data_dir = "~/reader-library"
default_voice = "Ryan"
default_speed = 1.2
max_file_bytes = 4096
chunk_target_chars = 100
chunk_hard_limit_chars = 200
""",
        encoding="utf-8",
    )

    settings = Settings.load(config_path)

    assert settings.data_dir == Path.home() / "reader-library"
    assert settings.default_voice == "Ryan"
    assert settings.default_speed == 1.2
    assert settings.max_file_bytes == 4096
    assert settings.chunk_target_chars == 100
    assert settings.chunk_hard_limit_chars == 200


def test_legacy_default_chunk_limits_are_migrated_once(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "chunk_target_chars = 1200\nchunk_hard_limit_chars = 1800\n",
        encoding="utf-8",
    )

    settings = Settings.load(config_path)

    assert settings.chunk_target_chars == 280
    assert settings.chunk_hard_limit_chars == 360
    migrated = config_path.read_text(encoding="utf-8")
    assert "chunk_target_chars = 280" in migrated
    assert "chunk_hard_limit_chars = 360" in migrated


def test_invalid_configuration_is_actionable(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("chunk_target_chars = 400\nchunk_hard_limit_chars = 200\n")

    with pytest.raises(ConfigurationError, match="chunk_hard_limit_chars"):
        Settings.load(config_path)
