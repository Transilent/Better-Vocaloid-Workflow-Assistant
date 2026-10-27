"""Recovery/lifecycle/wait contracts derived from the real-song feedback."""
import copy
from pathlib import Path
import sys,tempfile,threading,time,unittest
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'src'));sys.path.insert(0,str(APP/'tests'))
from agent_control import Control,ControlError,read_json
from agent_errors import error_payload
from agent_workflow import Workflow
from test_agent_workflow import FakeControl,write_midi


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=APP/'work',prefix='song-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.control=FakeControl(self.root);self.workflow=Workflow(self.control)
        self.score=self.control.import_asset(write_midi(self.root/'assets/source.mid'))
        self.flow=self.workflow.create(score_artifact_id=self.score['id'],brief='real-song recovery fixture',
            idempotency_key='same-flow-recovery')

    def fail_export(self):
        operation=self.control.start_operation('export_apply',{'plan_id':'old'},'fixture-export')
        self.control.set_operation(operation['id'],'failed',error={'code':'export_converter_rejected','message':'15ms overlap'})
        raw=self.workflow._read(self.flow['id'])
        self.workflow._write(raw,'fixture_export_started',stage='exporting',operation_id=operation['id'],
            active_plan_id='old',export_review={'decision':'accept','report_sha256':'old-report'},
            project_artifact_id='old-candidate',project_artifacts=[{'id':'old-candidate'}])
        return self.workflow.get(self.flow['id'])

    def test_failed_export_rebinds_same_flow_and_retains_old_decision(self):
        failed=self.fail_export()
        failed_operation=copy.deepcopy(self.control.operation_get(failed['operation_id']))
        other=self.control.import_asset(write_midi(self.root/'assets/corrected.mid'))
        recovered=self.workflow.rebind_score(failed['id'],failed['revision'],other['id'],'fix the overlap')
        self.assertEqual(recovered['id'],failed['id'])
        self.assertEqual(recovered['stage'],'review')
        self.assertEqual(recovered['score_artifact_id'],other['id'])
        self.assertIsNone(recovered['export_review'])
        self.assertEqual(recovered['project_artifacts'],[])
        prior=read_json(Path(recovered['recovery_history'][-1]['path']))
        self.assertEqual(prior['export_review']['report_sha256'],'old-report')
        self.assertEqual(prior['operation_id'],failed_operation['id'])
        self.assertEqual(self.control.operation_get(failed_operation['id']),failed_operation)
        self.assertEqual(len(list((self.control.state/'workflows').glob('*.json'))),1)
        decision=self.workflow.apply_review(recovered['id'],recovered['revision'],'review the corrected score')
        self.control.set_operation(decision['operation_id'],'succeeded',result={
            'artifact_id':other['id'],'source_sha256':other['sha256'],'caller_reviewed':True})
        reviewed=self.workflow.advance(decision['id'],decision['revision'])
        self.assertEqual(reviewed['stage'],'reviewed')
        self.assertEqual(self.workflow.advance(reviewed['id'],reviewed['revision'])['stage'],'exporting')

    def test_active_operation_and_stale_revision_cannot_rebind(self):
        reviewing=self.workflow.apply_review(self.flow['id'],self.flow['revision'],'review')
        with self.assertRaises(ControlError) as raised:
            self.workflow.rebind_score(reviewing['id'],reviewing['revision'],self.score['id'],'retry')
        self.assertEqual(raised.exception.code,'workflow_busy')
        with self.assertRaises(ControlError) as raised:
            self.workflow.rebind_score(reviewing['id'],0,self.score['id'],'stale')
        self.assertEqual(raised.exception.code,'workflow_conflict')

    def test_list_close_archive_clone_keep_evidence_and_reset_reviews(self):
        failed=self.fail_export()
        closed=self.workflow.close(failed['id'],failed['revision'],'superseded')
        self.assertEqual(closed['lifecycle'],'closed')
        self.assertEqual(closed['next']['tool'],'vwa_workflow_clone')
        with self.assertRaises(ControlError) as raised:self.workflow.advance(closed['id'],closed['revision'])
        self.assertEqual(raised.exception.code,'workflow_closed')
        selected=self.workflow.list(status='closed',query='real-song',limit=1)
        self.assertEqual(selected['total'],1)
        self.assertEqual(selected['workflows'][0]['id'],closed['id'])
        archived=self.workflow.close(closed['id'],closed['revision'],'retain',archive=True)
        self.assertEqual(archived['lifecycle'],'archived')
        # A code update does not prevent cloning stored evidence into a new flow.
        self.control.code_revision='new-code'
        child=self.workflow.clone(archived['id'],archived['revision'],reason='continue current code')
        self.assertNotEqual(child['id'],archived['id'])
        self.assertEqual(child['stage'],'review')
        self.assertNotIn('export_review',child)
        self.assertEqual(child['cloned_from']['workflow_id'],archived['id'])
        self.assertEqual(self.workflow.get(archived['id'])['lifecycle'],'archived')


class WaitTests(unittest.TestCase):
    def control(self):
        control=object.__new__(Control)
        self.value={'id':'op_'+'a'*32,'state':'running','updated':1.,'created':time.time(),
            'request':{'action':'fixture'},'progress':{'done':1,'total':4}}
        control.operation_get=lambda identifier:copy.deepcopy(self.value)
        return control

    def test_timeout_returns_current_state_without_result_payload(self):
        control=self.control();start=time.monotonic()
        value=control.operation_wait(self.value['id'],20)
        self.assertTrue(value['timed_out'])
        self.assertEqual(value['progress']['done'],1)
        self.assertLess(time.monotonic()-start,.5)
        self.assertEqual(value['progress_source'],'operation_record')

    def test_change_and_completion_wake_without_external_sleep_calls(self):
        control=self.control()
        first=control.operation_wait(self.value['id'],0)
        timer=threading.Timer(.03,lambda:self.value.update(updated=2.,progress={'done':2,'total':4}))
        timer.start();self.addCleanup(timer.join)
        changed=control.operation_wait(self.value['id'],1000,first['poll_cursor'],'change')
        self.assertTrue(changed['changed']);self.assertFalse(changed['timed_out'])
        timer2=threading.Timer(.03,lambda:self.value.update(state='succeeded',updated=3.,result={'verified':True}))
        timer2.start();self.addCleanup(timer2.join)
        final=control.operation_wait(self.value['id'],1000,detail='full')
        self.assertEqual(final['result'],{'verified':True})

    def test_invalid_wait_and_error_recovery_fields(self):
        control=self.control()
        for timeout in (-1,60001,True):
            with self.assertRaises(ControlError):control.operation_wait(self.value['id'],timeout)
        payload=error_payload(ControlError('monophonic_overlap','tiny overlap',{'seconds':.015}))
        self.assertEqual(payload['details']['seconds'],.015)
        self.assertFalse(payload['retryable'])
        self.assertIn('vwa_workflow_rebind_score',payload['remediation'])


if __name__=='__main__':unittest.main()
