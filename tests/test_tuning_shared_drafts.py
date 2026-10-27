"""Two-page GUI contracts for shared draft CAS and explicit conflict recovery."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes', 'Use tests/run_tests.py'

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget
from common import atomic_json, native_path

from style_drafts import DraftStore
import tuning_ui
from tuning_ui import TuningPage


application = QApplication([])


class Owner(QWidget):
    job = None
    processing_active = False
    storage = SimpleNamespace(busy=lambda: False)
    updates = SimpleNamespace(busy=lambda: False)

    def __init__(self, root):
        super().__init__()
        self.root = Path(root)

    def output_parent(self):
        return self.root

    def update_component_state(self):
        pass


DETAIL = {'onset': .8, 'sustain': .6, 'connection': .4, 'release': .2}


def region(preset, strength, pitch=True):
    return {'start': 0., 'end': .25, 'preset': preset, 'strength': strength,
            'detail': copy.deepcopy(DETAIL), 'pitch': pitch}


def stage_region(page, preset, strength, pitch=True):
    page.style_mode.setCurrentIndex(page.style_mode.findData('regions'))
    if page.style_regions:
        page.region_list.setCurrentRow(0)
        page.apply_preferences({'performance_style': preset, 'style_strength': strength,
                                'style_pitch': pitch, 'style_detail': DETAIL})
        page._schedule_style_autosave()
    else:
        page.apply_preferences({'performance_style': preset, 'style_strength': strength,
                                'style_pitch': pitch, 'style_detail': DETAIL})
        page.style_timeline.set_selection(0., .25)
        page.add_style_region()
        page.region_list.setCurrentRow(0)
    page._flush_style_autosave()
    application.processEvents()


def changed_payload(snapshot, preset, strength, pitch=True):
    data = copy.deepcopy(snapshot['data'])
    data.pop('_sync', None)
    item = region(preset, strength, pitch)
    data['style_regions'] = [item]
    data['mode'] = 'regions'
    data['selection'] = [item['start'], item['end']]
    data['selected_index'] = 0
    for key in ('tool', 'next_tool'):
        data[key] = {**data[key], 'performance_style': preset,
                     'style_strength': strength, 'style_pitch': pitch,
                     'style_detail': copy.deepcopy(DETAIL)}
    return data


def recovery_for(folder, input_sha, revision):
    matches = []
    seen = []
    prefix = input_sha + '.conflict-'
    for path in list(folder.iterdir()):
        if not (path.name.startswith(prefix) and path.name.endswith('.json')):
            continue
        try:
            value = json.loads(Path(native_path(path)).read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue
        seen.append((path.name, value.get('current_revision')))
        if value.get('current_revision') == revision:
            matches.append((path, value))
    assert matches, (revision, seen, sorted(path.name for path in folder.iterdir()) if folder.is_dir() else [])
    return matches[-1]


class RacingStore:
    """Inject one late external write immediately before an explicit CAS."""

    def __init__(self, delegate, input_sha, third_data):
        self.delegate = delegate
        self.input_sha = input_sha
        self.third_data = third_data
        self.armed = False
        self.write_calls = 0
        self.injected = None

    def read(self, input_sha):
        return self.delegate.read(input_sha)

    def write(self, input_sha, expected_revision, data, *, writer='unknown'):
        self.write_calls += 1
        if self.armed:
            self.armed = False
            current = self.delegate.read(input_sha)
            assert current['revision'] == expected_revision
            self.injected = self.delegate.write(
                input_sha, expected_revision, self.third_data, writer='late-third-agent')
        return self.delegate.write(input_sha, expected_revision, data, writer=writer)


Q = 705600000
track = {'name': 'Lead', 'mainRef': {'blickOffset': 0, 'database': {'name': 'Fixture'}},
         'groups': [], 'mainGroup': {'uuid': 'lead', 'parameters': {}, 'notes': [
             {'onset': index * Q // 2, 'duration': Q // 2, 'pitch': 60 + index,
              'lyrics': 'la'} for index in range(8)]}}
project = {'version': 153, 'time': {'tempo': [{'position': 0, 'bpm': 120}], 'meter': []},
           'tracks': [track]}

with tempfile.TemporaryDirectory(prefix='tuning-shared-', dir=APP / 'work', ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    source = root / 'shared-input.svp'
    atomic_json(source, project)
    state = {'tuning': {'session': None, 'preferences': {}}, 'untouched': {'value': 1}}
    draft_root = root / 'page-root' / 'cache/style-region-drafts'
    outside = DraftStore(draft_root, lock_timeout=.2)
    with patch.object(tuning_ui, 'ROOT', root / 'page-root'), \
            patch.object(tuning_ui, 'workspace_state', side_effect=lambda: copy.deepcopy(state)), \
            patch('workflow.read_state', return_value={'options': {}}), \
            patch.dict(os.environ, {'VWA_TESTING': '1', 'BVWA_TESTING': '1'}):
        owner_a, owner_b = Owner(root / 'a-output'), Owner(root / 'b-output')
        page_a, page_b = TuningPage(owner_a), TuningPage(owner_b)
        assert page_a.load_project(source) and page_b.load_project(source)
        input_sha = page_a._style_input_sha256
        assert input_sha == page_b._style_input_sha256 and not draft_root.joinpath(input_sha + '.json').is_file()
        assert page_a._style_draft_store().lock_timeout == .2
        page_b.performance_style.setCurrentIndex(page_b.performance_style.findData('sad'))
        local_preferences=page_b.preferences()

        stage_region(page_a, 'gentle', .5)
        first = outside.read(input_sha)
        assert first['exists'] and first['data']['style_regions'] == [region('gentle', .5)], (
            first, page_a.style_regions, page_a._style_draft_revision,
            page_a.region_draft_status.text(), page_a._last_style_draft is None,
            page_a.log.toPlainText())
        first_revision = first['revision']
        page_a.start_style_editor_button.click();application.processEvents()
        assert page_a._region_dialog is not None and page_a._region_dialog.isFullScreen()
        editor_panel=page_a._region_dialog_scroll.widget()
        assert editor_panel.isAncestorOf(page_a.region_load_latest_draft_button)
        assert editor_panel.isAncestorOf(page_a.region_save_new_revision_button)
        page_a._region_dialog.close();application.processEvents()

        # The second page sees the update without silently replacing its editor.
        assert page_b.style_regions == []
        page_b.check_shared_style_draft()
        assert page_b.style_regions == [] and page_b._style_draft_revision is None
        assert page_b.preferences()==local_preferences
        assert '共享草稿已更新' in page_b.shared_draft_status.text()
        assert page_b.shared_draft_load_button.isEnabled()
        page_b.shared_draft_load_button.click()
        application.processEvents()
        assert page_b.style_regions == [region('gentle', .5)]
        assert page_b._style_draft_revision == first_revision
        assert page_b._region_undo == [] and page_b._region_redo == []
        _, unsaved_recovery = recovery_for(draft_root, input_sha, first_revision)
        assert unsaved_recovery['incoming_data']['tool']['performance_style'] == 'sad'
        assert page_b.style_timeline.complete_note_range(.01, .20) == (0., .25)

        # An independent writer advances the file. Page A keeps its local view.
        second_payload = changed_payload(first, 'bold', .25, False)
        second = outside.write(input_sha, first_revision, second_payload, writer='external-agent')
        page_a.check_shared_style_draft()
        assert page_a.style_regions == [region('gentle', .5)]
        assert page_a._style_draft_revision == first_revision
        assert '共享草稿已更新' in page_a.shared_draft_status.text()

        stage_region(page_a, 'sad', .8, True)
        page_a._flush_style_autosave()
        assert page_a._style_draft_conflict
        assert page_a.style_regions == [region('sad', .8, True)], (
            page_a.style_regions, page_a._region_tool_intent, page_a.region_list.currentRow(),
            page_a.performance_style.currentData(), page_a.style_strength.currentData(),
            page_a.region_draft_status.text())
        current_snapshot = outside.read(input_sha)
        assert current_snapshot['revision'] == second['revision'], (
            input_sha, page_a._style_input_sha256, current_snapshot,
            sorted(path.name for path in draft_root.iterdir()))
        assert Path(current_snapshot['path']).parent.resolve() == draft_root.resolve()
        first_recovery_path, first_recovery = recovery_for(draft_root, input_sha, second['revision'])
        assert first_recovery['current_data']['style_regions'] == [region('bold', .25, False)]
        assert first_recovery['incoming_data']['style_regions'] == [region('sad', .8, True)]

        # A third writer changes the version after that conflict. Explicit save
        # must first back up the third version before replacing it.
        third_payload = changed_payload(second, 'gentle', .2, True)
        third = outside.write(input_sha, second['revision'], third_payload, writer='third-agent')
        page_a.save_new_revision_button.click()
        application.processEvents()
        saved = outside.read(input_sha)
        assert not page_a._style_draft_conflict, (
            page_a.shared_draft_status.text(), page_a._style_draft_revision,
            page_a._style_draft_remote_revision, page_a._style_draft_recovery_paths,
            page_a._style_draft_last_conflict, saved['revision'], saved['data']['style_regions'])
        assert saved['revision'] != third['revision']
        assert saved['data']['style_regions'] == [region('sad', .8, True)]
        third_recovery_path, third_recovery = recovery_for(draft_root, input_sha, third['revision'])
        assert third_recovery['current_data']['style_regions'] == [region('gentle', .2, True)]
        assert third_recovery['incoming_data']['style_regions'] == [region('sad', .8, True)]
        assert first_recovery_path != third_recovery_path

        # Ordinary autosave resumes after the explicit resolution.
        prior_revision = saved['revision']
        page_a.style_strength_spin.setValue(55)
        QTest.qWait(240)
        application.processEvents()
        autosaved = outside.read(input_sha)
        assert autosaved['revision'] != prior_revision
        assert autosaved['data']['style_regions'][0]['strength'] == .55
        assert '已自动暂存' in page_a.region_draft_status.text()

        # A late update between recovered-current and explicit CAS must produce
        # another conflict and stop. It must not chase the newest revision.
        fourth_payload = changed_payload(autosaved, 'gentle', .35, True)
        fourth = outside.write(input_sha, autosaved['revision'], fourth_payload, writer='fourth-agent')
        page_a.style_strength_spin.setValue(90)
        page_a._flush_style_autosave()
        assert page_a._style_draft_conflict
        _, conflict_recovery = recovery_for(draft_root, input_sha, fourth['revision'])
        assert conflict_recovery['current_data']['style_regions'] == [region('gentle', .35, True)]

        late_payload = changed_payload(fourth, 'bold', .15, False)
        racer = RacingStore(outside, input_sha, late_payload)
        page_a._style_draft_store_instance = racer
        page_a._style_draft_store_root = draft_root
        racer.armed = True
        page_a.save_new_revision_button.click()
        application.processEvents()
        assert racer.injected is not None and racer.write_calls == 1
        assert page_a._style_draft_conflict
        assert page_a.style_regions == [region('sad', .9, True)]
        latest = outside.read(input_sha)
        assert latest['revision'] == racer.injected['revision']
        assert latest['data']['style_regions'] == [region('bold', .15, False)]
        _, late_recovery = recovery_for(draft_root, input_sha, latest['revision'])
        assert late_recovery['current_data']['style_regions'] == [region('bold', .15, False)]
        assert late_recovery['incoming_data']['style_regions'] == [region('sad', .9, True)]
        assert '共享草稿又发生更新' in page_a.shared_draft_status.text()

        page_a.close();page_b.close();application.processEvents()

print('PASS: two GUI pages isolate external updates, preserve two-sided recovery across a third writer, load latest with undo reset, save an explicit local revision, resume autosave and reject a late CAS race')
