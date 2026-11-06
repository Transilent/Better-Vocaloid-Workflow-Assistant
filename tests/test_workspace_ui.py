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
from workspace_ui import PAGE_SPECS, workspace_geometry

assert workspace_geometry(QRect(0, 0, 1920, 1040))[0] == (1440, 900)
assert workspace_geometry(QRect(0, 0, 2560, 1400))[0] == (1440, 900)
assert workspace_geometry(QRect(0, 0, 1280, 672)) == ((1232, 624), (1000, 624))
assert workspace_geometry(QRect(0, 0, 800, 600)) == ((752, 552), (752, 552))

app = QApplication([])
app.setFont(QFont('Microsoft YaHei', 10))
window = Window()
window.show()
app.processEvents()
navigation = window.navigation
assert window.pages.count() == len(PAGE_SPECS) == len(window.nav_buttons) == 11
assert [spec.page_id for spec in navigation.sidebar] == [
    'processing', 'tuning', 'audio_mixdown', 'video_mixdown',
    'publishing', 'components', 'results', 'updates',
]
for page_id, widget in {
    'processing': window.processing,
    'components': window.components,
    'results': window.results,
    'publishing': window.publishing,
    'updates': window.updates,
    'video_mixdown': window.video_mixdown,
    'tuning': window.tuning,
    'audio_mixdown': window.audio_mixdown,
}.items():
    assert navigation.widget(page_id) is widget
assert navigation.button('updates') is window.nav_buttons[4]
assert navigation.resolve(5)[0].page_id == 'processing'
assert navigation.resolve(6)[0].page_id == 'updates'
subtitles, focus_step = navigation.resolve(7)
assert subtitles.page_id == 'processing' and focus_step == 'subtitles'
sidebar = window.nav_buttons[4].parentWidget().layout()
visual_order = [sidebar.itemAt(i).widget().text() for i in range(sidebar.count())
                if sidebar.itemAt(i).widget() is not None and sidebar.itemAt(i).widget() in window.nav_buttons]
assert visual_order == ['准备工作流', '调音工作流', '简易混音', '合成视频', '发布', '模型与组件', '任务与结果', '设置与更新']
QTest.mouseClick(navigation.button('updates'), Qt.LeftButton)
assert window.pages.currentWidget() == window.updates
assert navigation.current_page_id == 'updates'
window.navigate(5)
assert window.pages.currentIndex() == 0 and navigation.current_page_id == 'processing'
window.navigate(6)
assert window.pages.currentIndex() == 4 and navigation.current_page_id == 'updates'
window.navigate(10)
assert window.pages.currentWidget() == window.audio_mixdown
assert navigation.current_page_id == 'audio_mixdown'
window.navigate('processing')
assert window.windowTitle() == '术力口工作流助手 · VWA'
assert window.width() == 1600 and window.height() == 1000
assert not window.log.isVisible() and window.lyrics.isVisible(), 'Important lyrics are visible by default'
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
window.step_cards['reference'].toggle.setChecked(True)
app.processEvents()
assert window.lyrics.isVisible()
window.step_cards['reference'].toggle.setChecked(False)
window.set_running(True)
assert not window.start.isEnabled() and window.stop.isVisible()
window.set_running(False)
assert window.start.isEnabled() and not window.stop.isVisible()

original_status, original_install = components.status, components.install
state = {'ready': False, 'runtime': None}
try:
    components.status = lambda *args: dict(state)
    window.step_cards['separation'].toggle.setChecked(True)
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
    window.updates.restart_timer.stop()  # The fixture is not an installable update.
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
for page_id, name in (('processing', 'processing'), ('components', 'components'), ('publishing', 'publishing'),
                      ('updates', 'updates'), ('storage', 'storage'), ('subtitles', 'subtitles')):
    window.navigate(page_id)
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
assert tab.prepare_button.text() == '生成发布资料'
assert tab.upload_button.text() == '打开 B站填写'
assert not tab.upload_button.isEnabled(), 'A placeholder path is not a prepared publication bundle'
tab.fields['bilibili']['title'].setText('修改后的草稿')
assert tab.prepare_button.text() == '生成发布资料'
assert not tab.log.isVisible()
assert not tab.cover_seconds.isVisible()
tab.cover_mode.setCurrentIndex(tab.cover_mode.findData('frame'))
tab.select_step(1)
app.processEvents()
assert tab.cover_seconds.isVisible() and tab.cover_seconds.isEnabled()
window.close()
app.processEvents()
print('PASS: stable page IDs, sidebar projection, legacy aliases, workspace actions, installer signals, dirty drafts and small-window layout')
