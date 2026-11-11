"""Exercise the Quick publication controls without using a platform account."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from time import monotonic, sleep
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QUrl
from PySide6.QtGui import QGuiApplication

from common import ROOT, atomic_json, file_hash, load_config
import pipeline
import publication
from quick_app import create_workspace


assert os.environ.get('VWA_TESTING') == '1'
app = QGuiApplication([])
engine, window, owner, modes = create_workspace(app, viewport=(1440, 900), scale=100)
pub = owner.publication


def item(name):
    node = window.findChild(QObject, name)
    assert node is not None, name
    return node


def click(name):
    node = item(name)
    assert node.property('visible') and node.property('enabled'), (name, pub.view['message'])
    assert QMetaObject.invokeMethod(node, 'click'), name
    app.processEvents()


def wait(label, check, timeout=15):
    limit = monotonic() + timeout
    while monotonic() < limit:
        app.processEvents()
        if check():
            print('PASS', label, flush=True)
            return
        sleep(.012)
    raise AssertionError((label, pub.view['message']))


def make_clip(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([load_config()['ffmpeg'], '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'color=' + color + ':s=144x108:r=25:d=2',
                    '-f', 'lavfi', '-i', 'sine=f=220:d=2', '-c:v', 'libx264',
                    '-c:a', 'aac', '-shortest', str(path)], check=True, timeout=25,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


try:
    (ROOT / 'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='publication-controls-') as directory:
        root = Path(directory)
        owner.navigate('publishing')
        app.processEvents()
        fixture = {'bvid': 'BV1Fixture01', 'title': '合成原曲', 'desc': 'STAFF: 测试',
                   'owner': {'name': '夹具'}, 'duration': 2, 'pic': 'https://i0.hdslb.com/test.jpg'}
        with patch.object(pipeline, 'get_view', return_value=fixture) as fetch, \
                patch.object(pipeline, 'validate_dependencies'):
            item('publicationBv').setProperty('text', fixture['bvid'])
            app.processEvents()
            click('publicationFetchBv')
            wait('BV lookup makes a metadata-only source', lambda: not pub.view['busy'] and bool(pub.view['job']))
            assert fetch.call_count == 1
        job = Path(pub.view['job'])
        status = json.loads((job / 'status.json').read_text(encoding='utf-8'))
        assert status['planned_steps'] == ['metadata'] and not (job / 'video/source.mp4').exists()
        video, original = root / 'finished.mp4', job / 'video/source.mp4'
        make_clip(video, 'red')
        make_clip(original, 'blue')
        before = file_hash(video)
        assert pub.chooseVideo(QUrl.fromLocalFile(str(video)))
        wait('local video is checked without registering at selection', lambda: not pub.view['busy'] and pub.view['video'] == str(video))
        click('publicationTitleHelper')
        item('publicationCreditSong').setProperty('text', '我的合成曲')
        item('publicationCreditVoice').setProperty('text', '歌姬')
        item('publicationCreditName').setProperty('text', '调音甲')
        click('publicationApplyCredits')
        assert pub.view['title'] == '【歌姬翻唱】我的合成曲'
        assert '本次调音：调音甲' in pub.view['description']
        assert pub.setField('ai', False) and pub.setField('rights', '核对署名')
        assert pub.nextStep()
        cover = root / 'cache-cover.jpg'
        publication.extract_cover(original, cover, .32)
        with patch('cover_art.job_cover', return_value=(cover, {})) as refresh, \
                patch('cover_art.cached_cover', return_value=(cover, {})):
            click('publicationFetchOriginalCover')
            wait('explicit cover refresh makes the original cover selectable', lambda: not pub.view['busy']
                 and pub.setField('coverMode', 'original'))
            refresh.assert_called_once_with(job, refresh=True)
        assert pub.setField('coverMode', 'original-frame')
        seconds = item('publicationCoverSeconds')
        seconds.setProperty('text', '0.47')
        assert QMetaObject.invokeMethod(seconds, 'editingFinished')
        assert abs(pub.view['coverSeconds'] - .47) < .0001
        with patch.object(pipeline, 'ensure_source_video', return_value=original):
            assert pub.previewCover()
            wait('actual preview uses the original-video frame', lambda: not pub.view['busy'] and bool(pub.view['coverPreview']))
            assert '本家原视频' in pub.view['message']
            assert pub.nextStep()
            assert pub.prepareBundle()
            wait('finished files produce a registered bundle', lambda: not pub.view['busy'] and bool(pub.view['bundle']))
        bundle = Path(pub.view['bundle'])
        manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['cover']['seconds'] == .47 and manifest['cover']['frame_source'] == 'original'
        assert Path(manifest['cover']['source']) == original and file_hash(video) == before
        assert manifest['final_publish'] == 'human_only'
        log = bundle / 'bilibili_浏览器.log'
        log.write_text('a' * 21000 + '\n最近一条日志', encoding='utf-8')
        # The controller derives an owned log path and reads a bounded tail.
        assert pub.browserLogTail().strip() == '最近一条日志'
        print('PASS distinct source frame, fractional time, actual bundle receipt and bounded log', flush=True)
finally:
    modes.close()
    owner.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
