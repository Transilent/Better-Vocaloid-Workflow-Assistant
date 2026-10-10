"""Custom video/WAV destinations, reuse, task discovery and GUI directory selection."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / 'vendor')]
import common
import cover_art
import pipeline
import yt_dlp
from app import Window
# Load runtime/UI modules against the real app before isolating private state.
import workspace_ui
import components_ui
import updates_ui
from publication import load_bundle
from tests.support import ensure_fixture
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QFileDialog

config = common.load_config()
fixture = load_bundle(ensure_fixture(APP), 'bilibili')
application = QApplication([])
with tempfile.TemporaryDirectory(prefix='output-directory-', dir=APP / 'work') as folder:
    scratch = Path(folder)
    private = scratch / 'assistant'
    private.mkdir()
    common.atomic_json(private / 'config.json', config)
    destination = scratch / '自选保存 with spaces'
    another = scratch / '另一个保存目录'
    chosen = scratch / '再次选择'
    chosen.mkdir()
    with patch.object(common, 'ROOT', private):
        assert common.output_directory('') == private / 'jobs'
        assert common.output_directory('jobs') == private / 'jobs'
        common.save_output_directory(destination)
        job = pipeline.create_job('BV1ccWPeAEoZ', parent=destination, recognize_lyrics=False)
        request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
        assert job.parent == destination
        assert common.output_directory(request['output_directory']) == destination
        downloads = []

        class Downloader:
            def __init__(self, options):
                self.options = options
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def extract_info(self, url, download):
                assert download
                target = Path(self.options['outtmpl'].replace('%(ext)s', 'mp4'))
                assert target == job / 'video/source.mp4'
                shutil.copy2(fixture['video']['path'], target)
                downloads.append(target)
                return {'title': 'Synthetic video', 'duration': 3, 'format_id': 'fixture'}

        metadata = {'title': '测试音乐', 'owner': {'name': '测试作者'}, 'desc': '', 'bvid': request['bv']}
        with patch.object(yt_dlp, 'YoutubeDL', Downloader), \
             patch.object(pipeline, 'get_view', return_value=metadata), \
             patch.object(cover_art, 'job_cover', return_value=(Path(fixture['cover']['source']), {'url': 'fixture'})):
            pipeline.run(job, until='audio')
            assert common.file_hash(job / 'video/source.mp4') == fixture['video']['sha256']
            assert pipeline.check_audio(job / 'audio/source.wav').duration > 2.9
            with contextlib.redirect_stdout(io.StringIO()) as output:
                pipeline.run(job, until='audio')
            assert output.getvalue().count('已完成，跳过') == 3
            assert len(downloads) == 1, 'Resuming must use the video at its original location'
        copied = pipeline.reuse_download(job, parent=another, voice_mode='single', recognize_lyrics=False)
        assert copied.parent == another
        assert common.file_hash(copied / 'video/source.mp4') == common.file_hash(job / 'video/source.mp4')
        assert common.file_hash(copied / 'audio/source.wav') == common.file_hash(job / 'audio/source.wav')
        common.save_output_directory(chosen)
        assert {job, copied}.issubset(common.job_directories()), 'Changing the default must retain earlier task locations'
        assert not (private / 'jobs').exists(), 'Custom output must not create a second task under the default directory'

        invalid = scratch / 'file.mp4'
        invalid.write_bytes(b'file')
        try:
            pipeline.create_job('BV1ccWPeAEoZ', parent=invalid, remember=False)
            raise AssertionError('A file was accepted as an output parent')
        except ValueError as error:
            assert '文件夹' in str(error)
        with patch.object(Path, 'mkdir', side_effect=PermissionError('fixture')):
            try:
                pipeline.create_job('BV1ccWPeAEoZ', parent=scratch / 'not-writable', remember=False)
                raise AssertionError('Unwritable output accepted')
            except ValueError as error:
                assert '目录权限' in str(error)

        window = Window()
        window.show()
        application.processEvents()
        assert window.output_parent() == chosen
        assert window.results.tree.topLevelItemCount() == 2
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(destination)):
            QTest.mouseClick(window.choose_output_btn, Qt.LeftButton)
        assert window.options()['parent'] == destination
        assert common.output_directory() == destination
        window.set_running(True)
        assert not window.output_dir.isEnabled() and not window.choose_output_btn.isEnabled()
        window.set_running(False)
        window.close()
        window = Window()
        assert window.output_parent() == destination, 'The selected folder must survive a window restart'
        window.output_dir.clear()
        assert window.output_parent() == private / 'jobs'
        window.source.setText('BV1ccWPeAEoZ')
        with patch.object(window, 'launch') as launch:
            window.start_new()
            assert launch.called and window.job.parent == private / 'jobs'
        window.results.selected_job = job
        window.output_dir.setText(str(chosen))
        with patch.object(window, 'launch'):
            window.resume_last()
            assert window.job == job
            window.reprocess_last()
            assert window.job.parent == chosen
        assert (job / 'video/source.mp4').is_file()
        window.close()
        application.processEvents()
print('PASS: Unicode/space output directories, real video/WAV files, resume, reuse, remembered task roots, browse UI, restart and invalid destinations')
