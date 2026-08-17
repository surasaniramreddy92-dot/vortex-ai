"""Unit tests for src/vortex/screen.py ("read my screen", added 2026-08-17).

Mirrors documents.py's OCR-availability test pattern: no real screen
capture or Tesseract binary needed for these - what's under test is the
degrade-gracefully contract (never raises, always returns a clear spoken
message on failure) and the text-cleanup logic.
"""
import sys

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex import screen


def test_read_screen_text_when_ocr_unavailable(monkeypatch):
    monkeypatch.setattr('vortex.documents._ocr_available', lambda: False)
    text, err = screen.read_screen_text()
    assert text is None
    assert 'Tesseract' in err


def test_read_screen_text_when_capture_fails(monkeypatch):
    monkeypatch.setattr('vortex.documents._ocr_available', lambda: True)
    monkeypatch.setattr(screen, 'capture_screen', lambda: None)
    text, err = screen.read_screen_text()
    assert text is None
    assert "couldn't capture" in err


def test_read_screen_text_when_ocr_raises(monkeypatch):
    monkeypatch.setattr('vortex.documents._ocr_available', lambda: True)
    monkeypatch.setattr(screen, 'capture_screen', lambda: object())

    class BoomPytesseract:
        @staticmethod
        def image_to_string(img):
            raise RuntimeError('tesseract crashed')

    sys.modules['pytesseract'] = BoomPytesseract()
    try:
        text, err = screen.read_screen_text()
    finally:
        del sys.modules['pytesseract']
    assert text is None
    assert "couldn't read the text" in err


def test_read_screen_text_rejects_near_empty_result(monkeypatch):
    """A screen that OCRs down to almost nothing (icons, taskbar, no real
    body text) should be reported as "nothing readable," not spoken back as
    a few stray characters."""
    monkeypatch.setattr('vortex.documents._ocr_available', lambda: True)
    monkeypatch.setattr(screen, 'capture_screen', lambda: object())

    class SparsePytesseract:
        @staticmethod
        def image_to_string(img):
            return '  a  \n b \n'

    sys.modules['pytesseract'] = SparsePytesseract()
    try:
        text, err = screen.read_screen_text()
    finally:
        del sys.modules['pytesseract']
    assert text is None
    assert "don't see any readable text" in err


def test_read_screen_text_success_collapses_whitespace(monkeypatch):
    monkeypatch.setattr('vortex.documents._ocr_available', lambda: True)
    monkeypatch.setattr(screen, 'capture_screen', lambda: object())

    class RealPytesseract:
        @staticmethod
        def image_to_string(img):
            return 'This is\n\na real screen   full of\ttext content here.'

    sys.modules['pytesseract'] = RealPytesseract()
    try:
        text, err = screen.read_screen_text()
    finally:
        del sys.modules['pytesseract']
    assert err is None
    assert text == 'This is a real screen full of text content here.'


def test_capture_screen_returns_none_on_failure(monkeypatch):
    import PIL.ImageGrab

    def boom():
        raise RuntimeError('no display')
    monkeypatch.setattr(PIL.ImageGrab, 'grab', boom)
    assert screen.capture_screen() is None
