"""Full-window startup coordination; run only in the repository sandbox."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes', 'Use isolated runner'
sys.path.insert(0, str(APP))
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from app import Window
import startup_preferences as preferences
from support_info import QQ_GROUP

application = QApplication([])
window = Window()
window.show()
window.activateWindow()
application.processEvents()
updates = window.updates
updates.download = Mock()
assert preferences.tour_due(preferences.load_preferences(APP))
window.navigate('audio_mixdown')
before = window.source.text()
assert window.start_tutorial()
tour = window.onboarding
assert tour.active and tour.isVisible()
QTest.keyClicks(window.source, 'blocked')
assert window.source.text() == before

release = {'version': '99.1.0', 'available': True, 'notes': 'Fixture release notes',
           'url': 'https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases',
           'update': {'bytes': 100}}
updates.startup_update = True
updates.checked(release)
updates.notice_timer.stop()
updates.maybe_show_notice()
assert updates._notice_dialog is None and updates._queued_notice
assert not updates.install_requested and not updates.restart_timer.isActive()
for expected in ('components', 'tuning', 'audio_mixdown', 'video_mixdown', 'publishing', 'updates'):
    tour.next_step()
    application.processEvents()
    assert window.navigation.current_page_id == expected
assert tour._support.isVisible() and QQ_GROUP in tour._support_text.text()
tour.next_step()
application.processEvents()
assert window.onboarding is None and window.navigation.current_page_id == 'audio_mixdown'
assert not preferences.tour_due(preferences.load_preferences(APP))
dialog = updates._notice_dialog
assert dialog is not None and QQ_GROUP in dialog.group_label.text()
dialog.delay_combo.setCurrentIndex(dialog.delay_combo.findData(-1))
dialog.later_button.click()
application.processEvents()
assert not preferences.reminder_due('99.1.0', preferences.load_preferences(APP))
assert preferences.reminder_due('99.2.0', preferences.load_preferences(APP))
assert not window.start_tutorial()
updates.download.assert_not_called()

# Replay from settings, ESC skip and durable completion.
window.navigate('updates')
updates.replay_tour_button.click()
assert window.onboarding.active
QTest.keyClick(window.onboarding, Qt.Key_Escape)
application.processEvents()
assert window.onboarding is None and window.navigation.current_page_id == 'updates'
assert preferences.load_preferences(APP)['tour_outcome'] == 'skipped'
updates.copy_group_button.click()
assert application.clipboard().text() == QQ_GROUP
updates.reset_reminder_button.click()
window.activateWindow()
QTest.qWait(20)
updates.notice_timer.stop()
updates.maybe_show_notice()
dialog = updates._notice_dialog
assert dialog is not None
dialog.immediate_button.click()
application.processEvents()
updates.download.assert_called_once()
assert updates.install_requested
updates.install_requested = False
updates.restart_timer.stop()

# Closing the window never marks an unfinished tour complete.
config = json.loads((APP / 'config.json').read_text(encoding='utf-8'))
config['startup_experience'] = {}
(APP / 'config.json').write_text(json.dumps(config), encoding='utf-8')
assert window.start_tutorial(force=True)
window.close()
application.processEvents()
assert preferences.tour_due(preferences.load_preferences(APP))
assert window.onboarding is None
print('PASS: actual Window tutorial, page restore, blocked editing, popup ordering, durable skip/snooze/reset, QQ settings and explicit download consent')
