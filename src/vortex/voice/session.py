"""Active-session follow-up loop and the worker's outer event loop - the
state machine deciding wake vs. barge-in vs. timeout vs. watchdog recovery.

Extracted from Vortex._active_session and _worker (docs/REFACTOR_PLAN.md
Step 3). Preserves, verbatim:
  - the `stop_speaking` check at the very top of active_session()'s loop -
    THE fix for the bug where barge-in took 15-25+ seconds to register (see
    CHANGELOG.md 2026-08-16, fourth pass). Without it, this loop would start
    a brand-new full-length capture_command() window instead of returning
    control to worker(), which is the only place that speaks the
    acknowledgment and clears stop_speaking for the next turn.
  - the distinct barge-in acknowledgment ("Yes Boss, I'm listening.") vs. the
    fresh-wake greeting ("Yes Boss?"), keyed on event == 'barge_in'.
  - the wake-stream watchdog in the idle branch of worker()'s loop.

Dependencies (capture_command, execute, speak, greet, warm_up, and the
awaiting-confirmation clear) are injected as callables rather than importing
Vortex directly, so this module doesn't depend on the god-object it was
extracted from. app.py wires these to `self.<name>` lookups on the Vortex
instance at call time (not pre-bound method references captured once) so
that instance-level monkeypatching - e.g. tools/test_barge_in.py's worker-
dispatch scenario, which overrides `v.speak`/`v.capture_command`/`v.execute`/
`v.greet` before starting the worker thread - keeps behaving exactly as it
did before this extraction.
"""
import contextlib
import queue
import threading
import time


class Session:
    def __init__(self, *, events, barge_in, session_timeout, wake_watchdog_timeout,
                 capture_command, execute, speak, greet, warm_up, get_last_audio_at,
                 recover_wake_stream, is_capturing, clear_awaiting_confirmation,
                 log, is_running):
        self.events = events
        self.barge_in = barge_in
        self.session_timeout = session_timeout
        self.wake_watchdog_timeout = wake_watchdog_timeout
        self.capture_command = capture_command
        self.execute = execute
        self.speak = speak
        self.greet = greet
        self.warm_up = warm_up
        self.get_last_audio_at = get_last_audio_at
        self.recover_wake_stream = recover_wake_stream
        self.is_capturing = is_capturing
        self.clear_awaiting_confirmation = clear_awaiting_confirmation
        self.log = log
        self.is_running = is_running
        # docs/REFACTOR_PLAN.md Step 7: the only piece of state genuinely
        # missing before core/state_manager.py could answer "is VORTEX
        # inside an active-session follow-up window" - purely additive,
        # set/cleared around the *outside* of the loop below, touching none
        # of the barge-in-critical timing logic inside it.
        self.in_active_session = threading.Event()

    def active_session(self):
        """Keep listening for follow-ups (confirmations, next command) without
        requiring the wake word again, until session_timeout of silence.

        allow_offline_on_unclear is True only for the first capture_command()
        call here (right after the wake/barge-in acknowledgment - a
        deliberate, intentional "Hey Vortex" from the user) and False for
        every continuation call after that. Found live 2026-08-17: letting
        every follow-up capture try the offline STT "second opinion" on
        audio Google couldn't parse meant ambient noise during passive
        listening (no fresh wake, just this loop still open) could get
        fabricated into a plausible-sounding command, get answered, and keep
        the session alive for another window - a real, observed cascade of
        entirely unprompted responses. See stt.py's capture_command
        docstring for the full mechanism."""
        self.in_active_session.set()
        try:
            first = True
            while self.is_running():
                if self.barge_in.stop_speaking.is_set():
                    return
                cmd = self.capture_command(timeout=self.session_timeout,
                                            allow_offline_on_unclear=first)
                first = False
                if cmd is None:
                    self.log('Session timed out, returning to standby')
                    self.clear_awaiting_confirmation()
                    return
                self.execute(cmd)
        finally:
            self.in_active_session.clear()

    def worker(self):
        """Owns every slow operation: speaking, listening, executing."""
        time.sleep(1.5)
        self.barge_in.stop_speaking.clear()
        threading.Thread(target=self.warm_up, daemon=True).start()
        self.greet()
        while self.is_running():
            try:
                event = self.events.get(timeout=0.5)
            except queue.Empty:
                if (not self.is_capturing()
                        and time.monotonic() - self.get_last_audio_at() > self.wake_watchdog_timeout):
                    self.log(f'Wake stream watchdog: no audio for over {self.wake_watchdog_timeout}s, rebuilding stream')
                    self.recover_wake_stream()
                continue
            while not self.events.empty():
                with contextlib.suppress(queue.Empty):
                    self.events.get_nowait()
            if not self.is_running():
                break
            self.barge_in.stop_speaking.clear()
            if event == 'barge_in':
                # Speech is already cut. Drop any pending prompt and take the new order.
                self.log('Barge-in: yielding the floor')
                self.clear_awaiting_confirmation()
                # Distinct from the fresh-wake greeting on purpose - "Yes Boss?"
                # alone, right after VORTEX's own sentence was just cut off
                # mid-word, reads ambiguously (did it hear the interruption, or
                # is this a coincidence?). "Yes Boss, I'm listening" is
                # unambiguous: it specifically confirms the cutoff registered.
                self.speak("Yes Boss, I'm listening.")
            else:
                # Always spoken on a fresh wake too: silence alone gave no
                # audible confirmation VORTEX was actually listening.
                self.speak('Yes Boss?')
            self.active_session()
