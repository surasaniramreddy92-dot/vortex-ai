"""Unit tests for the 2026-08-16 Phase 7 additions to src/vortex/documents.py:
OCR fallback for scanned/image-only PDF pages, and page/section-preserving
extraction (extract_pages) for provenance.

No live Tesseract/Postgres/Qdrant dependency - PDF fixtures are built
in-memory with PyMuPDF itself (a real PDF with a real text page and a real
blank page, standing in for "no text layer" - PyMuPDF's get_text() returns
'' for both a blank page and an unrendered scanned image page, so this
exercises the actual detection heuristic, not a mock of it), and the OCR
binary call itself is mocked where its result matters (there is no
Tesseract binary on this dev machine as of this writing - see IMPLEMENTED.md).

Run with: pytest tests/unit/test_documents.py
"""
import os
import tempfile
from unittest.mock import patch

import pytest

fitz = pytest.importorskip('fitz', reason='PyMuPDF not installed')
docx = pytest.importorskip('docx', reason='python-docx not installed')
openpyxl = pytest.importorskip('openpyxl', reason='openpyxl not installed')

from vortex import documents


@pytest.fixture(autouse=True)
def reset_ocr_probe_cache():
    """_ocr_available() caches its result at module level (deliberately - the
    probe itself shouldn't re-run per page). Reset both caches around every
    test so tests don't leak state into each other via that cache."""
    documents._ocr_status = None
    documents._pdf_pages_cache.clear()
    yield
    documents._ocr_status = None
    documents._pdf_pages_cache.clear()


def _make_pdf(pages_text):
    """pages_text: list of str, one per page ('' for a blank/no-text-layer page)."""
    path = tempfile.mktemp(suffix='.pdf')
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def test_page_with_real_text_is_not_ocrd():
    """A page with plenty of native text should never trigger an OCR attempt -
    OCR is a fallback for near-empty pages, not a default for every page."""
    path = _make_pdf(['This is a real page of text with plenty of content, well over the threshold.'])
    try:
        with patch.object(documents, '_ocr_page') as mock_ocr:
            pages = documents.extract_pages(path)
        mock_ocr.assert_not_called()
        assert len(pages) == 1
        assert pages[0]['page'] == 1
        assert 'real page of text' in pages[0]['text']
    finally:
        os.remove(path)


def test_near_empty_page_triggers_ocr_attempt_when_available():
    """A blank/near-empty page (standing in for a scanned image page - no text
    layer either way) should trigger an OCR attempt when OCR is available,
    and the OCR'd text should replace the empty native text."""
    path = _make_pdf(['Real text on page one, long enough to not be a candidate.', ''])
    try:
        with patch.object(documents, '_ocr_status', True), \
             patch.object(documents, '_ocr_page', return_value='Recovered scanned text') as mock_ocr:
            pages = documents.extract_pages(path)
        mock_ocr.assert_called_once()
        assert pages[0]['text'].strip() != ''  # untouched real text
        assert pages[1]['text'] == 'Recovered scanned text'
    finally:
        os.remove(path)


def test_near_empty_page_degrades_gracefully_when_ocr_unavailable():
    """When Tesseract isn't available (the actual state of this dev machine
    today - see IMPLEMENTED.md), a near-empty page must NOT crash extraction;
    it should fall back to keeping whatever native text there was (here,
    none), and _ocr_page must never be called."""
    path = _make_pdf(['Real text on page one, long enough to not be a candidate.', ''])
    try:
        with patch.object(documents, '_ocr_status', False), \
             patch.object(documents, '_ocr_page') as mock_ocr:
            pages = documents.extract_pages(path)
        mock_ocr.assert_not_called()
        assert pages[1]['text'] == ''  # degraded gracefully, no exception
    finally:
        os.remove(path)


def test_ocr_available_false_when_binary_missing(monkeypatch):
    """_ocr_available() must check for the Tesseract *binary* explicitly, not
    just the pytesseract Python package - installing the wrapper alone must
    not be treated as OCR being usable."""
    monkeypatch.setattr('shutil.which', lambda name: None)
    assert documents._ocr_available() is False


def test_ocr_available_false_when_disabled_via_config(monkeypatch):
    monkeypatch.setattr(documents, 'OCR_ENABLED', False)
    assert documents._ocr_available() is False


def test_extract_text_pdf_unaffected_by_ocr_when_all_pages_have_text():
    """extract_text (the flat-string, backward-compatible API used by the
    plain-summarize path) should behave exactly as before for a normal,
    fully-text PDF - no markers, no structural change."""
    path = _make_pdf(['Page one content here.', 'Page two content here.'])
    try:
        text = documents.extract_text(path)
        assert 'Page one content' in text
        assert 'Page two content' in text
    finally:
        os.remove(path)


def test_extract_pages_docx_groups_by_heading():
    path = tempfile.mktemp(suffix='.docx')
    d = docx.Document()
    d.add_heading('Introduction', level=1)
    d.add_paragraph('This is the intro paragraph text.')
    d.add_heading('Budget', level=1)
    d.add_paragraph('Q3 budget was 500000 dollars.')
    d.save(path)
    try:
        sections = documents.extract_pages(path)
        by_heading = {s['section']: s['text'] for s in sections}
        assert 'Introduction' in by_heading
        assert 'intro paragraph' in by_heading['Introduction']
        assert 'Budget' in by_heading
        assert '500000' in by_heading['Budget']
        assert all(s['page'] is None for s in sections)
    finally:
        os.remove(path)


def test_extract_pages_docx_with_no_headings_returns_single_section():
    path = tempfile.mktemp(suffix='.docx')
    d = docx.Document()
    d.add_paragraph('Just a plain paragraph, no headings anywhere in this file.')
    d.save(path)
    try:
        sections = documents.extract_pages(path)
        assert len(sections) == 1
        assert sections[0]['section'] is None
        assert 'plain paragraph' in sections[0]['text']
    finally:
        os.remove(path)


def test_extract_pages_xlsx_one_section_per_sheet():
    path = tempfile.mktemp(suffix='.xlsx')
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = 'Summary'
    ws1.append(['Revenue', 100000])
    ws2 = wb.create_sheet('Details')
    ws2.append(['Item', 'Cost'])
    wb.save(path)
    try:
        sections = documents.extract_pages(path)
        names = {s['section'] for s in sections}
        assert names == {'Summary', 'Details'}
    finally:
        os.remove(path)


def test_extract_pages_txt_has_no_page_or_section():
    path = tempfile.mktemp(suffix='.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('Plain text file with no structure.')
    try:
        sections = documents.extract_pages(path)
        assert len(sections) == 1
        assert sections[0]['page'] is None
        assert sections[0]['section'] is None
        assert 'Plain text file' in sections[0]['text']
    finally:
        os.remove(path)
