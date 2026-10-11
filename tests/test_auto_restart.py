"""Only startup updates may restart automatically; active work defers installation."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['BVWA_TESTING'] = '1'
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox, QLineEdit, QWidget
import updates_ui

application = QApplication([])
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='auto-restart-') as directory:
    root = Path(directory)
    source = QComboBox()
    source.addItem('Auto', 'auto')
    busy = {'components': False, 'publishing': False, 'storage': False}
    owner = SimpleNamespace(processing_active=False,
        components=SimpleNamespace(source=source, busy=lambda: busy['components']),
        publishing=SimpleNamespace(busy=lambda: busy['publishing']),
        storage=SimpleNamespace(busy=lambda: busy['storage']), workflow=SimpleNamespace(persist=Mock()),
        navigate=Mock())
    with patch.object(updates_ui, 'ROOT', root), \
         patch.object(updates_ui, 'load_config', return_value={}), \
         patch.object(updates_ui.updater, 'pending_update', return_value=None), \
         patch.object(updates_ui.updater, 'current_version', return_value='0.3.1'):
        page = updates_ui.UpdatesPage(owner)
        assert page.auto_check.isChecked() and page.auto_download.isChecked() and page.auto_restart.isChecked()
        # A manually downloaded update must remain ready until the user applies it.
        page.downloaded(root / 'plan.json')
        page.finished()
        assert not page.restart_timer.isActive()
        with patch.object(updates_ui.updater, 'restart_to_apply') as restarting:
            for _ in range(12):
                page.maybe_auto_restart()
            restarting.assert_not_called()
        page.plan = None
        release = {'version': '0.3.2', 'notes': 'Fixture', 'available': True,
                   'update': {'bytes': 100}, 'url': 'https://example.test/release'}
        with patch.object(updates_ui.QTimer, 'singleShot') as schedule:
            page.startup_update = False
            page.checked(release)
            schedule.assert_not_called()
            page.startup_update = True
            page.checked(release)
            assert schedule.call_args.args[0] == 100
            owner.navigate.assert_called_once_with(4)
        with patch.object(page, 'download') as downloading:
            page.primary_action()
            downloading.assert_called_once()
            assert not page.startup_update, 'A manual download must clear the automatic context'
        # Starting work before the startup check skips this launch's automatic update.
        owner.processing_active = True
        with patch.object(updates_ui, 'UpdateWorker') as worker:
            page.check(startup=True)
            worker.assert_not_called()
        owner.processing_active = False
        page.startup_update = True
        page.downloaded(root / 'plan.json')
        page.finished()
        assert page.restart_timer.isActive()
        page.restart_timer.stop()
        with patch.object(updates_ui.updater, 'restart_to_apply') as restarting, \
             patch.object(updates_ui, 'QApplication') as qt:
            for _ in range(11):
                page.maybe_auto_restart()
            restarting.assert_called_once_with(str(root / 'plan.json'))
            owner.workflow.persist.assert_called_once_with(dirty=False)
            qt.instance().quit.assert_called_once()
        # Every kind of active task cancels the automatic countdown for this launch.
        for field in ('processing', 'components', 'publishing', 'storage'):
            page.startup_update = True
            page.downloaded(root / 'plan.json')
            page.finished()
            owner.processing_active = field == 'processing'
            busy.update({name: name == field for name in busy})
            with patch.object(updates_ui.updater, 'restart_to_apply') as restarting:
                page.maybe_auto_restart()
                assert page.restart_deferred and not page.restart_timer.isActive()
                owner.processing_active = False
                busy.update({name: False for name in busy})
                for _ in range(12):
                    page.maybe_auto_restart()
                page.finished()
                assert not page.restart_timer.isActive()
                restarting.assert_not_called()
        # Starting work during the countdown also defers, even after work finishes.
        page.downloaded(root / 'plan.json')
        page.finished()
        page.maybe_auto_restart()
        assert page.restart_remaining == 9 and '10 秒' in page.message.text()
        owner.processing_active = True
        page.maybe_auto_restart()
        owner.processing_active = False
        assert page.restart_deferred and page.restart_remaining is None
        # The user can postpone and apply later; toggling settings must not revive it.
        page.downloaded(root / 'plan.json')
        page.finished()
        page.cancel_download()
        assert page.restart_deferred and not page.restart_timer.isActive() and page.plan
        page.auto_restart.setChecked(False)
        assert not json.loads((root / 'config.json').read_text())['auto_restart_updates']
        page.auto_restart.setChecked(True)
        assert page.restart_deferred and not page.restart_timer.isActive()
        with patch.object(updates_ui.updater, 'restart_to_apply') as restarting, \
             patch.object(updates_ui, 'QApplication') as qt:
            page.primary_action()
            restarting.assert_called_once()
            qt.instance().quit.assert_called_once()
        page.close()
        # A pending update can resume at the next launch, subject to preferences.
        with patch.object(updates_ui.updater, 'pending_update',
                         return_value={'plan': str(root / 'pending.json'), 'version': '0.3.1'}):
            restored = updates_ui.UpdatesPage(owner)
            application.processEvents()
            assert restored.startup_update and restored.restart_timer.isActive()
            restored.restart_timer.stop()
            restored.close()
            with patch.object(updates_ui, 'load_config', return_value={'check_for_updates': False}):
                disabled = updates_ui.UpdatesPage(owner)
                application.processEvents()
                assert not disabled.restart_timer.isActive()
                disabled.close()
        # Interaction permanently ends this launch's automatic-install window,
        # including a short task that finishes before the download completes.
        interactive = QWidget()
        for key, value in vars(owner).items():
            setattr(interactive, key, value)
        edit = QLineEdit(interactive)
        guarded = updates_ui.UpdatesPage(interactive)
        guarded.startup_update = True
        interactive.processing_active = True
        QTest.keyClick(edit, Qt.Key_A)
        interactive.processing_active = False
        guarded.downloaded(root / 'late-plan.json')
        guarded.finished()
        assert guarded.startup_interrupted and guarded.restart_deferred
        assert not guarded.restart_timer.isActive()
        with patch.object(updates_ui.updater, 'restart_to_apply') as restarting:
            for _ in range(12):
                guarded.maybe_auto_restart()
            restarting.assert_not_called()
        guarded.plan = None
        with patch.object(updates_ui, 'UpdateWorker') as worker:
            guarded.check(startup=True)
            worker.assert_not_called()
        guarded.close()
        interactive.close()
print('PASS: startup-only download/restart, manual updates, countdown, active work, postponement, settings and next-launch resume')
