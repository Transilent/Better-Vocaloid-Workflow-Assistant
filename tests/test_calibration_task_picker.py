"""Isolated contract for choosing a task-owned calibration session."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
from wave import open as open_wave

ROOT=Path(__file__).resolve().parents[1]
assert os.environ.get('BVWA_TESTING')=='1' and ROOT.parent.name=='test-sandboxes','Use tests/run_tests.py'
sys.path.insert(0,str(ROOT))
from common import atomic_json,file_hash
from calibration_session import read_session
import calibration_ui
import results_ui


def task_row(path):
    return {'path':Path(path),'title':'Picked Song','state':'partial','modified':0}


def write_reference(path,duration=.75):
    with open_wave(str(path),'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b'\x00\x00'*round(24000*duration))


def write_record(task,name,reference,created):
    session_dir=task/name
    session_dir.mkdir()
    (session_dir/'renders').mkdir()
    spec={
        'schema':1,
        'project':str(task/'exports/voices.svp'),
        'project_sha256':file_hash(task/'exports/voices.svp'),
        'reference':str(reference),
        'reference_sha256':file_hash(reference),
        'voice':'lead','track_index':0,
        'score_sha256':file_hash(task/'exports/voices.svp'),
        'strength':.6,'minimum_duration':.5,
        'outputs':{
            'neutral':'BVWA_pickertest_lead_neutral',
            'loudness':'BVWA_pickertest_lead_loudness',
            'tension':'BVWA_pickertest_lead_tension',
            'breathiness':'BVWA_pickertest_lead_breathiness',
            'voicing':'BVWA_pickertest_lead_voicing',
            'gender':'BVWA_pickertest_lead_gender',
            'toneShift':'BVWA_pickertest_lead_toneShift',
        },
        'state':'waiting','render_folder':str(session_dir/'renders'),
        'render_channel_mode':'standard',
    }
    atomic_json(session_dir/'session.json',spec)
    (session_dir/'calibration.svp').write_text(json.dumps({
        'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[]},'tracks':[],
    }),encoding='utf-8')
    os.utime(session_dir/'session.json',(created,created))
    return session_dir/'session.json'


class FakeOwner:
    def __init__(self,row):
        self.results=SimpleNamespace(rows=[row],owner=None)
        self.processing_active=False
        self.storage=SimpleNamespace(busy=lambda:False)
        self.updates=SimpleNamespace(busy=lambda:False)


class FakeDialog:
    def __init__(self,owner,row):
        self.owner=owner
        self.row=row
        self._busy=False
        self.status=[]
        self.failure=None
        self.project=SimpleNamespace(text='before')
        self.reference=SimpleNamespace(text='before')
        self.resume_session=Mock()
    def busy(self):return self._busy
    def set_status(self,text,error=False):self.status.append((text,bool(error)))
    def failed(self,error):self.failure=str(error)


def ready_dialog(owner,row):
    dialog=FakeDialog(owner,row)
    dialog._resume_blocker=MethodType(calibration_ui.CalibrationDialog._resume_blocker,dialog)
    dialog.resume_task=MethodType(calibration_ui.CalibrationDialog.resume_task,dialog)
    return dialog


class Picker:
    outcome=0
    selection_row=None
    selection_value=None
    last=None
    def __init__(self,owner,parent=None,**options):
        type(self).last=(owner,parent,options)
    def exec_(self):return type(self).outcome
    def selection(self):return {'row':type(self).selection_row,'value':type(self).selection_value}
    def deleteLater(self):pass


with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='calibration-task-picker-') as directory:
    root=Path(directory)
    task=root/'BV1fixture_20261022'; (task/'exports').mkdir(parents=True); (task/'audio').mkdir()
    quarter=705600000
    project={
        'version':153,
        'time':{'tempo':[{'position':0,'bpm':120}],'meter':[]},
        'tracks':[{
            'name':'Lead','mainRef':{'blickOffset':0,'database':{'name':'Fixture Voice'}},
            'groups':[],
            'mainGroup':{'uuid':'lead','notes':[{'onset':0,'duration':quarter,'pitch':60,'lyrics':'la'}],
                         'parameters':{}},
        }],
    }
    atomic_json(task/'exports/voices.svp',project)
    reference=task/'audio/lead.wav';write_reference(reference)
    now=1_790_000_000
    older=write_record(task,'calibration-older',reference,now-120)
    newer=write_record(task,'calibration-newer',reference,now)
    (newer.parent/'calibrated.svp').write_bytes((task/'exports/voices.svp').read_bytes())
    # These are real session files too, but not task-owned direct calibration records.
    atomic_json(task/'session.json',json.loads(older.read_text(encoding='utf-8')))
    nested=task/'nested/calibration-deep';nested.mkdir(parents=True)
    atomic_json(nested/'session.json',json.loads(older.read_text(encoding='utf-8')))

    assert read_session(older)['voice']=='lead'
    assert read_session(newer)['voice']=='lead'
    row=task_row(task)
    sessions=calibration_ui._task_calibration_sessions(row)
    assert sessions==[newer.resolve(),older.resolve()]
    assert task/'session.json' not in sessions and nested/'session.json' not in sessions
    choices=calibration_ui._task_calibration_choices(row)
    assert len(choices)==2 and [Path(item['value']) for item in choices]==sessions
    assert all(task.as_posix() not in item['label'] for item in choices)
    assert '已有校准工程' in choices[0]['label'] and '主唱' in choices[0]['label']
    assert calibration_ui._selected_task_calibration(row,str(newer))==newer.resolve()
    try:
        calibration_ui._selected_task_calibration(row,str(task/'session.json'))
    except ValueError:
        pass
    else:
        raise AssertionError('A non-calibration* session must not be resumed')

    empty=root/'empty-task';empty.mkdir()
    try:
        calibration_ui._task_calibration_choices(task_row(empty))
    except ValueError as error:
        assert '校准记录' in str(error)
    else:
        raise AssertionError('An empty task must expose a concrete missing-record reason')

    owner=FakeOwner(row);dialog=ready_dialog(owner,row)
    before=(dialog.project.text,dialog.reference.text,dialog._busy,tuple(dialog.status))
    Picker.outcome=0;Picker.selection_row=row;Picker.selection_value=str(newer)
    with patch.object(results_ui,'TaskPickerDialog',Picker,create=True):
        dialog.resume_task()
    after=(dialog.project.text,dialog.reference.text,dialog._busy,tuple(dialog.status))
    assert after==before and not dialog.resume_session.called
    picked_owner,picked_parent,options=Picker.last
    assert picked_owner is owner and picked_parent is dialog
    assert options['title']=='从任务恢复校准' and options['confirm_text']=='恢复此校准'
    assert [Path(item['value']) for item in options['choices'](row)]==sessions

    dialog=ready_dialog(owner,row);dialog._busy=True
    with patch.object(results_ui,'TaskPickerDialog',side_effect=AssertionError('busy picker opened')) as picker_class:
        dialog.resume_task()
    picker_class.assert_not_called()
    assert dialog.project.text=='before' and dialog.status and dialog.status[-1][1]
    assert dialog.failure is None

    dialog=ready_dialog(owner,row);owner.processing_active=True
    with patch.object(results_ui,'TaskPickerDialog',side_effect=AssertionError('active picker opened')) as picker_class:
        dialog.resume_task()
    picker_class.assert_not_called()
    owner.processing_active=False

    dialog=ready_dialog(owner,row)
    Picker.outcome=1;Picker.selection_row=row;Picker.selection_value=str(older)
    with patch.object(results_ui,'TaskPickerDialog',Picker,create=True):
        dialog.resume_task()
    dialog.resume_session.assert_called_once_with(str(older.resolve()))
    assert dialog.failure is None

    source_tree=ast.parse((ROOT/'src/calibration_ui.py').read_text(encoding='utf-8-sig'))
    dialog_node=next(node for node in source_tree.body
                     if isinstance(node,ast.ClassDef) and node.name=='CalibrationDialog')
    resume_node=next(node for node in dialog_node.body
                     if isinstance(node,ast.FunctionDef) and node.name=='resume_task')
    picker_call=next(node for node in ast.walk(resume_node) if isinstance(node,ast.Call)
                     and isinstance(node.func,ast.Name) and node.func.id=='TaskPickerDialog')
    assert {item.arg for item in picker_call.keywords} >= {'title','choices','confirm_text'}
    assert any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
               and node.func.attr=='resume_session' for node in ast.walk(resume_node))

print('PASS: actual minimal SVP records, direct calibration*/session.json filtering, explicit multi-record choices, cancellation/busy immutability and exact resume_session delegation')
print('BOUNDARY: the full GUI picker state and fitting/validation chain are not re-executed here; shared picker and existing resume_session suites own those checks.')
