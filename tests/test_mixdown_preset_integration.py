"""Real mixer controls, library persistence and guarded preset commits."""
import copy
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes'
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from common import native_path
import audio_mixdown_ui as ui
from mixdown_preset_documents import plan_application, make_document
from preset_builtin_documents import builtin_documents
from preset_documents import PresetError
from preset_library import PresetLibrary
import mixdown_preset_ui as library_ui

app = QApplication.instance() or QApplication([])
work = APP / 'work'
work.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='mixer-library-', dir=native_path(work)) as folder:
    root = Path(folder)
    library = PresetLibrary(root / 'library')
    owner = QWidget()
    with patch.object(ui.AudioMixdownDialog, '_start_component_support_check', return_value=False), \
         patch.object(ui.AudioMixdownDialog, '_schedule_preview_refresh', return_value=False):
        mixer = ui.AudioMixdownDialog(owner, None, preset_library=library)
        try:
            assert not library.root.exists(), 'Mixer opening must not create or rewrite the library'
            mixer.add_vocal_track()
            mixer.vocal_tracks[0]['gain_db'].setValue(-8)
            mixer.vocal_tracks[0]['pan'].setValue(-31)
            mixer.vocal_tracks[0]['offset'].setValue(.125)
            mixer.vocal_tracks[0]['reverb_send'] = .27
            mixer.vocal_tracks[1]['pan'].setValue(44)
            mixer.instrumental_db.setValue(-3)
            mixer.width_control.set_value(.73)
            stamp = mixer._mix_preset_stamp()
            plan = plan_application(builtin_documents()[4], stamp['state'],
                                    sample_rate=stamp['sample_rate'])
            assert mixer._mix_preset_stamp() == stamp, 'Preview must be read-only'
            notify = Mock(wraps=mixer.parameters_changed)
            with patch.object(mixer, 'parameters_changed', notify):
                assert mixer._commit_mix_preset(plan, stamp)
            assert notify.call_count == 1, 'Apply must invalidate audition/export once'
            after = mixer._mix_preset_stamp()
            assert after['paths'] == stamp['paths'] and after['balance'] == stamp['balance']
            assert after['backing'] == stamp['backing']
            assert mixer._mix_preset_state()['tracks'] == stamp['state']['tracks']
            assert mixer.width_control.value() == .73
            assert mixer.undo_mix_preset_button.isEnabled()
            assert mixer.undo_mix_preset()
            assert mixer._mix_preset_stamp() == stamp
            assert not mixer.undo_mix_preset_button.isEnabled()

            stale_plan = plan_application(builtin_documents()[0], stamp['state'])
            mixer.compression.setValue(57)
            changed = mixer._mix_preset_stamp()
            try:
                mixer._commit_mix_preset(stale_plan, stamp)
                raise AssertionError('Stale target was accepted')
            except PresetError as error:
                assert error.code == 'target_changed'
            assert mixer._mix_preset_stamp() == changed

            # A failed widget commit rolls back before sending a notification.
            setter = mixer.reverb_profile.setCurrentIndex
            calls = []
            def fail_once(value):
                calls.append(value)
                if len(calls) == 1:
                    raise ValueError('synthetic widget failure')
                setter(value)
            fresh = mixer._mix_preset_stamp()
            notify = Mock(wraps=mixer.parameters_changed)
            with patch.object(mixer.reverb_profile, 'setCurrentIndex', side_effect=fail_once), \
                 patch.object(mixer, 'parameters_changed', notify):
                try:
                    mixer._commit_mix_preset(stale_plan, fresh)
                    raise AssertionError('Injected failure was accepted')
                except ValueError:
                    pass
            assert notify.call_count == 0
            assert mixer._mix_preset_stamp() == fresh

            fractional = builtin_documents()[0]
            fractional['payload'].update(compression_strength=.335, reverb_mix=.215,
                                         reverb_predelay_ms=23.8, width=1.234)
            precision_plan = plan_application(fractional, fresh['state'],
                                              sample_rate=fresh['sample_rate'], include_width=True)
            mixer._commit_mix_preset(precision_plan, fresh)
            assert mixer._mix_preset_state() == precision_plan['after']
            assert mixer.undo_mix_preset() and mixer._mix_preset_stamp() == fresh

            # Save the same display name twice: both independently appended.
            saved = make_document(fresh['state']['processing'], 'Same name')
            class SavedDialog:
                document = saved
                def __init__(self, *args, **kwargs): pass
                def exec_(self): return QDialog.Accepted
            with patch.object(library_ui, 'MixPresetSaveDialog', SavedDialog):
                assert mixer.save_mix_preset()
            saved2 = make_document(fresh['state']['processing'], 'Same name')
            SavedDialog.document = saved2
            with patch.object(library_ui, 'MixPresetSaveDialog', SavedDialog):
                assert mixer.save_mix_preset()
            local = [e for e in library.snapshot()['entries'] if not e['builtin']]
            assert len(local) == 2 and {e['id'] for e in local} == {saved['id'], saved2['id']}
            assert mixer._mix_preset_stamp() == fresh

            chosen = next(e for e in library.snapshot()['entries']
                          if e['id'] == 'builtin.mixdown.opera_bright')
            class ChosenDialog:
                def __init__(self, backend, before, **kwargs):
                    self.selected_entry = chosen
                    self.plan = plan_application(backend.get_document(chosen['id'], chosen['version']),
                                                  before, sample_rate=kwargs['sample_rate'])
                def exec_(self): return QDialog.Accepted
            with patch.object(library_ui, 'MixPresetLibraryDialog', ChosenDialog):
                assert mixer.choose_mix_preset()
            assert mixer._mix_preset_state()['tracks'] == fresh['state']['tracks']
            assert mixer.undo_mix_preset() and mixer._mix_preset_stamp() == fresh

            # The old entry uses the shared validated path and retains lock semantics.
            assert mixer.apply_mix_preset('clear_lead', lock_mix=True)
            assert mixer._mix_preset_state()['tracks'] == fresh['state']['tracks']
            assert mixer.undo_mix_preset()
            assert mixer._mix_preset_stamp() == fresh
            assert mixer.apply_mix_preset('opera_bright', lock_mix=False)
            assert mixer._mix_preset_state()['tracks'] != fresh['state']['tracks']
            mixer.compression.setValue(61)
            assert not mixer.undo_mix_preset_button.isEnabled()
            assert not mixer.undo_mix_preset(), 'Manual edits invalidate undo'
        finally:
            mixer.close()
            app.processEvents()
            owner.close()
print('PASS: mixer capture, limited scope, request invalidation, safe append save, rollback, stale refusal and undo')
