"""Isolated stdlib fixtures for durable project memory; no installed state."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
import vwa_project_memory as pm


def fact(key='audio.preview'):
    return {'id': key, 'kind': 'fact', 'title': '连续试听与混响',
            'body': 'GR 压缩减益和混响频谱必须使用当前实际数据。',
            'verification': 'verified', 'evidence': ['report.json'], 'anchors': ['src/engine.py']}


def task(key, deps=()):
    return {'id': key, 'kind': 'task', 'title': key, 'body': '待验证的工作',
            'acceptance': ['独立实测通过并保存回执'], 'depends_on': list(deps)}


def editable(row):
    return {k: v for k, v in row.items() if k not in ('revision', 'created_at', 'updated_at', 'basis_commit', 'basis_dirty', 'validation', 'history')}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        fixtures = SRC.parent / 'work/memory-fixtures'
        fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='vwa-memory-test-', dir=fixtures)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        (self.root / 'src/engine.py').write_text('version=1\n', encoding='utf-8')
        (self.root / 'report.json').write_text('{"passed":true,"skip_count":0}', encoding='utf-8')
        self.memory = pm.ProjectMemory(self.root)
        self.memory.init()

    def test_revision_history_conflict_and_retry_identity(self):
        one = self.memory.record(fact(), expected_revision=0, request_id='once')
        self.assertEqual(one['revision'], 1)
        self.assertEqual(self.memory.record(fact(), expected_revision=0, request_id='once'), one)
        with self.assertRaises(pm.MemoryError):self.memory.record(fact(), expected_revision=0)
        altered = fact();altered['body'] = '新的结论'
        with self.assertRaises(pm.MemoryError):self.memory.record(altered, expected_revision=1, request_id='once')
        self.memory.record(altered, expected_revision=1)
        self.assertEqual(len(self.memory.show('audio.preview', history=True)['history']), 2)

    def test_uncommitted_source_is_not_represented_as_a_clean_commit(self):
        with patch.object(pm,'basis_identity',return_value={'head':'a'*40,'dirty':True}):
            value=self.memory.record(fact(),expected_revision=0)
        self.assertEqual(value['basis_commit'],'a'*40)
        self.assertIs(value['basis_dirty'],True)

    def test_evidence_and_source_drift_are_not_current_truth(self):
        self.memory.record(fact(), expected_revision=0)
        self.assertEqual(self.memory.show('audio.preview')['validation']['freshness'], 'verified')
        (self.root / 'src/engine.py').write_text('version=2\n', encoding='utf-8')
        self.assertEqual(self.memory.show('audio.preview')['validation']['freshness'], 'stale')
        self.assertIn('audio.preview', self.memory.verify()['stale'])
        text = self.memory.bootstrap()
        self.assertIn('[stale]', text)
        self.assertIn('src/engine.py', text)

    def test_verified_claim_requires_evidence_and_actual_assertions(self):
        value = fact();value['evidence'] = []
        with self.assertRaises(pm.MemoryError):self.memory.record(value, expected_revision=0)
        value['evidence'] = [{'path': 'report.json', 'assertions': [{'pointer': '/passed', 'equals': False}]}]
        with self.assertRaises(pm.MemoryError):self.memory.record(value, expected_revision=0)
        value['evidence'][0]['assertions'][0]['equals'] = True
        self.memory.record(value, expected_revision=0)
        self.assertEqual(self.memory.show('audio.preview')['verification'], 'verified')

    def test_json_pointer_does_not_confuse_boolean_and_integer(self):
        value=fact();value['evidence']=[{'path':'report.json','assertions':[{'pointer':'/passed','equals':1}]}]
        with self.assertRaises(pm.MemoryError):self.memory.record(value, expected_revision=0)

    def test_task_dependencies_done_evidence_and_cycles(self):
        first=self.memory.record(task('prepare'),expected_revision=0)
        second=self.memory.record(task('verify', ['prepare']),expected_revision=0)
        self.assertEqual([r['id'] for r in self.memory.tasks(ready=True)],['prepare'])
        second=editable(second);second['status']='in_progress'
        with self.assertRaises(pm.MemoryError):self.memory.record(second,expected_revision=1)
        circular=editable(first);circular['depends_on']=['verify']
        with self.assertRaises(pm.MemoryError):self.memory.record(circular,expected_revision=1)
        completed=editable(first);completed['status']='done'
        with self.assertRaises(pm.MemoryError):self.memory.record(completed,expected_revision=1)
        completed.update(verification='verified',evidence=['report.json'])
        self.memory.record(completed,expected_revision=1)
        self.assertEqual([r['id'] for r in self.memory.tasks(ready=True)],['verify'])

    def test_task_lease_cannot_be_taken_while_active(self):
        self.memory.record(task('work'),expected_revision=0)
        self.memory.claim('work','session-a',expected_revision=1)
        self.assertEqual(self.memory.tasks(ready=True),[])
        with self.assertRaises(pm.MemoryError):self.memory.claim('work','session-b',expected_revision=2)
        future=pm.ProjectMemory(self.root,clock=lambda:'2099-01-01T00:00:00+00:00')
        self.assertEqual(future.claim('work','session-b',expected_revision=2)['owner'],'session-b')

    def test_chinese_and_english_search_and_rebuild_corrupt_index(self):
        self.memory.record(fact(),expected_revision=0)
        self.assertEqual(self.memory.search('混响')[0]['id'],'audio.preview')
        self.assertEqual(self.memory.search('reverb')[0]['id'],'audio.preview')
        (self.memory.store/'index.sqlite3').write_bytes(b'corrupt derived index')
        self.assertEqual(self.memory.search('GR')[0]['id'],'audio.preview')
        self.assertTrue(self.memory.verify()['ledger_valid'])

    def test_document_search_is_reference_only_and_refreshes_changed_files(self):
        (self.root/'docs').mkdir()
        path=self.root/'docs/history.md';path.write_text('# 历史调研\nSynthV 音高与项目格式。',encoding='utf-8')
        self.assertEqual(self.memory.index_documents(['docs'])['documents'],1)
        found=self.memory.search('SynthV')[0]
        self.assertEqual(found['kind'],'reference-document')
        self.assertEqual(found['validation']['freshness'],'reference-only')
        self.assertEqual(self.memory.entries(),{})
        path.write_text('# 最新调研\n新的验证边界。',encoding='utf-8')
        self.assertEqual(self.memory.search('SynthV'),[])
        self.assertEqual(self.memory.search('验证')[0]['title'],'最新调研')
        with self.assertRaises(pm.MemoryError):self.memory.index_documents(['jobs'])

    def test_superseded_records_and_cycles(self):
        self.memory.record(fact('old.preview'),expected_revision=0)
        newer=fact('new.preview');newer['supersedes']=['old.preview']
        self.memory.record(newer,expected_revision=0)
        self.assertNotIn('old.preview',[r['id'] for r in self.memory.search('混响')])
        older=fact('old.preview');older['supersedes']=['new.preview']
        with self.assertRaises(pm.MemoryError):self.memory.record(older,expected_revision=1)

    def test_backup_restore_and_no_overwrite(self):
        self.memory.record(fact(),expected_revision=0)
        backup=Path(self.memory.backup()['backup']);target=self.root/'workspace/restored-memory'
        self.assertTrue(self.memory.restore(backup,target)['ledger_valid'])
        recovered=pm.ProjectMemory(self.root,target)
        self.assertEqual(recovered.show('audio.preview')['revision'],1)
        with self.assertRaises(pm.MemoryError):self.memory.restore(backup,target)
        bundle=pm.load_json(backup);bundle['payload']['events'][0]['entry']['body']='tampered'
        broken=self.root/'broken-backup.json';broken.write_bytes(pm.encoded(bundle))
        with self.assertRaises(pm.MemoryError):self.memory.restore(broken,self.root/'another-store')
        self.assertFalse((self.root/'another-store').exists())

    def test_deleted_or_edited_event_is_detected(self):
        self.memory.record(fact(),expected_revision=0)
        other=fact('second');self.memory.record(other,expected_revision=0)
        first=sorted((self.memory.store/'ledger').glob('*.json'))[0]
        first.unlink()
        with self.assertRaises(pm.MemoryError):self.memory.verify()

    def test_deleted_committed_tail_is_detected(self):
        self.memory.record(fact(),expected_revision=0)
        self.memory.record(fact('second'),expected_revision=0)
        sorted((self.memory.store/'ledger').glob('*.json'))[-1].unlink()
        with self.assertRaises(pm.MemoryError):self.memory.verify()
        with self.assertRaises(pm.MemoryError):self.memory.recover_tip()

    def test_interrupted_tip_write_can_be_sealed_without_replaying_work(self):
        original=pm.atomic
        def fail_tip(path,value,root):
            if Path(path).name=='manifest.json':raise OSError('simulated interruption')
            return original(path,value,root)
        with patch.object(pm,'atomic',side_effect=fail_tip):
            with self.assertRaises(OSError):self.memory.record(fact(),expected_revision=0,request_id='recover-once')
        with self.assertRaises(pm.MemoryError):self.memory.verify()
        self.assertTrue(self.memory.recover_tip()['changed'])
        value=self.memory.record(fact(),expected_revision=0,request_id='recover-once')
        self.assertEqual(value['revision'],1)
        self.assertEqual(self.memory.verify()['events'],1)

    def test_cli_paths_resolve_against_explicit_project_root(self):
        (self.root/'record.json').write_bytes(pm.encoded(fact()))
        output=io.StringIO()
        with redirect_stdout(output):
            result=pm.main(['--root',str(self.root),'record','--file','record.json','--expect-revision','0'])
        self.assertEqual(result,0)
        backup=Path(self.memory.backup()['backup']).relative_to(self.root).as_posix()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pm.main(['--root',str(self.root),'restore','--backup',backup,'--into','workspace/cli-restored']),0)
        self.assertEqual(pm.ProjectMemory(self.root,self.root/'workspace/cli-restored').show('audio.preview')['revision'],1)

    def test_incomplete_write_does_not_replace_previous_event(self):
        self.memory.record(fact(),expected_revision=0)
        (self.memory.store/'.pending-crash').write_bytes(b'{')
        self.assertEqual(self.memory.show('audio.preview')['revision'],1)
        self.assertEqual(self.memory.verify()['pending_uncommitted_files'],1)

    def test_context_budget_and_no_silent_task_loss(self):
        for i in range(12):
            item=fact(f'fact-{i}');item['body']='重要事实。'*500
            self.memory.record(item,expected_revision=0)
        text=self.memory.bootstrap(budget=2500)
        self.assertLessEqual(len(text),2500)
        self.assertIn('未展开条目',text)
        for i in range(3):
            row=task(f'task-{i}');row['next_action']='必须续作。'*250
            self.memory.record(row,expected_revision=0)
        with self.assertRaises(pm.MemoryError):self.memory.bootstrap(budget=2500)

    def test_private_path_traversal_alias_and_credential_rejection(self):
        for name in ('../report.json','/report.json','C:/private.json','jobs/job.json',
                     'CACHE/account.json','workspace/config.json','src/report.json.', 'CON.json'):
            with self.subTest(path=name):
                row=fact();row['evidence']=[name]
                with self.assertRaises(pm.MemoryError):self.memory.record(row,expected_revision=0)
        row=fact();row['body']='github_pat_'+'a'*40
        with self.assertRaises(pm.MemoryError):self.memory.record(row,expected_revision=0)

    def test_hardlinked_write_and_junction_are_rejected(self):
        path=self.memory.store/'manifest.json';original=Path.lstat
        class Shared:
            st_mode=0o100644;st_nlink=2;st_file_attributes=0
        with patch.object(Path,'lstat',side_effect=lambda *a,**k:Shared()):
            with self.assertRaises(pm.MemoryError):pm.ordinary(path,self.root,writing=True)
        class Junction:
            st_mode=0o40755;st_nlink=1;st_file_attributes=1024
        with patch.object(Path,'lstat',side_effect=lambda *a,**k:Junction()):
            with self.assertRaises(pm.MemoryError):pm.ordinary(path,self.root)

    def test_concurrent_process_updates_have_one_winner(self):
        self.memory.record(fact(),expected_revision=0)
        code="import sys,json;sys.path.insert(0,sys.argv[1]);import vwa_project_memory as m;p=m.ProjectMemory(sys.argv[2]);r=p.show('audio.preview');r={k:v for k,v in r.items() if k not in ('revision','created_at','updated_at','basis_commit','basis_dirty','validation')};r['body']=sys.argv[3];p.record(r,expected_revision=1)"
        children=[subprocess.Popen([sys.executable,'-I','-B','-c',code,str(SRC),str(self.root),'worker-'+str(i)],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE) for i in range(2)]
        for child in children:child.communicate(timeout=15)
        self.assertEqual(sorted(c.returncode for c in children),[0,1])
        self.assertEqual(self.memory.show('audio.preview')['revision'],2)
        self.assertTrue(self.memory.verify()['ledger_valid'])

    def test_process_death_releases_os_lock(self):
        signal=self.root/'locked.signal'
        code=("import sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
              "import vwa_project_memory as m\np=m.ProjectMemory(sys.argv[2])\n"
              "with p.locked():\n Path(sys.argv[3]).write_text('ready')\n time.sleep(30)\n")
        child=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(SRC),str(self.root),str(signal)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            while not signal.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(signal.exists(),child.stderr.read().decode() if child.poll() is not None else 'lock handshake timed out')
        finally:
            if child.poll() is None:child.terminate()
            child.communicate(timeout=5)
        self.memory.record(fact(),expected_revision=0)
        self.assertTrue(self.memory.verify()['ledger_valid'])


if __name__=='__main__':unittest.main(verbosity=2)
