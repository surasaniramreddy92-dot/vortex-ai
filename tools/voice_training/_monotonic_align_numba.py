"""A faithful Numba port of piper-tts's monotonic-alignment-search Cython
extension (piper/train/vits/monotonic_align/core.pyx) - needed because that
extension's source file is not actually included in the published
piper-tts PyPI wheel (confirmed by inspecting the installed package
directly - `setup.py`/`__init__.py` are present, `core.pyx` is not, in
both piper-tts==1.7.0 and 1.6.1), so it can never be compiled from what
pip installs, with or without a C compiler.

The reference algorithm below was fetched byte-for-byte from the project's
own GitHub source (github.com/OHF-voice/piper1-gpl, the exact repository
piper-tts's own package metadata names as its homepage) via a direct raw
HTTP request - NOT through an LLM-summarizing fetch tool, specifically
because algorithmic source code must be reproduced exactly, not
paraphrased. maximum_path_each/maximum_path_c below are a line-for-line
translation of that verified source (cimport/prange -> numba.njit/prange,
memoryviews -> plain numpy arrays, everything else identical), not a
reimplementation from memory - see train.py for where this is wired in in
place of the missing compiled extension.
"""
import numba
import numpy as np


@numba.njit(cache=True)
def _maximum_path_each(path, value, t_y, t_x, max_neg_val=-1e9):
    index = t_x - 1
    for y in range(t_y):
        for x in range(max(0, t_x + y - t_y), min(t_x, y + 1)):
            if x == y:
                v_cur = max_neg_val
            else:
                v_cur = value[y - 1, x]
            if x == 0:
                if y == 0:
                    v_prev = 0.0
                else:
                    v_prev = max_neg_val
            else:
                v_prev = value[y - 1, x - 1]
            value[y, x] += max(v_prev, v_cur)

    for y in range(t_y - 1, -1, -1):
        path[y, index] = 1
        if index != 0 and (index == y or value[y - 1, index] < value[y - 1, index - 1]):
            index = index - 1


@numba.njit(cache=True, parallel=True)
def maximum_path_c(paths, values, t_ys, t_xs):
    b = paths.shape[0]
    for i in numba.prange(b):
        _maximum_path_each(paths[i], values[i], t_ys[i], t_xs[i])


def maximum_path(neg_cent, mask):
    """Drop-in replacement for piper.train.vits.monotonic_align's own
    maximum_path(neg_cent, mask) - identical signature/behavior, ported
    from that module's __init__.py (which is present and unmodified; only
    its `from .monotonic_align.core import maximum_path_c` line is
    unusable, since that compiled submodule doesn't exist)."""
    import torch

    device = neg_cent.device
    dtype = neg_cent.dtype
    neg_cent_np = neg_cent.data.cpu().numpy().astype(np.float32)
    path = np.zeros(neg_cent_np.shape, dtype=np.int32)

    t_t_max = mask.sum(1)[:, 0].data.cpu().numpy().astype(np.int32)
    t_s_max = mask.sum(2)[:, 0].data.cpu().numpy().astype(np.int32)
    maximum_path_c(path, neg_cent_np, t_t_max, t_s_max)
    return torch.from_numpy(path).to(device=device, dtype=dtype)
