import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtWidgets import QApplication
from app import Window

application = QApplication([])
window = Window()
assert window.windowTitle() == '术力口工作流助手 · BVWA'
assert window.source.text() == '' and window.job is None
window.show()
application.processEvents()
assert window.bv_panel.isVisible() and not window.music_panel.isVisible()
window.input_mode.setCurrentIndex(window.input_mode.findData('local'))
application.processEvents()
assert window.music_panel.isVisible() and not window.bv_panel.isVisible()
window.local_music.setText('example.mp3')
window.set_running(True)
assert not window.input_mode.isEnabled() and not window.local_music.isEnabled()
window.set_running(False)
assert window.local_music.isEnabled()
from common import atomic_json
job = APP / 'work/local-gui-source'
job.mkdir(parents=True, exist_ok=True)
atomic_json(job / 'source-info.json', {'kind': 'local', 'bvid': None, 'title': '本地歌曲', 'owner': {'name': ''}})
(job / '发布简介草稿.txt').write_text('本家与 STAFF：待填写', encoding='utf-8')
window.publishing.load_job(job)
assert window.publishing.bv.text() == ''
assert window.publishing.cover_mode.currentData() == 'custom'
assert '本地音乐' in window.publishing.source_info.text()
assert window.options()['voice_mode'] == 'dual'
window.voice_mode.setCurrentIndex(window.voice_mode.findData('import'))
assert window.options()['voice_mode'] == 'import'
window.voice_mode.setCurrentIndex(window.voice_mode.findData('single'))
assert window.options()['voice_mode'] == 'single'
assert not (APP / 'publish-ui.json').exists(), 'Run this test on a clean extracted release.'
window.close()
print('PASS: fresh portable GUI, local music input, three modes, no restored user state')
