"""Offscreen navigation, component installation signals, and layout snapshots."""
import json
from pathlib import Path
import sys
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
import optional_components as components
from app import Window

app = QApplication([])
app.setFont(QFont('Microsoft YaHei', 10))
window = Window()
window.show()
app.processEvents()
assert window.pages.count() == 4
assert not window.log.isVisible() and not window.lyrics.isVisible()
assert window.start.property('role') == 'primary'
QTest.mouseClick(window.lyrics_toggle, Qt.LeftButton)
assert window.lyrics.isVisible()
QTest.mouseClick(window.lyrics_toggle, Qt.LeftButton)
window.set_running(True)
assert not window.start.isEnabled() and window.stop.isVisible()
window.set_running(False)
assert window.start.isEnabled() and not window.stop.isVisible()

original_status, original_install = components.status, components.install
state = {'ready': False, 'runtime': None}
try:
    components.status = lambda *args: dict(state)
    window.separator_model.setCurrentIndex(window.separator_model.findData('roformer'))
    assert not window.start.isEnabled() and window.component_link.isVisible()
    QTest.mouseClick(window.component_link, Qt.LeftButton)
    assert window.pages.currentIndex() == 1
    def fake_install(runtime, progress, check, **kwargs):
        progress(3_000_000_000, 3_500_000_000, '正在下载')
        time.sleep(.08)
        state.update(ready=True, runtime=runtime)
        return dict(state)
    components.install = fake_install
    QTest.mouseClick(window.components.download, Qt.LeftButton)
    deadline = time.monotonic() + 5
    while window.components.busy() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert window.components.worker.wait(1000)
    app.processEvents()
    assert window.components.progress.value() == 85, 'Multi-gigabyte signals must not overflow int32'
    assert window.components.download.text() == '使用此模型'
    QTest.mouseClick(window.components.download, Qt.LeftButton)
    assert window.pages.currentIndex() == 0 and window.start.isEnabled()
    assert window.options()['voice_mode'] == 'roformer'
    assert window.options()['roformer_device'] == 'auto'
finally:
    components.status, components.install = original_status, original_install

window.voice_mode.setCurrentIndex(window.voice_mode.findData('dual'))
window.separator_model.setCurrentIndex(window.separator_model.findData('dual'))
window.close()
window = Window()  # Capture a clean state, not the simulated installation above.
window.show()
app.processEvents()
images = APP / 'docs/images'
images.mkdir(parents=True, exist_ok=True)
for index, name in ((0, 'processing'), (1, 'components'), (3, 'publishing')):
    window.navigate(index)
    app.processEvents()
    window.grab().save(str(images / (name + '.png')))
window.navigate(0)
window.resize(980, 680)
app.processEvents()
assert window.start.isVisible() and window.rect().contains(window.start.mapTo(window, window.start.rect().bottomRight()))
window.grab().save(str(images / 'processing-small.png'))
window.resize(1220, 820)
window.navigate(3)
tab = window.publishing
assert tab.prepare_button.text() == '生成发布资料'
tab.bundle = APP / 'work/placeholder-bundle'
tab.bundle_inputs = tab.input_snapshot()
tab.refresh_action()
assert tab.prepare_button.text() == '上传所选平台'
tab.fields['bilibili']['title'].setText('修改后的草稿')
assert tab.prepare_button.text() == '生成发布资料'
assert not tab.log.isVisible()
assert not tab.cover_seconds.isVisible()
tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('frame'))
assert tab.cover_seconds.isVisible() and tab.cover_seconds.isEnabled()
window.close()
app.processEvents()
print('PASS: workspace navigation, action hierarchy, multi-GB installer signals, dirty drafts, small-window layout')
