"""Export review decisions must name the exact report and its visible losses."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent_control import digest
from agent_supervision import summarize_export,validate_export_review,SupervisionError


class ExportSupervisionTests(unittest.TestCase):
    def fixture(self):
        path=str((Path(__file__).resolve().parent/'fixture.svp').resolve())
        row={'format':'svp','candidate_path':path,'candidate_sha256':'b'*64,
             'verified_notes':2,'verified_lyrics':2,'tempo_verified':False,
             'timing_verified':True,'time_signature_verified':True,
             'expected_tempo':[[0,120]],'output_tempo':[[0,120],[1920,120]],
             'loss_codes':['tempo_table_normalized']}
        receipt={'schema_version':1,'kind':'vwa-export-receipt','source_sha256':'a'*64,
                 'plan_sha256':'c'*64,'candidates':[row],'verified':{'notes':2,'lyrics':2},
                 'conversion_losses':[
                    {'code':'midi_velocity_not_mapped','message':'velocity omitted'},
                    {'code':'midi_controllers_not_automation','count':0},
                    {'code':'other_midi_metadata_not_mapped','count':0},
                    {'code':'vsqx_lyrics_restored_after_dump'}]}
        receipt['receipt_sha256']=digest(receipt)
        audit={'source_sha256':'a'*64,'status':'verified','candidates':[
            {'format':'svp','candidate_path':path,'candidate_sha256':'b'*64}],
            'blockers':[],'warnings':[],'inference_performed':False}
        audit['audit_sha256']=digest(audit)
        return {'receipt':{'receipt':receipt,'independent_audit':audit},
                'artifacts':[{'id':'art_fixture','path':path,'sha256':'b'*64}]}

    def summary(self,result=None):
        return summarize_export(result or self.fixture(),'a'*64,'op_fixture')

    def decision(self,report,**changes):
        return {'report_sha256':report['report_sha256'],'decision':'accept',
                'rationale':'Reviewed synthetic notes/lyrics/seconds and declared losses.',
                'accepted_loss_codes':report['required_loss_codes'],**changes}

    def assert_code(self,code,fn,*args):
        with self.assertRaises(SupervisionError) as caught:fn(*args)
        self.assertEqual(caught.exception.code,code)

    def test_default_summary_exposes_losses_and_separates_ticks_from_seconds(self):
        report=self.summary()
        self.assertTrue(report['can_accept'])
        self.assertIn('tempo_table_normalized',report['required_loss_codes'])
        self.assertIn('other_midi_metadata_not_mapped',report['required_loss_codes'])
        self.assertNotIn('midi_controllers_not_automation',report['required_loss_codes'])
        self.assertNotIn('vsqx_lyrics_restored_after_dump',report['required_loss_codes'])
        candidate=report['candidates'][0]
        self.assertTrue(candidate['tick_roundtrip_verified'])
        self.assertFalse(candidate['tempo_table_equal'])
        self.assertEqual(report['independent_audit']['status'],'verified')
        accepted=validate_export_review(report,self.decision(report))
        self.assertTrue(accepted['caller_reviewed'])
        self.assertFalse(accepted['acoustic_validation'])

    def test_exact_report_and_each_required_loss_must_be_acknowledged(self):
        report=self.summary()
        self.assert_code('export_review_conflict',validate_export_review,report,
                         self.decision(report,report_sha256='0'*64))
        self.assert_code('export_losses_unacknowledged',validate_export_review,report,
                         self.decision(report,accepted_loss_codes=[]))
        self.assert_code('invalid_export_review',validate_export_review,report,
                         self.decision(report,accepted_loss_codes=['invented']))

    def test_receipt_hash_source_and_registry_binding_cannot_be_forged(self):
        for change in ('receipt','source','registry'):
            result=self.fixture()
            if change=='receipt':result['receipt']['receipt']['verified']['notes']=3
            elif change=='source':result['receipt']['receipt']['source_sha256']='0'*64
            else:result['artifacts'][0]['sha256']='0'*64
            self.assert_code('export_report_mismatch',self.summary,result)

    def test_independent_audit_is_hash_and_candidate_bound(self):
        result=self.fixture()
        result['receipt']['independent_audit']['status']='differences'
        self.assert_code('export_report_mismatch',self.summary,result)
        result=self.fixture();audit=result['receipt']['independent_audit']
        audit['candidates'][0]['candidate_sha256']='0'*64
        audit['audit_sha256']=digest({k:v for k,v in audit.items() if k!='audit_sha256'})
        self.assert_code('export_report_mismatch',self.summary,result)

    def test_real_differences_block_accept_but_allow_revision(self):
        result=self.fixture();audit=result['receipt']['independent_audit']
        audit.update(status='differences',blockers=[{'code':'note_seconds_mismatch','message':'actual drift'}])
        audit['audit_sha256']=digest({k:v for k,v in audit.items() if k!='audit_sha256'})
        report=self.summary(result)
        self.assertFalse(report['can_accept'])
        self.assert_code('export_blockers',validate_export_review,report,self.decision(report))
        revised=validate_export_review(report,self.decision(report,decision='needs_revision',accepted_loss_codes=[]))
        self.assertEqual(revised['decision'],'needs_revision')

    def test_missing_audit_is_unknown_and_report_tampering_is_rejected(self):
        result=self.fixture();del result['receipt']['independent_audit']
        report=self.summary(result)
        self.assertEqual(report['independent_audit']['status'],'unknown')
        self.assertIn('independent_audit_unavailable',report['required_loss_codes'])
        altered=copy.deepcopy(report);altered['blockers'].append({'code':'late'})
        self.assert_code('export_report_mismatch',validate_export_review,altered,self.decision(report))

    def test_written_pronunciation_evidence_is_visible_and_candidate_bound(self):
        result=self.fixture()
        native={'source_sha256':'b'*64,'format':'svp','notes':[
            {'note_ref':'written-fixture','phonemes':'sh an','acoustic_verification':False}]}
        result['receipt']['pronunciation_evidence']=[{'format':'svp','source_sha256':'b'*64,'report':native}]
        report=self.summary(result)
        self.assertEqual(report['candidates'][0]['pronunciation'],native)
        result['receipt']['pronunciation_evidence'][0]['source_sha256']='0'*64
        self.assert_code('export_report_mismatch',self.summary,result)

    def test_malformed_or_nonfinite_pronunciation_report_is_rejected(self):
        result=self.fixture();result['receipt']['pronunciation_evidence']=None
        self.assert_code('invalid_export_report',self.summary,result)
        result=self.fixture();result['receipt']['pronunciation_evidence']=[
            {'format':'svp','source_sha256':'b'*64,'report':{'source_sha256':'b'*64,'invalid':float('nan')}}]
        self.assert_code('invalid_export_report',self.summary,result)

    def test_unavailable_native_phonemes_require_explicit_acknowledgement(self):
        result=self.fixture()
        result['receipt']['pronunciation_evidence']=[{'format':'svp','source_sha256':'b'*64,
            'report':{'source_sha256':'b'*64,'format':'svp','status':'unknown',
                      'evidence_available':False,'notes':[],'reason':'unsupported overlap'}}]
        report=self.summary(result)
        self.assertEqual(report['candidates'][0]['pronunciation']['status'],'unknown')
        self.assertIn('native_phonemes_unavailable',report['required_loss_codes'])
        self.assert_code('export_losses_unacknowledged',validate_export_review,report,
            self.decision(report,accepted_loss_codes=[c for c in report['required_loss_codes']
                                                     if c!='native_phonemes_unavailable']))


if __name__=='__main__':unittest.main()
