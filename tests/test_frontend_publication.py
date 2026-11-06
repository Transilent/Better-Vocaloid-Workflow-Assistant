"""Publication review actions, real cover variants, and stale-state guards."""
import json
import sys
import tempfile
import time
from threading import Event
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

from app import Window
from publication import XIAOHONGSHU, XIAOHONGSHU_CREATOR_URL, load_bundle, prepare
from tests.support import ensure_fixture


bundle = ensure_fixture(APP)
manifest = load_bundle(bundle, 'bilibili')
app = QApplication([])
window = Window()
window.resize(1400, 900)
window.show()
window.navigate(3)
tab = window.publishing
tab.restore_bundle(bundle)
tab.select_step(3)
app.processEvents()


def wait_idle(timeout=20):
    deadline = time.monotonic() + timeout
    while tab.busy() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not tab.busy(), tab.status.text()
    if tab.task is not None:
        assert tab.task.wait(1000)
    app.processEvents()

assert tab.prepare_button.text() == '生成发布资料'
assert tab.upload_button.text() == '打开 B站填写'
assert tab.prepare_button.isVisible() and tab.prepare_button.isEnabled()
assert tab.upload_button.isVisible() and tab.upload_button.isEnabled()

# The compatibility entry point generates only; opening a platform is a separate action.
events = []
with patch.object(tab, 'prepare_bundle', lambda: events.append('prepare')), \
     patch.object(tab, 'upload_selected', lambda: events.append('upload')):
    tab.primary_action()
assert events == ['prepare'], events

launched = []
with patch.object(tab, 'launch_browser', lambda platform: launched.append(platform)):
    QTest.mouseClick(tab.upload_button, Qt.LeftButton)
    wait_idle()
assert launched == ['bilibili'], launched

# Handing off a Xiaohongshu-only package requires an explicit local declaration.
xhs = manifest['content'][XIAOHONGSHU]
assert set(tab.account_actions) == {'bilibili'}
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='xhs-handoff-') as folder:
    scratch = Path(folder)
    original_video = Path(manifest['video']['path'])
    local_video = scratch / 'finished.mp4'
    local_video.write_bytes(original_video.read_bytes())
    source = scratch / 'source'
    source.mkdir()
    for name in ('request.json', 'source-info.json', '发布简介草稿.txt'):
        (source / name).write_bytes((Path(manifest['source_job']) / name).read_bytes())
    args = (source, local_video)
    options = {'cover_file': bundle / 'cover.jpg', 'parent': scratch / 'bundles'}
    for invalid in ({}, {'ai_generated': 0}, {'ai_generated': 'false'}):
        try:
            prepare(*args, {XIAOHONGSHU: {'title': xhs['title'], 'description': xhs['description'], **invalid}}, **options)
            raise AssertionError('Ambiguous or absent AI declaration accepted')
        except ValueError as error:
            assert 'AI' in str(error)
    assert not options['parent'].exists()
    solo = prepare(*args, {XIAOHONGSHU: {**xhs, 'ai_generated': False}}, **options)
    saved = load_bundle(solo, XIAOHONGSHU, require_ai=True)
    assert saved['content'][XIAOHONGSHU]['ai_generated'] is False
    assert not saved['platform_covers'] and not list(solo.glob('cover-bilibili-*'))
    assert '（本地选择）：不含' in (solo / '发布前核对.txt').read_text(encoding='utf-8')
    tab.restore_bundle(solo)
    assert not tab.upload_button.isEnabled()
    assert tab.manual_check.isEnabled() and not tab.manual_actions['title'].isEnabled()
    opened = []
    with patch('publishing_ui.ROOT', scratch), \
         patch('publishing_ui.subprocess.Popen', side_effect=AssertionError('manual handoff launched a helper')), \
         patch.object(QDesktopServices, 'openUrl', side_effect=lambda url: opened.append(url) or True):
        tab.launch_browser(XIAOHONGSHU, login_only=True)
        tab.launch_browser(XIAOHONGSHU)
        tab.resume_browser(XIAOHONGSHU)
        tab.stop_browser(XIAOHONGSHU)
        tab.upload_selected()
        assert not tab.processes and not (scratch / 'cache/publish-browser').exists()
        tab.manual_check.click()
        wait_idle()
        assert not opened and tab.manual_actions['title'].isEnabled()
        for action, expected in (
                ('title', xhs['title']), ('description', xhs['description']),
                ('all', xhs['title'] + '\n\n' + xhs['description']),
                ('video', str(local_video)), ('cover', str(solo / 'cover.jpg'))):
            tab.manual_actions[action].click()
            assert app.clipboard().text() == expected
        tab.manual_actions['folder'].click()
        tab.manual_actions['official'].click()
        assert opened[0].isLocalFile() and Path(opened[0].toLocalFile()) == solo
        assert opened[1].toString() == XIAOHONGSHU_CREATOR_URL
        local_video.write_bytes(b'changed after preflight')
        tab.manual_actions['official'].click()
        assert len(opened) == 2 and not tab.manual_actions['official'].isEnabled()
        assert not tab.processes and not (scratch / 'cache/publish-browser').exists()
    # Restore a v2 package without the new field without silently inferring it.
    legacy = dict(manifest, content={**manifest['content'], XIAOHONGSHU: {
        'title': xhs['title'], 'description': xhs['description']}})
    (solo / 'manifest.json').write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
    (solo / 'cover.jpg').write_bytes((bundle / 'cover.jpg').read_bytes())
    for ratio in manifest['platform_covers']['bilibili'].values():
        if isinstance(ratio, dict) and ratio['file'] != 'cover.jpg':
            (solo / ratio['file']).write_bytes((bundle / ratio['file']).read_bytes())
    tab.restore_bundle(solo)
    assert tab.fields[XIAOHONGSHU]['ai_generated'].currentData() is None
    assert tab.upload_button.isEnabled() and not tab.manual_check.isEnabled()
