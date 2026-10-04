"""Word COM must never hang the extraction pipeline.

A Word 97 file ('Ancient Tales for Modern Times.doc') made the hidden Word
instance stop at an invisible dialog; Documents.Open never returned, and with no
timeout the single extraction worker (and so ALL extraction) stalled ~10 min until
the instance was killed by hand. Failures also skipped word.Quit(), leaving
hidden WINWORD.EXE processes behind.
"""
import threading
import time
from unittest.mock import MagicMock, patch

from extractors import microsoft_word_extractor as wx


class FakeWord:
    """Word.Application stand-in whose Documents.Open can block until 'killed'."""
    def __init__(self, hang=False, text='Hello from Word', open_error=None):
        self.killed = threading.Event()
        self.quit_called = False
        self.Visible = True
        self.DisplayAlerts = 1
        doc = MagicMock(); doc.Content.Text = text
        def _open(path, *a, **kw):
            if open_error:
                raise open_error
            if hang:
                self.killed.wait(10)
                raise RuntimeError('The remote procedure call failed.')
            return doc
        self.Documents = MagicMock(); self.Documents.Open.side_effect = _open
    def Quit(self):
        self.quit_called = True


def _run(fake, timeout=1):
    pids = iter([{100}, {100, 555}])                    # before / after starting our instance
    killed = []
    def kill(pid):
        killed.append(pid); fake.killed.set()
    with patch.object(wx, '_word_pids', side_effect=lambda: next(pids)), \
         patch.object(wx, '_dispatch_new_word', return_value=fake), \
         patch.object(wx, '_kill_pid', side_effect=kill), \
         patch.object(wx, '_com_timeout', return_value=timeout):
        t0 = time.time()
        result = wx._extract_doc_legacy('x.doc')
    return result, killed, time.time() - t0


def test_normal_extraction_returns_text_and_closes_word():
    fake = FakeWord()
    (text, err), killed, _ = _run(fake)
    assert (text, err) == ('Hello from Word', None)
    assert fake.quit_called and killed == []


def test_hung_word_is_killed_after_the_timeout_and_reported():
    fake = FakeWord(hang=True)
    (text, err), killed, elapsed = _run(fake, timeout=1)
    assert text is None
    assert 'did not respond within 1s' in err
    assert killed == [555]                              # only the instance we started
    assert elapsed < 5


def test_failed_open_still_closes_word():
    fake = FakeWord(open_error=RuntimeError('blocked by your File Block settings'))
    (text, err), killed, _ = _run(fake)
    assert text is None and 'File Block' in err
    assert fake.quit_called


def test_users_own_word_is_never_killed():
    """If no new WINWORD process can be identified, the watchdog kills nothing."""
    fake = FakeWord(hang=True)
    with patch.object(wx, '_word_pids', return_value={100}), \
         patch.object(wx, '_dispatch_new_word', return_value=fake), \
         patch.object(wx, '_kill_pid') as kill, \
         patch.object(wx, '_com_timeout', return_value=0.5):
        threading.Timer(1.5, fake.killed.set).start()   # unblock the fake eventually
        text, err = wx._extract_doc_legacy('x.doc')
    kill.assert_not_called()
    assert text is None
