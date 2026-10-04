"""
[ MICROSOFT WORD EXTRACTION KERNEL ]
Dual-mode engine designed for maximum fidelity across modern and legacy Word formats.

PIPELINE:
1. XML Parsing (.docx): Primary tier. Reads the OpenXML parts directly to
   extract text from body paragraphs, tables, text boxes, headers and footers.
2. COM Automation (.doc): Legacy tier. For binary documents, the system triggers 
   Windows COM Automation (pywin32) to physically automate a local Word instance 
   for high-fidelity content recovery.

REQUIRES: Microsoft Word (for .doc support), pywin32.
"""

MANIFEST = {
    "id": "com.microsoft.word.standard",
    "version": "1.3.0",
    "name": "Microsoft Word Extractor",
    "extensions": ["docx", "doc"],
    "requires": ["lxml", "pywin32", "olefile", "psutil"]
}

__description__ = (
    "A comprehensive Microsoft Word engine supporting both modern XML (.docx) "
    "and legacy binary (.doc) formats. It features a dual-tier recovery strategy "
    "using native parsing and Windows COM automation for maximum accuracy."
)

import os
import re
import threading
import zipfile
from core import logger
from core.extractors.base import ExtractorContext


def _word_pids() -> set:
    import psutil
    pids = set()
    for p in psutil.process_iter(['name']):
        if (p.info.get('name') or '').lower() == 'winword.exe':
            pids.add(p.pid)
    return pids


def _dispatch_new_word():
    """A separate Word instance (DispatchEx), never the user's own Word."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    return win32com.client.DispatchEx("Word.Application")


def _kill_pid(pid: int) -> None:
    import psutil
    psutil.Process(pid).kill()


def _com_timeout() -> float:
    from core.settings import settings
    return float(settings.get('word:com_timeout') or 120)


def _extract_doc_legacy(file_path: str) -> tuple:
    """Extract text from old binary .doc files via Word COM automation.

    Word can stop at an invisible dialog and never return, which used to block
    the (single) extraction worker indefinitely. A watchdog kills only the Word
    instance started here if it doesn't finish within word:com_timeout seconds;
    killing it makes the pending COM call fail, so this thread continues.
    """
    word, our_pid, timed_out = None, None, threading.Event()
    timeout = _com_timeout()

    def _watchdog():
        timed_out.set()
        if our_pid:
            try:
                _kill_pid(our_pid)
            except Exception:
                pass

    timer = threading.Timer(timeout, _watchdog)
    try:
        before = _word_pids()
        word = _dispatch_new_word()
        new = _word_pids() - before
        our_pid = next(iter(new)) if len(new) == 1 else None   # never guess: kill nothing if unsure
        word.Visible = False
        word.DisplayAlerts = 0  # suppress repair dialog
        timer.start()

        doc = word.Documents.Open(os.path.abspath(file_path))
        text = doc.Content.Text
        doc.Save()  # write repairs back to the original file (user's choice, 2026-10-04)
        doc.Close()
        return text, None
    except Exception as e:
        if timed_out.is_set():
            return None, (f"Legacy .doc extraction failed: Word did not respond within "
                          f"{timeout:g}s (likely an invisible dialog); its instance was stopped")
        return None, f"Legacy .doc extraction failed (Word may not be installed): {e}"
    finally:
        timer.cancel()
        if word is not None and not timed_out.is_set():
            try:
                word.Quit()
            except Exception:
                if our_pid:
                    try:
                        _kill_pid(our_pid)
                    except Exception:
                        pass


_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_MC_FALLBACK = '{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback'
_PART = re.compile(r'^word/(header\d*|document|footer\d*|footnotes|endnotes)\.xml$')
_PART_ORDER = {'header': 0, 'document': 1, 'footer': 2, 'footnotes': 3, 'endnotes': 4}


def _docx_paragraphs(file_path: str) -> list:
    """Every paragraph's text in a .docx: body, tables, text boxes, headers,
    footers and notes. python-docx's doc.paragraphs only covers top-level body
    paragraphs, so table- or text-box-only documents looked empty."""
    from lxml import etree
    with zipfile.ZipFile(file_path) as z:
        parts = sorted((n for n in z.namelist() if _PART.match(n)),
                       key=lambda n: (_PART_ORDER[re.sub(r'\d', '', n[5:-4])], n))
        out = []
        for name in parts:
            root = etree.fromstring(z.read(name))
            # Text boxes are stored twice (mc:Choice + a VML mc:Fallback copy).
            for fb in root.iter(_MC_FALLBACK):
                fb.getparent().remove(fb)
            for p in root.iter(_W + 'p'):
                chunks = []
                for el in p.iter(_W + 't', _W + 'tab', _W + 'br', _W + 'cr'):
                    if next(el.iterancestors(_W + 'p')) is not p:
                        continue            # belongs to a paragraph nested in a text box
                    tag = el.tag[len(_W):]
                    chunks.append(el.text or '' if tag == 't' else '\t' if tag == 'tab' else '\n')
                text = ''.join(chunks).strip()
                if text:
                    out.append(text)
        return out


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Extract content from a Word file using native parsing or COM fallback.
    """
    logger.info(f"Extracting: {os.path.basename(file_path)}", ext="ms-word")
    try:
        ext = os.path.splitext(file_path)[1].lower()
        
        # Handle legacy .doc
        if ext == '.doc':
            text, err = _extract_doc_legacy(file_path)
            if not err:
                return text, None
            # Word refused it (File Block blocks Word 6/95 and 1.x/2.0 by default,
            # or the file is damaged): fall back to the built-in pre-97 reader.
            try:
                from core.legacy_doc import text_from_legacy_doc
                builtin = text_from_legacy_doc(file_path)
            except Exception:
                return None, err
            if not builtin:
                return None, err
            return builtin, None, {'reader': 'builtin', 'word_error': err[:300]}

        # Handle modern .docx
        paragraphs = _docx_paragraphs(file_path)
        if not paragraphs:
            return None, "No text found in document"
        return "\n\n".join(paragraphs), None
    except Exception as e:
        return None, f"Failed to extract Word document: {e}"