tab.restore_bundle(bundle)
assert tab.fields[XIAOHONGSHU]['ai_generated'].currentData() is True

# A preflight result is discarded if any frozen input changes while hashing runs.
tab.restore_bundle(bundle)
tab.select_step(3)
entered, release = Event(), Event()


def slow_validation(*args, **kwargs):
    entered.set()
    assert release.wait(10), 'test did not release preflight worker'
    return {}


stale_warnings = []
stale_launches = []
with patch('publication.load_bundle', side_effect=slow_validation), \
     patch.object(QMessageBox, 'warning', lambda *args: stale_warnings.append((args[1], args[2]))), \
     patch.object(tab, 'launch_browser', lambda platform: stale_launches.append(platform)):
    QTest.mouseClick(tab.upload_button, Qt.LeftButton)
    assert entered.wait(5), tab.status.text()
    assert tab.busy() and not tab.upload_button.isEnabled()
    assert tab.workspace.locked and not tab.prepare_button.isEnabled()
    assert '检查发布资料' in tab.status.text()
    tab.fields['bilibili']['title'].setText('预检期间发生变化的文稿')
    release.set()
    wait_idle()
assert not stale_launches
assert any('检查结果已过期' in title for title, _ in stale_warnings)

# Editing a draft invalidates the upload entry but never changes the generate button.
tab.fields['bilibili']['title'].setText('修改后的草稿')
app.processEvents()
assert tab.prepare_button.text() == '生成发布资料' and tab.prepare_button.isEnabled()
assert not tab.upload_button.isEnabled()
assert '重新生成' in tab.upload_button.toolTip()

# The generate button cannot fall through into browser launching on a validation stop.
tab.fields['bilibili']['title'].setText('【XX翻唱】未命名')
tab.fields['xiaohongshu']['title'].setText('【XX翻唱】未命名')
warnings = []
never_launched = []
with patch.object(QMessageBox, 'warning', lambda *args: warnings.append(args[-1])), \
     patch.object(tab, 'launch_browser', lambda platform: never_launched.append(platform)):
    QTest.mouseClick(tab.prepare_button, Qt.LeftButton)
assert warnings and not never_launched and not tab.busy()

tab.restore_bundle(bundle)
tab.select_step(1)
app.processEvents()
for ratio in ('4:3', '16:9'):
    index = tab.cover_asset_mode.findData(ratio)
    assert index >= 0
    tab.cover_asset_mode.setCurrentIndex(index)
    app.processEvents()
    asset = manifest['platform_covers']['bilibili'][ratio]
    expected = QPixmap(str(bundle / asset['file']))
    assert not tab.cover_preview.original.isNull()
    assert (tab.cover_preview.original.width(), tab.cover_preview.original.height()) == (
        expected.width(), expected.height()
    )
    assert ratio in tab.cover_detail.text()

# Before a bundle exists, selecting a ratio cannot retain or fabricate an image.
tab.bundle = None
tab.bundle_inputs = None
tab.refresh_cover_assets(force=True)
tab.cover_asset_mode.setCurrentIndex(tab.cover_asset_mode.findData('4:3'))
tab.show_cover_asset()
assert tab.cover_preview.original.isNull()
assert '未生成' in tab.cover_detail.text()

tab.bundle = APP / 'work/missing-publication-bundle'
tab.bundle_inputs = tab.input_snapshot()
tab.refresh_cover_assets(force=True)
assert '发布资料无法读取' in tab.cover_asset_hint.text()

# Locking keeps the current step and disables all step jumps until work completes.
tab.restore_bundle(bundle)
tab.select_step(3)
locked_index = tab.workspace.index
tab.set_busy(True)
tab.select_step(0)
assert tab.workspace.index == locked_index
assert tab.workspace.locked and all(not item.isEnabled() for item in tab.workspace.buttons)
tab.set_busy(False)
assert not tab.workspace.locked

# A hash failure in the background preflight is recoverable and never launches a browser.
tab.restore_bundle(bundle)
tab.select_step(3)
warnings = []
never_launched = []
with patch('publication.load_bundle', side_effect=ValueError('fixture hash mismatch')), \
     patch.object(QMessageBox, 'warning', lambda *args: warnings.append((args[1], args[2]))), \
     patch.object(tab, 'launch_browser', lambda platform: never_launched.append(platform)):
    QTest.mouseClick(tab.upload_button, Qt.LeftButton)
    wait_idle()
assert any('检查未通过' in title for title, _ in warnings) and not never_launched
assert any('重新生成' in text for _, text in warnings)
assert not tab.upload_button.isEnabled() and '重新生成' in tab.upload_button.toolTip()

window.close()
app.processEvents()
print('PASS: manual Xiaohongshu handoff and legacy restore, Bilibili-only dispatch, async preflight, stale inputs, hash failure and dual-cover previews')
