"""Exercise the new primary action with real synthetic media and Qt workers."""
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox
from app import Window
from common import atomic_json
import publishing_ui
from publication import load_bundle
from tests.support import ensure_fixture

fixture = ensure_fixture(APP)
original = load_bundle(fixture, 'bilibili')
scratch = APP / 'work/publication-workspace'
job = scratch / 'source'
job.mkdir(parents=True, exist_ok=True)
atomic_json(job / 'request.json', {'bv': None})
atomic_json(job / 'source-info.json', {'kind': 'local', 'bvid': None,
    'title': '合成测试', 'owner': {'name': ''}})
(job / '发布简介草稿.txt').write_text('本家：测试来源\nSTAFF：测试作者', encoding='utf-8')
app = QApplication([])
window = Window()
window.show()
window.navigate(3)
tab = window.publishing
tab.load_job(job)
tab.video.setText(original['video']['path'])
tab.cover_file.setText(original['cover']['source'])
tab.voice.setText('测试歌姬')
tab.apply_title()
tab.append_credit()
app.processEvents()
warnings, uploaded = [], []
real_prepare = publishing_ui.prepare
def prepared(*args, **kwargs):
    return real_prepare(*args, parent=scratch / 'bundles', **kwargs)
def saved(path, data):
    atomic_json(scratch / 'saved-ui.json' if path == APP / 'publish-ui.json' else path, data)
with patch.object(publishing_ui, 'prepare', prepared), \
     patch.object(publishing_ui, 'atomic_json', saved), \
     patch.object(QMessageBox, 'warning', lambda *args: warnings.append(args[-1])):
    assert tab.prepare_button.text() == '生成发布资料'
    QTest.mouseClick(tab.prepare_button, Qt.LeftButton)
    assert tab.busy() and not tab.prepare_button.isEnabled()
    deadline = time.monotonic() + 60
    while tab.busy() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert tab.task.wait(1000)
    app.processEvents()
    assert tab.bundle is not None, tab.status.text()
    assert tab.prepare_button.text() == '上传所选平台' and tab.prepare_button.isEnabled()
    bundle = tab.bundle
    ready = load_bundle(bundle, 'bilibili')
    assert ready['cover']['kind'] == 'custom'
    assert ready['final_publish'] == 'human_only'
    assert ready['platform_covers']['bilibili']['policy'] == 'contain'
    # Verify dispatch to both selected platforms without opening external sites.
    with patch.object(tab, 'launch_browser', lambda platform: uploaded.append(platform)):
        QTest.mouseClick(tab.prepare_button, Qt.LeftButton)
    assert uploaded == ['bilibili', 'xiaohongshu']
    tab.fields['xiaohongshu']['title'].setText('修改后的草稿')
    assert tab.prepare_button.text() == '生成发布资料'
    tab.upload_selected()
    assert len(warnings) == 1 and '当前视频' in str(warnings[0]), warnings
    saved_inputs = json.loads((scratch / 'saved-ui.json').read_text(encoding='utf-8'))['inputs']
    tab.restore_bundle(bundle, saved_inputs)
    assert tab.cover_mode.currentData() == 'custom'
    assert tab.cover_file.text() == ready['cover']['source']
    assert tab.prepare_button.text() == '上传所选平台'
    assert tab.voice.text() == '测试歌姬'
    # Failed preparation restores controls and reveals diagnostic details.
    tab.video.setText(str(scratch / 'missing.mp4'))
    QTest.mouseClick(tab.prepare_button, Qt.LeftButton)
    while tab.busy() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert tab.task.wait(1000)
    app.processEvents()
    assert tab.prepare_button.isEnabled() and tab.log_toggle.isChecked()
window.close()
app.processEvents()
print('PASS: real bundle preparation, upload dispatch, dirty inputs, custom-cover restore, recoverable errors')
