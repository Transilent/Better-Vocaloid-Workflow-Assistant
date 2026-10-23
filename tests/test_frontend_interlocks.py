"""Preview has its own cancellation and participates in all resource gates."""
import os,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use isolated runner'
from PyQt5.QtWidgets import QApplication
from app import Window
from results_ui import active_task_operations
from storage_ui import StoragePage
from updates_ui import UpdatesPage
import storage
application=QApplication([])
with tempfile.TemporaryDirectory(dir=APP/'work',prefix='preview-guards-') as folder:
    root=Path(folder);job=root/'jobs/a';job.mkdir(parents=True);other=root/'jobs/b';other.mkdir()
    video=SimpleNamespace(busy=lambda:False,preview_busy=lambda:True,
                          preview_inputs={'source_job':str(job),'video':str(job/'video/source.mp4')},
                          fields={'video':str(other/'video/source.mp4')},cancel_preview=Mock())
    owner=SimpleNamespace(video_mixdown=video,processing_active=False,components=None,
                          storage=None,updates=None,publishing=None,calibration_dialog=None)
    assert active_task_operations(owner,job)==('视频预览',)
    assert active_task_operations(owner,other)==()
    assert '视频预览' in StoragePage.other_operations(SimpleNamespace(owner=owner))
    assert UpdatesPage.active_operations(SimpleNamespace(owner=owner,busy=lambda:False))
    event=Mock();Window.closeEvent(SimpleNamespace(video_mixdown=video),event)
    event.ignore.assert_called_once();video.cancel_preview.assert_called_once()
    video.preview_busy=lambda:False;video.preview_inputs={}
    assert active_task_operations(owner,job)==()
    assert '视频预览' not in StoragePage.other_operations(SimpleNamespace(owner=owner))
    assert not UpdatesPage.active_operations(SimpleNamespace(owner=owner,busy=lambda:False))
    preview=root/'cache/video-preview';owned=preview/('preview-'+'a'*32);owned.mkdir(parents=True)
    (owned/'still.png').write_bytes(b'cached preview')
    with patch.object(storage,'ROOT',root),patch.object(storage,'job_directories',lambda:[]), \
         patch('task_contract.protected_references',return_value=[]), \
         patch.object(storage,'locked',lambda p:Path(p)==owned):
        try:storage.clean([preview]);raise AssertionError('Active preview cleanup was allowed')
        except ValueError as error:assert '预览' in str(error)
        assert (owned/'still.png').is_file()
    with patch.object(storage,'ROOT',root),patch.object(storage,'job_directories',lambda:[]), \
         patch('task_contract.protected_references',return_value=[]),patch.object(storage,'locked',lambda p:False):
        assert storage.clean([preview])>0
        assert not preview.exists()
print('PASS: frozen preview references, close cancellation, cache/update gates, ended preview unlock, backend run-lock cleanup refusal')
