"""Document intelligence (Phase 7) - file resolution and text extraction.

Reads PDF/DOCX/XLSX/TXT files by voice command ("read my resume", "summarize
notes.docx", "what does budget.xlsx say about Q3"). `extract_text` returns
the FULL text - truncation (for the simple summarize path) or chunking+
embedding (for the RAG-backed question-answering path, see rag.py) is a
decision for the caller, not this module.
"""
import os
from pathlib import Path

SEARCH_DIRS = [Path.home() / 'Desktop', Path.home() / 'Documents', Path.home() / 'Downloads']
SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.xls', '.txt', '.md'}


def resolve_document(name):
    """Find a file matching `name` (with or without extension) in common folders."""
    name = name.strip().strip('"\'').lower()
    candidates = []
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            stem, full = f.stem.lower(), f.name.lower()
            if name == full or name == stem or name in stem:
                candidates.append(f)
    if not candidates:
        return None
    exact = [c for c in candidates if c.stem.lower() == name]
    pool = exact or candidates
    return str(max(pool, key=lambda p: p.stat().st_mtime))


def extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        import fitz  # PyMuPDF
        with fitz.open(path) as doc:
            text = '\n'.join(page.get_text() for page in doc)
    elif ext == '.docx':
        import docx
        text = '\n'.join(p.text for p in docx.Document(path).paragraphs)
    elif ext in ('.xlsx', '.xls'):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        rows = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    rows.append(' | '.join(cells))
        text = '\n'.join(rows)
    else:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
    return text


def build_document_prompt(text, question):
    return (
        f'Document contents:\n{text}\n\n'
        f'Based only on the document above, {question}\n'
        'Answer concisely in a couple of spoken sentences.'
    )
