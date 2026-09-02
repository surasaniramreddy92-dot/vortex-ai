"""Correctness tests for _monotonic_align_numba.py - the Numba port that
replaces piper-tts's missing compiled monotonic-alignment extension (see
that module's own docstring and train.py for why this exists at all).

Not part of the main `tests/unit/` suite or CI - same reason
`tools/wakeword/`'s own training code isn't either: it needs the
`voice-training` extra (torch, numba, ...), a heavy, rarely-installed
dependency set that CI's normal `pip install -e ".[all,voice-offline,dev]"`
doesn't include. Run manually after installing that extra:
    pytest tools/voice_training/test_monotonic_align_numba.py

This is training-critical code - a silently wrong alignment wouldn't
crash, it would just teach the model bad timing from every single
utterance, which is exactly the kind of bug that's expensive to notice
after the fact. These tests check actual DP correctness (the produced path
is optimal for an unambiguous case, not just "some path"), not merely that
the function runs without raising.
"""
import numpy as np

from _monotonic_align_numba import maximum_path, maximum_path_c


def _run(value, t_y, t_x):
    value = np.asarray(value, dtype=np.float32)
    path = np.zeros_like(value, dtype=np.int32)
    maximum_path_c(path[np.newaxis], value[np.newaxis], np.array([t_y], dtype=np.int32),
                    np.array([t_x], dtype=np.int32))
    return path


def _assert_valid_monotonic_path(path, t_y, t_x):
    """Structural invariants any correct output must satisfy, regardless of
    which specific path is optimal for a given value matrix."""
    assert path.sum() == t_y, 'exactly one chosen column per row'
    cols = path.argmax(axis=1)
    assert cols[0] == 0, 'must start at column 0'
    assert cols[-1] == t_x - 1, 'must end at the last column'
    assert all(cols[i + 1] >= cols[i] for i in range(len(cols) - 1)), 'columns must be non-decreasing'


def test_unambiguous_two_segment_path():
    """A value matrix that overwhelmingly favors staying at column 0 for
    the first half and column 1 for the second half - the optimal path is
    unambiguous and can be verified by hand."""
    value = [[10.0, -100.0],
             [10.0, -100.0],
             [-100.0, 10.0],
             [-100.0, 10.0]]
    path = _run(value, t_y=4, t_x=2)
    expected = np.array([[1, 0], [1, 0], [0, 1], [0, 1]], dtype=np.int32)
    assert np.array_equal(path, expected)


def test_single_frame_single_phoneme():
    path = _run([[0.0]], t_y=1, t_x=1)
    assert np.array_equal(path, [[1]])


def test_diagonal_when_all_scores_equal():
    """With no signal at all (uniform scores), the DP still must produce a
    *structurally valid* monotonic path - there's no "wrong" choice among
    valid ones here, but there must be no invalid one (skipping a column,
    non-monotonic, or not covering the full range)."""
    value = np.zeros((5, 3), dtype=np.float32)
    path = _run(value, t_y=5, t_x=3)
    _assert_valid_monotonic_path(path, t_y=5, t_x=3)


def test_produces_valid_path_across_several_random_cases():
    rng = np.random.default_rng(0)
    for _ in range(20):
        t_y = int(rng.integers(3, 12))
        t_x = int(rng.integers(1, t_y))
        value = rng.normal(size=(t_y, t_x)).astype(np.float32)
        path = _run(value, t_y, t_x)
        _assert_valid_monotonic_path(path, t_y, t_x)


def test_maximum_path_wrapper_matches_maximum_path_c_shape():
    """The public maximum_path(neg_cent, mask) wrapper (what
    train.py actually substitutes into piper's models.py) must return a
    torch tensor of the same shape as its input, on the same device/dtype -
    matching piper's own __init__.py contract exactly."""
    import torch
    neg_cent = torch.randn(2, 5, 3)
    mask = torch.ones(2, 5, 3)
    result = maximum_path(neg_cent, mask)
    assert result.shape == neg_cent.shape
    assert result.dtype == neg_cent.dtype
    assert result.device == neg_cent.device
