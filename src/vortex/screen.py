"""Voice-triggered screen reading ("read my screen") - screenshot + OCR,
spoken back. Added 2026-08-17, direct user request.

Reuses documents.py's OCR probe/dependency (pytesseract + the separately-
installed Tesseract binary) rather than a second copy of the same "is OCR
actually usable" check - same honest contract: degrades to a clear spoken
message if Tesseract isn't installed, never silently claims it read
something it didn't.
"""
import logging

from . import documents

_log = logging.getLogger('vortex.screen')

# A screen full of UI chrome/whitespace can OCR down to a handful of stray
# characters - not worth reading back as if it were real content. Mirrors
# documents.py's OCR_MIN_TEXT_CHARS reasoning at a slightly higher bar,
# since a screen (icons, taskbar, window borders) has more OCR-noise
# potential than a scanned document page.
MIN_USEFUL_TEXT_CHARS = 15


def capture_screen():
    """Returns a PIL Image of the current screen (all monitors combined on
    Windows, matching what ImageGrab.grab() gives by default), or None if
    capture failed for any reason - a screenshot failure should degrade to
    "couldn't read the screen," not crash the caller."""
    try:
        from PIL import ImageGrab
        return ImageGrab.grab()
    except Exception as e:
        _log.warning(f'Screen capture failed: {type(e).__name__}: {e}')
        return None


def read_screen_text():
    """Screenshots the current screen and OCRs it. Returns (text, error) -
    text is None if OCR genuinely couldn't run or found nothing useful;
    error is a ready-to-speak explanation for that case, None on success.
    Never raises - every failure mode (no Tesseract, capture failure, OCR
    exception) resolves to a clear spoken message instead."""
    if not documents._ocr_available():
        return None, (
            "I can't read the screen - that needs Tesseract OCR installed, "
            "which isn't set up on this machine yet.")
    img = capture_screen()
    if img is None:
        return None, "I couldn't capture the screen."
    try:
        import pytesseract
        text = pytesseract.image_to_string(img)
    except Exception as e:
        _log.warning(f'Screen OCR failed: {type(e).__name__}: {e}')
        return None, "I captured the screen but couldn't read the text on it."
    text = ' '.join(text.split())  # collapse OCR's line breaks/whitespace into one spoken run
    if len(text) < MIN_USEFUL_TEXT_CHARS:
        return None, "I don't see any readable text on the screen right now."
    return text, None
