"""Bound metadata caches without hiding tasks; protect referenced/edited publications."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
import common
import storage
import task_store
import storage_ui
from pipeline import job_lock

with tempfile.TemporaryDirectory(dir=APP/'work',prefix='storage-management-') as directory:
    root=Path(directory)
    with patch.object(common,'ROOT',root),patch.object(task_store,'ROOT',root),patch.object(storage,'ROOT',root):
        jobs=[]
        for index in range(5):
            job=root/'jobs'/str(index);job.mkdir(parents=True);jobs.append(job)
            common.atomic_json(job/'request.json',{'source_title':f'Song {index}','lyrics':'词'*1200})
            common.atomic_json(job/'status.json',{'state':'done'})
            os.utime(job/'status.json',(100+index,100+index))
        index_path=root/'cache/task-index.json'
        with patch.object(task_store,'INDEX_MAX_ENTRIES',2):
            rows=task_store.records()
            assert len(rows)==5 and [row['path'] for row in rows]==jobs[::-1]
            cached=json.loads(index_path.read_bytes())
            assert set(cached['entries'])=={str(path) for path in jobs[-2:]}
            with patch.object(task_store,'atomic_json',side_effect=AssertionError('Unchanged bounded cache was rewritten')):
                assert len(task_store.records())==5
            os.utime(jobs[0]/'status.json',(200,200))
            assert task_store.records()[0]['path']==jobs[0]
            assert str(jobs[0]) in json.loads(index_path.read_bytes())['entries']
            cached=json.loads(index_path.read_bytes());cached['entries'][str(jobs[0])]='broken'
            common.atomic_json(index_path,cached)
            assert len(task_store.records())==5
            before=index_path.read_bytes()
            assert task_store.records(check=lambda:True)==[] and index_path.read_bytes()==before
        with patch.object(task_store,'INDEX_MAX_BYTES',3000):
            assert len(task_store.records())==5
            assert index_path.stat().st_size<=3000 and not json.loads(index_path.read_bytes())['entries']
            index_path.write_bytes(b' '*4000)
            with patch.object(task_store,'job_directories',return_value=[]): assert task_store.records()==[]
            assert index_path.stat().st_size<=3000

        source=root/'finished.mp4';source.write_bytes(b'finished video stays outside bundles')
        packages=root/'publish-packages';packages.mkdir()
        def bundle(name,video=None):
            path=packages/name;path.mkdir()
            common.atomic_json(path/'manifest.json',{'video':{'path':str(video or source)},'source_job':str(jobs[0]),'cover':{}})
            (path/'bilibili_简介.txt').write_text('Original generated credits',encoding='utf-8')
            common.atomic_json(path/'cleanup-inventory.json',{'version':1,'kind':'publication-generated',
                'entries':[{'file':p.name,'bytes':p.stat().st_size,'sha256':common.file_hash(p)} for p in path.iterdir()]})
            return path
        disposable=bundle('disposable')
        selected=bundle('selected');edited=bundle('edited');unknown=bundle('unknown')
        legacy=bundle('legacy');(legacy/'cleanup-inventory.json').unlink()
        video_inside=bundle('video-inside',packages/'video-inside/master.mp4')
        (video_inside/'master.mp4').write_bytes(b'USER FINISHED VIDEO')
        (edited/'bilibili_简介.txt').write_text('User edited credits',encoding='utf-8')
        (unknown/'personal.txt').write_text('A user file',encoding='utf-8')
        common.atomic_json(root/'publish-ui.json',{'bundle':common.saved_path(selected)})
        assert [row['path'] for row in storage.publication_candidates()]==[disposable]
        protected={p:common.file_hash(p) for parent in (selected,edited,unknown,legacy,video_inside) for p in parent.iterdir() if p.is_file()}
        assert storage.clean([disposable])>0 and not disposable.exists()
        assert source.read_bytes()==b'finished video stays outside bundles'
        assert all(path.is_file() and common.file_hash(path)==sha for path,sha in protected.items())

        changed=bundle('changed-after-scan')
        assert changed in [row['path'] for row in storage.candidates()]
        (changed/'bilibili_简介.txt').write_text('Edited after scanning',encoding='utf-8')
        try:storage.clean([changed]);raise AssertionError('Newly edited publication removed')
        except ValueError:pass
        assert changed.exists()
        active=bundle('browser-active');profile=root/'cache/publish-browser/bilibili';profile.mkdir(parents=True)
        common.atomic_json(profile/'active.json',{'bundle':str(active)})
        assert active not in [row['path'] for row in storage.publication_candidates()]
        with job_lock(profile):
            try:storage.clean([]);raise AssertionError('Cleanup allowed during upload')
            except ValueError:pass
        preview=root/'cache/publish-preview.jpg';preview.write_bytes(b'regenerated preview')
        common.atomic_json(root/'publish-ui.json',{'inputs':{'cover_file':common.saved_path(preview)}})
        assert preview not in [row['path'] for row in storage.candidates()]
        common.atomic_json(root/'publish-ui.json',{'bundle':common.saved_path(selected)})
        assert storage.clean([preview])==len(b'regenerated preview') and not preview.exists()
        import _winapi
        link=packages/'linked';_winapi.CreateJunction(str(edited),str(link))
        try:
            assert link not in [row['path'] for row in storage.publication_candidates()]
            try:storage.clean([link]);raise AssertionError('Junction accepted for cleanup')
            except ValueError:pass
        finally:
            assert link.is_junction() and link.resolve()==edited.resolve()
            os.rmdir(link)
# Live publishing children share browser-temp with cache cleanup even after preparation ends.
polled = []
owner = SimpleNamespace(processing_active=False, components=SimpleNamespace(busy=lambda: False),
    updates=SimpleNamespace(busy=lambda: False),
    publishing=SimpleNamespace(busy=lambda: False, poll_browser=lambda: polled.append(True),
                               live_browser_active=lambda: True),
    video_mixdown=SimpleNamespace(busy=lambda: False), calibration_dialog=None)
active = storage_ui.StoragePage.other_operations(SimpleNamespace(owner=owner))
assert active == ('登录或上传浏览器',), active
assert polled == [True]
print('PASS: bounded task metadata, all tasks visible, stable reuse, oversized/corrupt caches, cancellation and publication protection')
