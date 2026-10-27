"""Offline boundary and current-segment binding checks for lyric references."""
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'src'));sys.path.insert(0,str(APP/'tests'))
from agent_control import Control,ControlError,io_path
from agent_workflow import Workflow
from agent_worker import dispatch
from test_agent_workflow import write_midi


class TimelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(dir=APP/'work',prefix='lyric-binding-')).resolve()
        self.addCleanup(lambda:shutil.rmtree(io_path(self.root)))
        self.assets=self.root/'assets';io_path(self.assets).mkdir()
        self.jobs=self.root/'jobs';io_path(self.jobs).mkdir()
        app=self.root/'app';io_path(app).mkdir()
        for source in APP.glob('*.py'):
            shutil.copy2(io_path(source),io_path(app/source.name))
        shutil.copy2(io_path(APP/'config.example.json'),io_path(app/'config.json'))
        config=self.root/'config.json'
        io_path(config).write_text(json.dumps({'schema':1,'application_root':str(app),
            'worker_python':sys.executable,'state_root':str(self.root/'state'),
            'allowed_asset_roots':[str(self.assets)],'allowed_job_roots':[str(self.jobs)],
            'source_commit':'private-lyric-timeline-fixture'}),encoding='utf-8')
        self.control=Control(config)
        self.midi=write_midi(self.assets/'vocal.mid')
        self.score=self.control.import_asset(self.midi)

    def source(self,name,text):
        target=self.assets/name;io_path(target).write_text(text,encoding='utf-8')
        return self.control.lyric_timeline_import(str(target))

    def test_immutable_word_source_keeps_bom_and_actual_times(self):
        source='\ufeff[0,1000](0,450,0)风(500,450,0)雨'
        artifact=self.source('word.yrc',source)
        self.assertEqual(artifact['summary']['granularity'],'word')
        self.assertEqual(artifact['summary']['word_count'],2)
        self.assertNotIn('lines',artifact)
        raw=self.control.artifact(artifact['summary']['raw_artifact_id'])
        self.assertEqual(raw['sha256'],hashlib.sha256(source.encode()).hexdigest())
        before=hashlib.sha256(io_path(self.midi).read_bytes()).hexdigest()
        result=self.control.lyric_timeline_review(self.score['id'],artifact['id'],start=0,end=1.5)
        self.assertEqual(result['summary']['unique_possible_offsets'],2)
        self.assertTrue(all(row['delta_seconds']==0 for row in result['hints']))
        self.assertFalse(result['automatic_patch'])
        self.assertEqual(before,hashlib.sha256(io_path(self.midi).read_bytes()).hexdigest())

    def test_workflow_only_binds_explicit_version_and_current_window(self):
        artifact=self.source('line.lrc','[00:00.00]风雨\n[00:20.00]远处歌词')
        workflow=Workflow(self.control)
        flow=workflow.create(score_artifact_id=self.score['id'],brief='逐段校准',review_mode='segments')
        with self.assertRaises(ControlError) as raised:
            workflow.references_set(flow['id'],flow['revision'],rationale='句子参考',lyrics_artifact_id=artifact['id'])
        self.assertEqual(raised.exception.code,'lyric_version_note_required')
        bound=workflow.references_set(flow['id'],flow['revision'],rationale='仅参考句子位置',
            lyrics_artifact_id=artifact['id'],lyric_version_note='自制片段，偏移0秒；不是声学真值。')
        page=workflow.review(bound['id'])
        evidence=page['reference_evidence']['lyrics_timeline']
        self.assertLessEqual(evidence['window']['duration_seconds'],30)
        self.assertEqual(len(evidence['reference_cues']['lines']),1)
        self.assertEqual(evidence['reference_cues']['words'],[])
        self.assertTrue(all('possible_offset' not in row for row in evidence['hints']))
        self.assertEqual(evidence['track_indices'],[page['segment']['track_index']])
        self.assertNotIn('notes',evidence)
        self.assertIn('notes',workflow.review(bound['id'],detail='full')['reference_evidence']['lyrics_timeline'])
        current=workflow._read(bound['id'])
        current=workflow._write(current,'fixture_checked',midi_segment_decisions={'fixture':{'checked':True}})
        reset=workflow.references_set(current['id'],current['revision'],rationale='重新确认时间偏移',
            lyrics_artifact_id=artifact['id'],lyrics_time_origin=.1,lyric_version_note='显式偏移+0.1秒')
        self.assertEqual(workflow._read(reset['id'])['midi_segment_decisions'],{})

    def test_reference_kind_window_and_immutable_identity(self):
        artifact=self.source('line.lrc','[00:00.00]风雨')
        with self.assertRaises(ControlError) as raised:
            self.control.lyric_timeline_review(self.score['id'],self.score['id'],start=0,end=1)
        self.assertEqual(raised.exception.code,'wrong_reference_kind')
        with self.assertRaises(ControlError):
            self.control.lyric_timeline_review(self.score['id'],artifact['id'],start=0,end=31)
        io_path(Path(artifact['path'])).write_text('{}',encoding='utf-8')
        with self.assertRaises(ControlError) as raised:
            self.control.lyric_timeline_review(self.score['id'],artifact['id'],start=0,end=1)
        self.assertEqual(raised.exception.code,'stale_artifact')

    def test_provider_is_fixed_host_and_uses_real_granularity(self):
        payload={'code':200,'lrc':{'lyric':'[00:01.00]自制句子'}}
        requests=[]
        def fetch(request,timeout):
            requests.append(request.full_url)
            return io.BytesIO(json.dumps(payload).encode())
        with patch('common.direct_urlopen',side_effect=fetch):
            result=dispatch(self.control,'lyric_timeline_fetch',{'song':'30352891'})
        self.assertEqual(result['timeline']['granularity'],'line')
        self.assertEqual(result['timeline']['lines'][0]['words'],[])
        self.assertEqual(result['origin']['song_id'],'30352891')
        self.assertTrue(requests[0].startswith('https://music.163.com/api/song/lyric/v1?'))
        with patch('lyrics_sources.search',return_value=[{'id':str(i)} for i in range(20)]):
            page=dispatch(self.control,'lyric_timeline_search',{'query':'fixture','limit':20,'offset':20})
        self.assertEqual(page['next_offset'],40)
        with self.assertRaises(ValueError):
            dispatch(self.control,'lyric_timeline_fetch',{'song':'https://example.com/30352891'})


if __name__=='__main__':unittest.main()
