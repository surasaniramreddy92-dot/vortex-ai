"""Document intelligence (Phase 7, scoped v1).

Reads PDF/DOCX/XLSX/TXT files by voice command ("read my resume", "summarize
notes.docx", "what does budget.xlsx say about Q3") using the same local
Ollama model VORTEX already talks to for everything else. No RAG/embeddings/
chunked retrieval yet - documents are read in full (truncated to fit
context) rather than indexed, which is the honest v1 scope for a personal
assistant handling a handful of files at a time, not a document corpus.
That fuller scope is Phase 5's RAG work, not duplicated here.
"""
import os
from pathlib import Path

MAX_CHARS = 12000  # keeps extracted text comfortably inside llama3.2:1b's context window
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
    return text[:MAX_CHARS]


def build_document_prompt(text, question):
    return (
        f'Document contents:\n{text}\n\n'
        f'Based only on the document above, {question}\n'
        'Answer concisely in a couple of spoken sentences.'
    )
