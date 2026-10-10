"""Offscreen navigation, component installation signals, and layout snapshots."""
import json
from pathlib import Path
import sys
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
import optional_components as components
import updater
from app import Window
from workspace_ui import workspace_geometry

assert workspace_geometry(QRect(0, 0, 1920, 1040))[0] == (1600, 992)
assert workspace_geometry(QRect(0, 0, 1280, 672)) == ((1232, 624), (1000, 624))
assert workspace_geometry(QRect(0, 0, 800, 600)) == ((752, 552), (752, 552))

app = QApplication([])
app.setFont(QFont('Microsoft YaHei', 10))
window = Window()
window.show()
app.processEvents()
assert window.pages.count() == 8
sidebar = window.nav_buttons[4].parentWidget().layout()
visual_order = [sidebar.itemAt(i).widget().text() for i in range(sidebar.count())
                if sidebar.itemAt(i).widget() in window.nav_buttons]
assert visual_order == ['处理音乐', '模型与组件', '任务与结果', '发布准备', '工作流', '缓存与磁盘', '歌词转字幕', '设置与更新']
QTest.mouseClick(window.nav_buttons[4], Qt.LeftButton)
assert window.pages.currentWidget() == window.updates
window.navigate(0)
assert window.windowTitle() == '术力口工作流助手 · BVWA'
assert window.width() == 1600 and window.height() == 1000
assert not window.log.isVisible() and not window.lyrics.isVisible()
assert window.start.property('role') == 'primary'
assert window.zh_lyric_mode.currentData() == 'hanzi'
window.zh_lyric_mode.setCurrentIndex(window.zh_lyric_mode.findData('pinyin'))
assert window.options()['zh_lyric_mode'] == 'pinyin'
window.language.setCurrentIndex(window.language.findData('ja'))
assert not window.zh_lyric_panel.isVisible()
window.language.setCurrentIndex(window.language.findData('zh'))
window.recognize.setChecked(False)
assert not window.zh_lyric_mode.isEnabled()
window.recognize.setChecked(True)
assert window.zh_lyric_mode.isEnabled()
assert not window.windowIcon().isNull()
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

original_check, original_prepare = updater.check_release, updater.prepare_update
try:
    updater.check_release = lambda source: {
        'version': '9.9.9', 'available': True, 'notes': 'Update fixture',
        'url': updater.RELEASES, 'update': {'bytes': 1024}}
    window.updates.check()
    assert window.updates.worker.wait(1000)
    app.processEvents()
    assert window.updates.action.text() == '下载程序更新'
    assert window.updates.notes.toPlainText() == 'Update fixture'
    assert window.updates.action.isEnabled()
    updater.prepare_update = lambda *args: APP / 'work' / 'fixture-plan.json'
    window.updates.download()
    assert window.updates.worker.wait(1000)
    app.processEvents()
    assert window.updates.action.text() == '重启并安装更新'
    window.set_running(True)
    window.updates.primary_action()
    assert '当前任务' in window.updates.message.text(), 'Update installation must wait for active processing'
    window.set_running(False)
    window.updates.checked(updater.legacy_release({'archive': {'name': 'BVWA-Windows-x64.zip'}, 'parts': ['part']}))
    assert '尚未提供自动更新信息' in window.updates.message.text()
finally:
    updater.check_release, updater.prepare_update = original_check, original_prepare

window.voice_mode.setCurrentIndex(window.voice_mode.findData('dual'))
window.separator_model.setCurrentIndex(window.separator_model.findData('dual'))
window.close()
window = Window()  # Capture a clean state, not the simulated installation above.
window.output_dir.setText('jobs')  # Documentation screenshots omit personal output paths.
window.show()
app.processEvents()
images = APP / 'docs/images'
images.mkdir(parents=True, exist_ok=True)
for index, name in ((0, 'processing'), (1, 'components'), (3, 'publishing'), (4, 'updates'), (5, 'workflow'), (6, 'storage'), (7, 'subtitles')):
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
