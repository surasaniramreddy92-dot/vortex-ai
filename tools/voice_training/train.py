"""Launches Piper's real training CLI (python -m piper.train) against a
dataset prepared by record.py (or prepare_smoke_test.py) - see README.md
for the full workflow and exact commands to run.

Two real compatibility issues in piper-tts==1.7.0's published package,
both confirmed by actually running the pipeline and inspecting the
installed package directly, not assumed or guessed around:

1. **VAD API drift.** `piper/train/vits/dataset.py`'s `_trim_silence` calls
   `SileroVoiceActivityDetector.process_array(chunk)`, but that method does
   not exist in any currently-published pysilero-vad release (checked
   3.4.0, the latest, and 3.0.0 directly - neither has it). The installed
   API's `process_samples(samples) -> float` takes the same argument shape
   (a numpy array chunk) and returns the same result shape (a float speech
   probability) - verified directly before relying on it.

2. **Missing Cython source.** `piper/train/vits/models.py`'s forward()
   lazily does `from . import monotonic_align`, which unconditionally
   tries to import a compiled `monotonic_align.core` submodule - but the
   `core.pyx` source needed to build that submodule is simply not included
   in the piper-tts wheel (checked 1.7.0 and 1.6.1 directly: `setup.py`/
   `__init__.py` are present, `core.pyx` is not, in either). No C compiler
   would fix this - there is nothing to compile. See
   `_monotonic_align_numba.py`'s own docstring for the fix: a Numba port
   of the exact algorithm, fetched byte-for-byte from the project's real
   GitHub source (not reimplemented from memory), pre-registered in
   sys.modules so models.py's lazy import picks it up instead of the
   broken one.

Run this instead of `python -m piper.train` directly so both patches are
always applied. All arguments after the script name are passed straight
through to Piper's LightningCLI exactly as if you'd run
`python -m piper.train <args>` yourself - see README.md for the actual
commands (smoke test vs. real training).
"""
import sys
import types

from pysilero_vad import SileroVoiceActivityDetector

if not hasattr(SileroVoiceActivityDetector, 'process_array'):
    SileroVoiceActivityDetector.process_array = SileroVoiceActivityDetector.process_samples

import _monotonic_align_numba  # noqa: E402

_fake_monotonic_align = types.ModuleType('piper.train.vits.monotonic_align')
_fake_monotonic_align.maximum_path = _monotonic_align_numba.maximum_path
sys.modules['piper.train.vits.monotonic_align'] = _fake_monotonic_align

from piper.train.__main__ import main  # noqa: E402

if __name__ == '__main__':
    sys.argv[0] = 'piper.train'
    main()
