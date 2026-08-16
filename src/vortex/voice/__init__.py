"""VORTEX voice subsystem - wake detection, TTS, STT, barge-in, session loop.

Extracted from the Vortex god-object in main.py, per docs/REFACTOR_PLAN.md
Step 3. Mechanical extraction only: the algorithms (AGC math, chunking,
barge-in signal flow, capture VAD) are unchanged from the versions verified
live on 2026-08-16 (see CHANGELOG.md) - only their location moved.
"""
