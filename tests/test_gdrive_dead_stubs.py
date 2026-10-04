"""A .gdoc/.gsheet stub whose Drive document no longer exists is not an error.

407 stubs failed with Drive 404 'File not found': documents moved from
saltheart.foamfollower@ to albert.g.lotito@ (new IDs), so the old stubs are
dead pointers. The user chose to keep the files and mark them done.
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from extractors import gdrive_extractor as gx


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(vault_id='t', file_hash='t', cancel_token=threading.Event(),
                            logger=MagicMock(spec=ExtractorLogger), settings=MagicMock())


def _http_error(status, reason):
    from googleapiclient.errors import HttpError
    resp = MagicMock(status=status, reason=reason)
    body = json.dumps({'error': {'code': status, 'errors': [{'reason': reason}], 'message': reason}}).encode()
    return HttpError(resp, body)


@pytest.fixture
def stub(tmp_path):
    p = tmp_path / 'Old notes.gdoc'
    p.write_text(json.dumps({'doc_id': 'DEAD123', 'email': 'saltheart.foamfollower@gmail.com',
                             'url': 'https://docs.google.com/open?id=DEAD123'}), encoding='utf-8')
    return str(p)


def _service_raising(err):
    svc = MagicMock()
    svc.files.return_value.get.return_value.execute.side_effect = err
    return svc


def test_document_gone_from_drive_is_done_not_an_error(stub):
    with patch.object(gx, 'is_authorized', return_value=True), \
         patch.object(gx, '_build_service', return_value=_service_raising(_http_error(404, 'notFound'))):
        text, error, meta = gx.extract(stub, _ctx())
    assert (text, error) == (None, None)
    assert meta == {'drive_status': 'not_found', 'doc_id': 'DEAD123',
                    'owner': 'saltheart.foamfollower@gmail.com'}


def test_permission_denied_is_still_an_error(stub):
    with patch.object(gx, 'is_authorized', return_value=True), \
         patch.object(gx, '_build_service', return_value=_service_raising(_http_error(403, 'forbidden'))):
        result = gx.extract(stub, _ctx())
    assert result[0] is None and 'Drive API error' in result[1]


def test_network_failure_is_still_an_error(stub):
    with patch.object(gx, 'is_authorized', return_value=True), \
         patch.object(gx, '_build_service', return_value=_service_raising(OSError('connection reset'))):
        result = gx.extract(stub, _ctx())
    assert result[0] is None and 'connection reset' in result[1]


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_worker_marks_dead_stub_completed(mock_router, mock_complete):
    from workers import extraction_worker
    k = MagicMock(); k.__name__ = 'gdrive_extractor'
    k.extract.return_value = (None, None, {'drive_status': 'not_found', 'doc_id': 'DEAD123'})
    mock_router.return_value = [k]
    extraction_worker.process_task('t.db', {'file_hash': 'h', 'file_path': 'x.gdoc', 'file_type': 'gdoc'})
    assert mock_complete.call_args[1]['status'] == 'COMPLETED'
    assert mock_complete.call_args[1]['error'] is None
