"""AGC (automatic gain control) with noise-floor gating.

Extracted verbatim from Vortex._agc / self.noise_floor (docs/REFACTOR_PLAN.md
Step 3) - identical math, identical state, just moved off the Vortex
god-object into its own small stateful object. No behavior change.
"""
import numpy as np


class AudioProcessor:
    """Boost voice-level audio toward a target RMS before wake-word inference.
    Tracks a slow-moving ambient noise floor and only boosts frames that stand
    out above it - steady background noise gets left alone (and folded into
    the floor estimate) instead of amplified into a false wake trigger.

    noise_floor starts at a hardcoded 250.0 and only decays back toward the
    true ambient floor via a slow exponential average (2%/update) on purpose
    (see CHANGELOG.md 2026-08-16) - starting too low would let the floor get
    permanently stuck, boosting noise into false triggers.
    """

    def __init__(self, target_rms, max_gain, noise_margin, initial_noise_floor=250.0):
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.noise_margin = noise_margin
        self.noise_floor = initial_noise_floor

    def boost(self, audio_i16):
        rms = np.sqrt(np.mean(audio_i16.astype(np.float64) ** 2))
        if rms < self.noise_floor * self.noise_margin:
            self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms
            return audio_i16
        gain = min(self.max_gain, self.target_rms / rms)
        if gain <= 1.0:
            return audio_i16
        return np.clip(audio_i16.astype(np.float64) * gain, -32768, 32767).astype(np.int16)
