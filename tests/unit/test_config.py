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
    assert cfg.wake_threshold == 0.60
    assert cfg.barge_in_threshold == 0.60
    assert cfg.wake_cooldown == 1.5
    assert cfg.wake_watchdog_timeout == 5.0
    assert cfg.agc_target_rms == 3500
    assert cfg.agc_max_gain == 4.0
    assert cfg.agc_noise_margin == 1.6
    assert cfg.session_timeout == 18
    assert cfg.history_turns == 10
    assert cfg.summary_max_chars == 12000
    assert cfg.llm_model == 'llama3.2:1b'
    assert cfg.llm_max_tokens == 32
    assert 'VORTEX' in cfg.system_prompt
    assert cfg.postgres_dsn == 'dbname=vortex user=vortex password=vortex_local_dev host=localhost'
    assert cfg.qdrant_url == 'http://localhost:6333'
    assert cfg.embed_model == 'nomic-embed-text'
    assert cfg.activation_response == 'Yes Boss?'
    assert cfg.barge_in_response == "Yes Boss, I'm listening."
    assert cfg.personality_mode == 'professional'


def test_activation_and_personality_env_overrides(monkeypatch):
    monkeypatch.setenv('VORTEX_ACTIVATION_RESPONSE', 'At your service.')
    monkeypatch.setenv('VORTEX_BARGE_IN_RESPONSE', 'Interrupted, go ahead.')
    monkeypatch.setenv('VORTEX_PERSONALITY_MODE', 'witty')
    cfg = VortexConfig.from_env()
    assert cfg.activation_response == 'At your service.'
    assert cfg.barge_in_response == 'Interrupted, go ahead.'
    assert cfg.personality_mode == 'witty'


def test_root_dependent_paths_resolve_from_root():
    cfg = VortexConfig.from_env()
    assert cfg.log_dir == os.path.join(cfg.root, 'logs')
    assert cfg.data_dir == os.path.join(cfg.root, 'data')
    assert cfg.memory_db_path == os.path.join(cfg.data_dir, 'vortex_memory.db')


def test_wake_word_default_is_repo_relative_not_root_relative():
    """Regression test for a real bug: wake_word's default used to be
    derived from `root` (os.path.join(root, 'tools', 'wakeword', ...)),
    which only ever "worked" because this repo's checkout and its
    VORTEX_HOME happened to be the same E:\\VORTEX directory on this one
    machine. The trained model is a repo-committed asset, not user data -
    its default must resolve relative to config.py's own file location,
    independent of root/VORTEX_HOME entirely. Caught when CI (a fresh
    checkout with VORTEX_HOME defaulting to ~/.vortex, nowhere near the
    repo) failed to find the model at all."""
    from vortex.config import _DEFAULT_WAKE_WORD_PATH
    cfg = VortexConfig.from_env()
    assert cfg.wake_word == str(_DEFAULT_WAKE_WORD_PATH)
    assert os.path.exists(cfg.wake_word), 'the real, repo-committed model file should exist at this path'


def test_env_var_override(monkeypatch):
    monkeypatch.setenv('VORTEX_WAKE_THRESHOLD', '0.65')
    monkeypatch.setenv('VORTEX_VOICE', 'en-GB-SoniaNeural')
    cfg = VortexConfig.from_env()
    assert cfg.wake_threshold == 0.65
    assert cfg.voice == 'en-GB-SoniaNeural'


def test_root_override_changes_dependent_paths_but_not_wake_word(monkeypatch):
    from vortex.config import _DEFAULT_WAKE_WORD_PATH
    monkeypatch.setenv('VORTEX_HOME', r'C:\CustomRoot')
    cfg = VortexConfig.from_env()
    assert cfg.root == r'C:\CustomRoot'
    assert cfg.log_dir == r'C:\CustomRoot\logs'
    # wake_word is repo-relative, not root-relative - a root override must
    # not move it (see test_wake_word_default_is_repo_relative_not_root_relative).
    assert cfg.wake_word == str(_DEFAULT_WAKE_WORD_PATH)


def test_wake_word_env_override_bypasses_the_repo_relative_default(monkeypatch):
    """VORTEX_WAKE_WORD, if set directly, should win over the repo-relative default."""
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
    assert cfg1.wake_threshold == 0.60
    monkeypatch.setenv('VORTEX_WAKE_THRESHOLD', '0.95')
    cfg2 = VortexConfig.from_env()
    assert cfg2.wake_threshold == 0.95
    assert cfg1.wake_threshold == 0.60  # cfg1 is a frozen snapshot, unaffected
