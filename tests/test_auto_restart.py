"""Update notices require consent; snoozes and active work cannot install."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication,QComboBox,QLineEdit,QVBoxLayout,QWidget
import updates_ui as ui
import startup_preferences as prefs
from support_info import QQ_GROUP
QT=QApplication.instance() or QApplication([])

class UpdateConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='update-consent-',dir=APP/'work')
        self.root=Path(self.tmp.name);(self.root/'config.json').write_text('{}',encoding='utf-8')
        self.owner=QWidget();self.owner._startup_closing=False;self.owner.processing_active=False
        source=QComboBox();source.addItem('Auto','auto')
        self.owner.components=SimpleNamespace(source=source,busy=lambda:False)
        self.owner.storage=SimpleNamespace(busy=lambda:False)
        self.owner.workflow=SimpleNamespace(persist=Mock());self.owner.navigate=Mock()
        self.owner.onboarding=None;self.owner.start_tutorial=Mock()
        self.patches=[patch.object(ui,'ROOT',self.root),patch.object(ui,'load_config',side_effect=lambda **kw:json.loads((self.root/'config.json').read_text())),patch.object(ui.updater,'pending_update',return_value=None),patch.object(ui.updater,'current_version',return_value='1.0.0')]
        for p in self.patches:p.start()
        self.page=ui.UpdatesPage(self.owner)
        layout=QVBoxLayout(self.owner);self.edit=QLineEdit();layout.addWidget(self.edit);layout.addWidget(self.page)
        self.owner.resize(1100,850);self.owner.show();self.owner.activateWindow();QT.processEvents()
        self.release={'version':'1.1.0','notes':'Description <b>as text</b>','available':True,'update':{'bytes':100},'url':'https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/tag/v1.1.0'}
    def tearDown(self):
        self.owner._startup_closing=True;self.page.restart_timer.stop();self.page.close();self.owner.close();QT.processEvents()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def notice(self,release=None,force=False):
        self.owner.activateWindow();QTest.qWait(20)
        self.page.queue_notice(release or self.release,force=force);self.page.notice_timer.stop();self.page.maybe_show_notice();QT.processEvents()
        return self.page._notice_dialog
    def test_check_does_not_download_or_skip_after_typing(self):
        QTest.keyClick(self.edit,Qt.Key_A)
        with patch.object(ui,'UpdateWorker') as worker,patch.object(self.page,'start_worker') as start:
            worker.return_value.isRunning.return_value=False
            self.page.check(startup=True);worker.assert_called_once();start.assert_called_once()
        self.page.worker=None
        with patch.object(self.page,'download') as download:
            self.page.checked(self.release);download.assert_not_called()
            self.assertFalse(self.page.install_requested);self.assertFalse(self.page.restart_timer.isActive())
    def test_snooze_survives_restart_but_not_new_release(self):
        dialog=self.notice();self.assertIsNotNone(dialog);self.assertIn(QQ_GROUP,dialog.group_label.text())
        dialog.delay_combo.setCurrentIndex(dialog.delay_combo.findData(86400));dialog.later_button.click();QT.processEvents()
        self.assertFalse(prefs.reminder_due('1.1.0',prefs.load_preferences(self.root)));self.assertFalse(self.page.install_requested)
        self.page._notice_shown.clear();self.page.queue_notice(self.release);self.assertIsNone(self.page._queued_notice)
        dialog=self.notice({**self.release,'version':'1.2.0'});self.assertIsNotNone(dialog);dialog.reject();QT.processEvents()
    def test_tutorial_queues_notice(self):
        self.owner.onboarding=SimpleNamespace(active=True)
        self.assertIsNone(self.notice());self.assertIsNotNone(self.page._queued_notice)
        self.owner.onboarding.active=False;self.page.notice_timer.stop();self.page.maybe_show_notice();QT.processEvents()
        self.assertIsNotNone(self.page._notice_dialog);self.page._notice_dialog.reject();QT.processEvents()
    def test_immediate_is_only_download_trigger_and_active_work_defers(self):
        dialog=self.notice()
        with patch.object(self.page,'download') as download:
            dialog.immediate_button.click();QT.processEvents();download.assert_called_once()
            self.assertTrue(self.page.install_requested);self.owner.navigate.assert_called_with('updates')
        self.page.downloaded(self.root/'plan.json');self.page.finished();self.assertTrue(self.page.restart_timer.isActive())
        self.page.maybe_auto_restart();self.assertEqual(self.page.restart_remaining,9)
        self.owner.processing_active=True;self.page.maybe_auto_restart();self.owner.processing_active=False
        self.assertTrue(self.page.restart_deferred);self.assertFalse(self.page.restart_timer.isActive())
        with patch.object(ui.updater,'restart_to_apply') as restart:
            for _ in range(12):self.page.maybe_auto_restart()
            restart.assert_not_called()
    def test_pending_needs_consent_and_interaction_defers(self):
        self.page.plan=str(self.root/'pending.json');self.assertFalse(self.page.restart_timer.isActive())
        dialog=self.notice({**self.release,'pending_plan':self.page.plan,'update':None})
        with patch.object(ui.updater,'restart_to_apply') as restart:
            dialog.immediate_button.click();QT.processEvents();self.assertTrue(self.page.restart_timer.isActive());restart.assert_not_called()
            QTest.keyClick(self.edit,Qt.Key_B);self.assertTrue(self.page.restart_deferred);self.assertFalse(self.page.install_requested)
            for _ in range(12):self.page.maybe_auto_restart()
            restart.assert_not_called()
        self.page.copy_qq_group();self.assertEqual(QApplication.clipboard().text(),QQ_GROUP)
        self.page.replay_tutorial();self.owner.start_tutorial.assert_called_with(force=True)
    def test_manual_restart_uses_existing_active_work_guard(self):
        self.page.plan=str(self.root/'plan.json');self.owner.processing_active=True
        with patch.object(ui.updater,'restart_to_apply') as restart:
            self.page.primary_action();restart.assert_not_called()
        self.owner.processing_active=False
        with patch.object(ui.updater,'restart_to_apply') as restart,patch.object(ui.QApplication,'quit') as quit_app:
            self.page.primary_action();restart.assert_called_once();quit_app.assert_called_once()

    def test_pending_check_failure_keeps_consent_and_snooze_gate(self):
        pending={**self.release,'pending_plan':str(self.root/'pending.json'),'update':None}
        self.page._pending_release=pending;self.page.startup_update=True
        prefs.defer_reminder(self.root,'1.1.0',-1)
        self.page.failed('Offline')
        self.assertEqual(self.page.plan,pending['pending_plan'])
        self.assertIsNone(self.page._queued_notice)
        self.assertFalse(self.page.install_requested)
        self.page.checked({**self.release,'version':'1.2.0'})
        self.assertIsNone(self.page.plan)
        self.assertEqual(self.page._queued_notice['version'],'1.2.0')

    def test_clipboard_denial_does_not_claim_success(self):
        with patch.object(ui.QApplication,'clipboard') as clipboard:
            clipboard.return_value.text.return_value=''
            self.page.copy_qq_group()
            clipboard.return_value.setText.assert_called_once_with(QQ_GROUP)
            self.assertIn('无法写入剪贴板',self.page.message.text())

if __name__=='__main__':unittest.main()
