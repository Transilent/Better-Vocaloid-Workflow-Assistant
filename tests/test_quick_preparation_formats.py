"""Catch a lost format selection at the real Qt Quick/Preparation draft boundary."""
import json
import os
from pathlib import Path
import tempfile
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from common import ROOT, atomic_json, workspace_state
from operation_diagnostics import close_journal
from pipeline import planned_steps
from preparation_commands import PreparationDraft, create_from_draft, restore_draft_settings
from quick_app import create_workspace


assert os.environ.get('VWA_TESTING') == '1', 'Run in an isolated application'
app = QGuiApplication([])
app.setQuitOnLastWindowClosed(False)
work = ROOT / 'work'


def check_at(viewport, scale, directory):
    # A saved installation with project export disabled is a supported choice.
    # The first click must enable the selected format and persist the new choice.
    atomic_json(ROOT / 'cache/workspace.json', {'quick_preparation': {'version': 1, 'options': {
        'target': 'midi', 'project_export_enabled': False, 'project_formats': [],
        'audio_export_enabled': True, 'audio_formats': ['wav']
    }}})
    source = directory / '回归音频.wav'
    with wave.open(str(source), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(bytes(32000))
    engine, window, bridge, modes = create_workspace(app, viewport=viewport, scale=scale)

    def find(*, name='', label=''):
        queue = [window.contentItem()]
        while queue:
            item = queue.pop()
            if item.isVisible() and ((name and item.objectName() == name) or
                    (label and item.property('text') == label
                     and item.metaObject().indexOfProperty('checkState') >= 0)):
                return item
            queue.extend(item.childItems())
        raise AssertionError((name, label, viewport))

    try:
        bridge.chooseMusic(QUrl.fromLocalFile(str(source)))
        bridge.setPreparationField('output_directory', str(directory / 'tasks'))
        assert bridge.preparation['project_formats'] == []
        QTest.qWait(60)
        scroll = find(name='preparationContentScroll')
        flick = scroll.property('contentItem')

        def click(item):
            center = QPointF(item.width() / 2, item.height() / 2)
            vertical = flick.mapFromScene(item.mapToScene(center)).y()
            maximum = max(0, flick.property('contentHeight') - flick.height())
            flick.setProperty('contentY', max(0, min(maximum, flick.property('contentY') + vertical - flick.height() / 2)))
            QTest.qWait(30)
            point = item.mapToScene(center).toPoint()
            local = flick.mapFromScene(QPointF(point))
            assert item.isEnabled() and 0 < local.y() < flick.height(), (point, viewport)
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
            app.processEvents()

        click(find(name='preparationOutputOptions'))
        QTest.qWait(35)

        def toggle(label, field, expected):
            box = find(label=label)
            click(box)
            selected = bridge.preparation[field]
            persisted = workspace_state()['quick_preparation']['options'][field]
            assert selected == persisted == expected, (
                viewport, label, box.property('checked'), selected, persisted, bridge.notice)
            assert box.property('checked') == (label.lower() in expected)
            assert '导出格式无效' not in bridge.notice, bridge.notice

        toggle('USTX', 'project_formats', ['ustx'])
        toggle('SVP', 'project_formats', ['ustx', 'svp'])
        toggle('VSQX', 'project_formats', ['ustx', 'vsqx', 'svp'])
        toggle('FLAC', 'audio_formats', ['wav', 'flac'])

        task = create_from_draft(bridge.state.draft)
        request = json.loads((task / 'request.json').read_text(encoding='utf-8'))
        steps, _, _ = planned_steps(request)
        assert [stage[0] for stage in steps][-2:] == ['midi', 'exports']
        assert request['project_formats'] == ['ustx', 'vsqx', 'svp'] and request['project_export_enabled']
        assert request['audio_formats'] == ['wav', 'flac'] and request['audio_export_enabled']
        assert restore_draft_settings(PreparationDraft()).preferences()['project_formats'] == request['project_formats']
        toggle('FLAC', 'audio_formats', ['wav'])
        toggle('VSQX', 'project_formats', ['ustx', 'svp'])
        toggle('SVP', 'project_formats', ['ustx'])
        toggle('USTX', 'project_formats', [])
        task = create_from_draft(bridge.state.draft)
        request = json.loads((task / 'request.json').read_text(encoding='utf-8'))
        steps, _, _ = planned_steps(request)
        assert not request['project_export_enabled'] and 'exports' not in [stage[0] for stage in steps]
        print(f'PASS {viewport[0]}: Quick format clicks update saved preferences, request and export plan', flush=True)
    finally:
        modes.close()
        bridge.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


try:
    with tempfile.TemporaryDirectory(dir=work, prefix='quick-formats-') as directory:
        for viewport, scale in (((1440, 900), 100), ((1920, 1080), 125)):
            location = Path(directory) / str(scale)
            location.mkdir()
            check_at(viewport, scale, location)
finally:
    close_journal()
