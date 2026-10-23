"""Exercise the actual task-file dialog and publication evidence using real media."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage,QColor
from app import Window
import common
import task_contract as contract
import publication
import results_ui

app=QApplication([])
window=Window()


def wait(dialog):
    deadline=time.monotonic()+30
    while dialog.worker is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    app.processEvents()
    assert dialog.worker is None,'File checker did not finish'


with tempfile.TemporaryDirectory(dir=APP/'work',prefix='contract-ui-') as temp:
    root=Path(temp);job=root/'job';job.mkdir()
    common.atomic_json(job/'request.json',{'bv':'BV1000000000','language':'zh','voice_mode':'single','tools':{}})
    common.atomic_json(job/'status.json',{'state':'done','steps':{}})
    with patch.object(window.video_mixdown,'busy',return_value=True):
        window.video_mixdown.source_job=job
        assert results_ui.active_task_operations(window,job)==('视频合成',)
    window.video_mixdown.source_job=None
    common.atomic_json(job/'source-info.json',{'title':'Fixture song','bvid':'BV1000000000','owner':{'name':'Fixture creator'}})
    common.atomic_json(job/'upstream-info.json',{})
    (job/'发布简介草稿.txt').write_text('Fixture credits',encoding='utf-8')
    video=root/'finished.mp4'
    subprocess.run([common.load_config()['ffmpeg'],'-nostdin','-v','error','-y','-f','lavfi','-i','color=c=green:s=320x180:r=10',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','1','-c:v','libx264','-c:a','aac',str(video)],check=True,capture_output=True)
    cover=root/'original.png';image=QImage(320,180,QImage.Format_RGB32);image.fill(QColor('#85bca9'));assert image.save(str(cover))
    from task_contract_ui import TaskFilesDialog
    dialog=TaskFilesDialog(window,job);dialog.show();wait(dialog)
    assert '检查未完成' not in dialog.status.text(),dialog.status.text()
    dialog.start(lambda:contract.bind(job,'video',video));wait(dialog)
    assert contract.handoff(job,verify=True)=='video_ready'
    assert any(dialog.tree.topLevelItem(i).text(1)=='成品视频' for i in range(dialog.tree.topLevelItemCount()))
    dialog.to_publication();wait(dialog)
    assert window.publishing.job.text()==str(job) and window.publishing.video.text()==str(video)
    fields={'bilibili':{'title':'Fixture cover','description':'Fixture credits'}}
    bundle=publication.prepare(job,video,fields,cover_file=cover,parent=root/'packages')
    assert contract.handoff(job,verify=True)=='awaiting_upload'
    contract.publication_state(bundle,'bilibili','waiting_review')
    assert contract.handoff(job,verify=True)=='awaiting_publish'
    contract.rebuild(job)
    assert contract.handoff(job,verify=True)=='awaiting_publish', 'Rebuild lost publication assets or selected inputs'
    contract.publication_state(bundle,'bilibili','cancelled')
    assert contract.handoff(job,verify=True)=='awaiting_publish'
    assert json.loads((job/'task.json').read_text(encoding='utf-8'))['publication']['final_publish']=='human_only'
    # Editing a prepared cover invalidates the snapshot without touching the video.
    (bundle/'cover.jpg').write_bytes(b'changed-cover')
    assert contract.handoff(job,verify=True)=='needs_review'
    assert video.is_file()
    # Full source-task identity accompanies navigation into the optional video tool.
    (job/'video').mkdir();original=job/'video/source.mp4';original.write_bytes(video.read_bytes())
    window.video_mixdown.use_source_job(job)
    assert window.video_mixdown.source_job==job and window.video_mixdown.fields['video'].text()==str(original)
    # Closing a dialog in the completion callback must not destroy a running QThread.
    second=TaskFilesDialog(window,job);wait(second)
    second.start(callback=lambda _:second.accept());wait(second)
    assert not second.isVisible()
window.close();app.processEvents()
print('PASS: offscreen file registration/navigation, real FFmpeg media, publication assets and human-only publish state')
