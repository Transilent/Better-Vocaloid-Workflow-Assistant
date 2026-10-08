import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtWidgets import QApplication
from app import Window

application = QApplication([])
window = Window()
assert window.windowTitle() == 'Better Vocaloid Workflow Assistant'
assert window.source.text() == '' and window.job is None
assert window.options()['voice_mode'] == 'dual'
window.voice_mode.setCurrentIndex(window.voice_mode.findData('import'))
assert window.options()['voice_mode'] == 'import'
window.voice_mode.setCurrentIndex(window.voice_mode.findData('single'))
assert window.options()['voice_mode'] == 'single'
assert not (APP / 'publish-ui.json').exists(), 'Run this test on a clean extracted release.'
window.close()
print('PASS: fresh portable GUI, three modes, no restored user state')
