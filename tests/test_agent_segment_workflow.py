"""The Agent must finish bounded segments before export/style generation."""
import json
from pathlib import Path
import sys,tempfile,unittest
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'src'));sys.path.insert(0,str(APP/'tests'))
import mido
from agent_control import ControlError
from agent_score import read_score
from agent_segments import plan_segments,segment_notes
from agent_segment_workflow import progress,style_review,style_decide,combined_style
from agent_style import read_context,plan_style
from agent_workflow import Workflow
from test_agent_workflow import FakeControl

class SegmentControl(FakeControl):
    def call_worker(self,action,arguments):
        if action=='segment_plan':
            score=read_context(arguments['path']) if arguments['kind']=='style' else read_score(arguments['path'])
            return plan_segments(score)
        if action=='style_segment_context':
            context=read_context(arguments['path'])
            return {'notes':segment_notes(context,arguments['segment'])}
        return super().call_worker(action,arguments)
    def make_plan(self,kind,artifact_id,**arguments):
        if kind=='style':return {'id':'plan_'+'a'*32,'plan':plan_style(self.artifact(artifact_id)['path'],arguments['proposal'])}
        return super().make_plan(kind,artifact_id,**arguments)

class SegmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=APP/'work',prefix='segment-workflow-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.control=SegmentControl(self.root);self.workflow=Workflow(self.control)
        midi=mido.MidiFile(ticks_per_beat=480);track=mido.MidiTrack();midi.tracks.append(track)
        for index in range(80):
            track.append(mido.MetaMessage('lyrics',text='la',time=0))
            track.append(mido.Message('note_on',note=60+index%5,velocity=80,time=0))
            track.append(mido.Message('note_off',note=60+index%5,time=240))
        path=self.root/'assets/score.mid';midi.save(path)
        self.artifact=self.control.import_asset(path)
        self.flow=self.workflow.create(score_artifact_id=self.artifact['id'],brief='逐段检查',review_mode='segments')

    def complete(self,flow,note_edits=None):
        page=self.workflow.review(flow['id']);segment=page['segment']
        started=self.workflow.apply_review(flow['id'],flow['revision'],'只核对本段音符与原字。',
            note_edits=note_edits,segment_id=segment['id'])
        args=self.control.operation_get(started['operation_id'])['request']['arguments']
        output=self.root/('review-'+str(started['revision']));output.mkdir()
        result=self.workflow.execute_review(started['operation_id'],args,output,lambda:None,lambda text:None)
        self.control.set_operation(started['operation_id'],'succeeded',result=result)
        return self.workflow.advance(started['id'],started['revision'])

    def test_default_segment_reads_and_reviews_only_current_notes(self):
        page=self.workflow.review(self.flow['id'])
        self.assertEqual(page['total_notes'],80)
        self.assertLessEqual(len(page['evidence']['score']['notes']),16)
        self.assertGreater(self.flow['review_progress']['total'],1)
        following=self.complete(self.flow)
        self.assertEqual(following['stage'],'review')
        self.assertEqual(following['review_progress']['checked'],1)
        count=1
        while following['stage']=='review':following=self.complete(following);count+=1
        self.assertEqual(following['stage'],'reviewed')
        self.assertEqual(following['review_progress']['checked'],count)
        self.assertEqual(count,following['review_progress']['total'])

    def test_other_segment_edits_rejected_and_modified_segment_requires_reread(self):
        notes=read_score(self.artifact['path'])['notes']
        with self.assertRaises(ControlError) as raised:
            self.workflow.apply_review(self.flow['id'],self.flow['revision'],'跨段不应通过',
                note_edits=[{'note_id':notes[-1]['note_id'],'pitch':71}])
        self.assertEqual(raised.exception.code,'segment_edit_outside_scope')
        changed=self.complete(self.flow,[{'note_id':notes[0]['note_id'],'pitch':61}])
        self.assertEqual(changed['review_progress']['checked'],0)
        self.assertTrue(changed['last_segment_result']['requires_reread'])
        confirmed=self.complete(changed)
        self.assertEqual(confirmed['review_progress']['checked'],1)

    def test_style_decisions_are_segment_scoped_and_must_all_be_checked(self):
        q=705600000
        document={'version':153,'library':[],'time':{'tempo':[{'position':0,'bpm':120}],'meter':[]},
            'tracks':[{'name':'Lead','mainRef':{'blickOffset':0,'isInstrumental':False,'database':{}},
                'groups':[],'mainGroup':{'uuid':'fixture','parameters':{},'notes':[
                    {'onset':i*q//2,'duration':q//2,'pitch':60+i%3,'lyrics':'la'} for i in range(40)]}}]}
        target=self.root/'assets/style.svp';target.write_text(json.dumps(document),encoding='utf-8')
        artifact=self.control.import_asset(target);plan=plan_segments(read_context(target))
        raw=self.workflow._read(self.flow['id'])
        raw=self.workflow._write(raw,'fixture_exported',stage='project_ready',project_artifact_id=artifact['id'],
            style_segments=plan,style_segment_decisions={},export_summary={'report_sha256':'bound'},
            export_review={'decision':'accept','report_sha256':'bound'})
        context=style_review(self.workflow,raw['id'])
        self.assertLessEqual(len(context['context']['notes']),16)
        with self.assertRaises(ControlError):combined_style(raw)
        # An otherwise valid region on the next segment cannot be applied here.
        later=plan['segments'][-1]
        proposal={'track_indices':[0],'brief':'逐段','regions':[{'start':later['start_seconds'],
            'end':later['end_seconds'],'preset':'sad','strength':.4,'style_pitch':True,'rationale':'outside'}]}
        with self.assertRaises(ControlError) as raised:
            style_decide(self.workflow,raw['id'],raw['revision'],context['segment']['id'],'拒绝跨段',proposal)
        self.assertEqual(raised.exception.code,'segment_style_outside_scope')
        current=self.workflow.get(raw['id'])
        while not current['review_progress']['complete']:
            view=style_review(self.workflow,current['id'])
            current=style_decide(self.workflow,current['id'],current['revision'],view['segment']['id'],'本段保留原表现。')
        self.assertIsNone(combined_style(self.workflow._read(current['id'])))

    def test_distinct_track_styles_cannot_be_merged_into_global_targeting(self):
        value={'request':{'brief':'two voices'},'style_segments':{'segments':[
            {'fingerprint':'left'},{'fingerprint':'right'}]},'style_segment_decisions':{
            'left':{'proposal':{'track_indices':[0],'regions':[]}},
            'right':{'proposal':{'track_indices':[1],'regions':[]}}}}
        with self.assertRaises(ControlError) as raised:combined_style(value)
        self.assertEqual(raised.exception.code,'segmented_multi_track_style')

if __name__=='__main__':unittest.main()
