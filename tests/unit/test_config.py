"""Unit tests for src/vortex/config.py.

Every expected default here was cross-checked against the actual constants in
src/vortex/main.py at the time this test was written (see the reading in this
session's history) - not assumed to match. Run with: pytest tests/unit/test_config.py
"""
import os

import pytest

from vortex.config import VortexConfig


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every VORTEX_*/USER_NAME env var cleared before each test, so defaults
    are tested against a known-clean environment rather than whatever happens
    to be set in .env on this machine."""
    for key in list(os.environ):
        if key.startswith('VORTEX_') or key == 'USER_NAME':
            monkeypatch.delenv(key, raising=False)


def test_defaults_match_main_py():
    cfg = VortexConfig.from_env()
    assert cfg.voice == 'en-US-AvaMultilingualNeural'
    assert cfg.user_name == 'Boss'
    assert cfg.tts_volume == 1.0
    assert cfg.wake_threshold == 0.8
    assert cfg.barge_in_threshold == 0.75
    assert cfg.wake_cooldown == 1.5
    assert cfg.wake_watchdog_timeout == 5.0
    assert cfg.agc_target_rms == 3500
    assert cfg.agc_max_gain == 4.0
    assert cfg.agc_noise_margin == 1.6
    assert cfg.session_timeout == 18
    assert cfg.history_turns == 10
    assert cfg.summary_max_chars == 12000
    assert cfg.llm_model == 'llama3.2:1b'
    assert 'VORTEX' in cfg.system_prompt
    assert cfg.postgres_dsn == 'dbname=vortex user=vortex password=vortex_local_dev host=localhost'
    assert cfg.qdrant_url == 'http://localhost:6333'
    assert cfg.embed_model == 'nomic-embed-text'


def test_root_dependent_paths_resolve_from_root():
    cfg = VortexConfig.from_env()
    assert cfg.wake_word == os.path.join(cfg.root, 'tools', 'wakeword', 'models', 'hey_vortex.onnx')
    assert cfg.log_dir == os.path.join(cfg.root, 'logs')
    assert cfg.data_dir == os.path.join(cfg.root, 'data')
    assert cfg.memory_db_path == os.path.join(cfg.data_dir, 'vortex_memory.db')


def test_env_var_override(monkeypatch):
    monkeypatch.setenv('VORTEX_WAKE_THRESHOLD', '0.65')
    monkeypatch.setenv('VORTEX_VOICE', 'en-GB-SoniaNeural')
    cfg = VortexConfig.from_env()
    assert cfg.wake_threshold == 0.65
    assert cfg.voice == 'en-GB-SoniaNeural'


def test_root_override_changes_dependent_paths(monkeypatch):
    monkeypatch.setenv('VORTEX_HOME', r'C:\CustomRoot')
    cfg = VortexConfig.from_env()
    assert cfg.root == r'C:\CustomRoot'
    assert cfg.log_dir == r'C:\CustomRoot\logs'
    assert cfg.wake_word == r'C:\CustomRoot\tools\wakeword\models\hey_vortex.onnx'


def test_wake_word_env_override_bypasses_root(monkeypatch):
    """VORTEX_WAKE_WORD, if set directly, should win over the root-derived path."""
    monkeypatch.setenv('VORTEX_WAKE_WORD', r'D:\somewhere\custom_model.onnx')
    cfg = VortexConfig.from_env()
    assert cfg.wake_word == r'D:\somewhere\custom_model.onnx'


def test_config_is_frozen():
    """A config object shouldn't be mutable after construction - accidental
    reassignment of a shared config instance has caused real bugs elsewhere
    in this project (see the wake-stream reuse bug in CHANGELOG.md); frozen
    dataclasses raise instead of silently allowing it."""
    cfg = VortexConfig.from_env()
    with pytest.raises(Exception):
        cfg.voice = 'something else'


def test_two_instances_are_independent_snapshots(monkeypatch):
    """Confirms the default_factory fix actually works: changing an env var
    between constructions must be picked up, not frozen at import time."""
    cfg1 = VortexConfig.from_env()
    assert cfg1.wake_threshold == 0.8
    monkeypatch.setenv('VORTEX_WAKE_THRESHOLD', '0.95')
    cfg2 = VortexConfig.from_env()
    assert cfg2.wake_threshold == 0.95
    assert cfg1.wake_threshold == 0.8  # cfg1 is a frozen snapshot, unaffected
