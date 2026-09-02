# Training a custom VORTEX voice

Direct user request (2026-09-02): a real, custom-trained voice on the
owner's own voice, not just prosody tuning on top of a commercial cloud
voice (see `IMPLEMENTED.md`'s Phase 1 row for that separate, earlier work).

**Read this whole file before recording anything.** It's short, and it
sets honest expectations this project has held itself to throughout: what
this actually produces, what it costs in time, and what's still not done
after this stage.

## Honest scope

This machine has no NVIDIA GPU (Intel integrated graphics only). Training
a real generative voice model on CPU is dramatically slower than on GPU -
expect **many hours to overnight** per training attempt, not minutes. This
is a genuine, deliberate trade-off the project owner chose, accepting a
**working first pass, not a polished result** - the same honest framing
`tools/wakeword/build_hey_vortex.py`'s own docstring uses for VORTEX's
other self-trained model.

**Legal constraint, non-negotiable:** train only on your own recorded
voice. Do not attempt to clone Emma, Ava, or any other commercial/
third-party voice - that's a real ToS/legal problem, not just a technical
one.

## What's real right now

- The training pipeline genuinely works end to end on this machine -
  verified with a real (fake-data) training step, not assumed. See "Two
  real bugs found and fixed" below - this took real debugging, not a
  clean `pip install` and go.
- `script.py` - 155 hand-composed, phonetically varied sentences (~8-12
  minutes of pure reading, likely 15-20 minutes wall-clock with natural
  pauses). Not a claim these alone produce a great voice - a genuine first
  milestone, extensible later (see "Recording more later" below).
- `record.py` - run this yourself; there is no way for Claude to
  participate in live audio recording.
- Nothing here touches `src/vortex/` yet. The actual runtime integration
  point already exists (`VORTEX_OFFLINE_TTS_VOICE`/
  `VORTEX_OFFLINE_TTS_MODEL_DIR`, see `config.py`) and needs zero code
  changes - it's a config change once a trained voice actually exists.

## Step 1 - install the extra

```
pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[voice-training]"
```

The first line matters: plain `pip install -e ".[voice-training]"` alone
pulls torch's default wheel, which bundles CUDA redistributables even
though this machine has no NVIDIA GPU to use them - the CPU-only wheel
above is meaningfully smaller. If torch is already installed (any build),
pip will skip reinstalling it and just add the rest of the extra.

## Step 2 - record

```
python tools/voice_training/record.py
```

Reads `script.py`'s sentences one at a time. Press Enter to start
recording, read the sentence naturally, press Enter again to stop.
Resumable - already-recorded sentences are skipped on a re-run, so this
can be spread across multiple sessions. To redo a sentence, delete its
line from `dataset/metadata.csv` and its file from `dataset/wavs/`, then
re-run.

Record somewhere reasonably quiet. You don't need studio conditions, but
consistent background noise (not silence, just *consistent*) across all
recordings matters more than any single recording being perfect.

## Step 3 - train

```
python tools/voice_training/train.py fit \
  --data.csv_path tools/voice_training/dataset/metadata.csv \
  --data.audio_dir tools/voice_training/dataset/wavs \
  --data.cache_dir tools/voice_training/dataset/cache \
  --data.espeak_voice en-us \
  --data.config_path tools/voice_training/dataset/config.json \
  --data.voice_name <pick a name, e.g. your first name> \
  --data.batch_size 8 \
  --trainer.accelerator cpu \
  --trainer.default_root_dir tools/voice_training/runs
```

**Use `train.py`, not `python -m piper.train` directly** - it applies two
real compatibility patches piper-tts==1.7.0 needs on this machine (see
below). Leave this running - Lightning checkpoints periodically to
`tools/voice_training/runs/` (`--trainer.default_root_dir`), so it's safe
to stop and resume later by adding `--ckpt_path <path to a .ckpt file>` to
the same command.

There is no fixed "done" epoch count - training runs open-ended by
design (this is Piper's own choice, not something disabled here) because
mel-reconstruction loss (`val_mel`, which IS logged and checkpointed on)
saturates early while the adversarial losses keep removing audible
artifacts for a while longer. In practice: let it run for a good while
(hours), then try a checkpoint (Step 4) and judge by ear whether it's
worth continuing.

**Real limitation, not hidden:** the automated `val_mos` quality proxy
Piper supports (a UTMOS-based predictor, useful for narrowing down
checkpoints without listening to every single one) needs `torchaudio`,
which currently has no release matching `torch==2.14.0` - installing a
mismatched pair risked destabilizing the pipeline this took real work to
get working at all, so it was deliberately skipped. Checkpoint selection
for now relies on `val_mel` plus your own ears, same as the plan always
intended for final judgment anyway.

## Step 4 - export and try it

```
python -m piper.train.export_onnx tools/voice_training/runs/<checkpoint>.ckpt tools/voice_training/exported/<voice_name>.onnx
```

(Copy `dataset/config.json` alongside the exported `.onnx` as
`<voice_name>.onnx.json` - Piper's runtime expects both files.) Then point
VORTEX at it:

```
VORTEX_OFFLINE_TTS_VOICE=<voice_name>
VORTEX_OFFLINE_TTS_MODEL_DIR=<path to the exported/ directory>
```

This makes it VORTEX's **offline fallback** voice (today's offline path is
fallback-only by design, engaged only when the primary cloud voice can't
be reached). Promoting a finished custom voice to the *primary* voice is a
separate, later decision once one actually sounds good enough - not
something to decide before a real voice exists to judge.

## Recording more later

Nothing here is a one-shot commitment. Run `record.py` again anytime to
add more sentences (add new ones to `script.py`, or increase
`MIN_DURATION_SECONDS`-passing re-takes of existing ones), then resume
training with `--ckpt_path` pointing at your latest checkpoint. More real
data is the single biggest lever on quality - more so than any
hyperparameter here.

## Two real bugs found and fixed while setting this up

Both confirmed by actually running the pipeline against a throwaway fake
dataset (`prepare_smoke_test.py`), not assumed from reading the source:

1. **`SileroVoiceActivityDetector.process_array` doesn't exist.**
   `piper/train/vits/dataset.py`'s silence-trimming step calls a method
   name that isn't in any currently-published `pysilero-vad` release
   (checked 3.4.0 and 3.0.0 directly). `process_samples` takes the same
   argument, returns the same result - `train.py` patches this in at
   import time.
2. **The Cython source for monotonic alignment is missing from the PyPI
   wheel.** `piper/train/vits/models.py` needs a compiled
   `monotonic_align.core` extension: the `.pyx` source it's built from is
   simply not included in `piper-tts` on PyPI (checked 1.7.0 and 1.6.1
   directly - `setup.py`/`__init__.py` are there, `core.pyx` is not). No C
   compiler would have fixed this - there was nothing to compile. Fetched
   the real source directly from the project's own GitHub repository (via
   a raw HTTP request, not an LLM-summarizing fetch - algorithmic source
   has to be exact, not paraphrased) and ported it line-for-line to Numba
   (`_monotonic_align_numba.py` - already a project dependency via
   `librosa`, so no new install needed), verified against hand-computable
   test cases (`test_monotonic_align_numba.py`), and registered as a
   substitute for the broken import.

Neither is a workaround papering over uncertainty - both are confirmed,
understood bugs in the published package, patched with verified-correct
replacements, not guesses.
