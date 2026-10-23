"""Cross-module contract, stage-plan, subtitle, log-throughput and state checks."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import mido
from common import clean_lyrics
from pipeline import planned_steps, create_job
from subtitles import midi_cues
from v2m_runtime import configure, configure_llama_layout, validate_backend_contract
import progress_state

assert clean_lyrics('你，好！\r\n\n世 界。', preserve_lines=True) == '你好\n世界'
assert clean_lyrics('你，好！\r\n\n世 界。') == '你好世界'
configure(APP/'dependencies/vocal2midi2')
assert validate_backend_contract()['checked_hooks'] >= 10
from inference.API import game_api
with patch.object(game_api, '_find_word_nucleus_start', None):
    try:
        validate_backend_contract()
        raise AssertionError('Changed upstream hook accepted')
    except RuntimeError as error:
        assert '_find_word_nucleus_start' in str(error)

with tempfile.TemporaryDirectory(dir=APP/'work', prefix='module-audit-') as folder:
    root = Path(folder)
    try:
        configure_llama_layout(SimpleNamespace(load_model=lambda path: None, init_llama_lib=lambda: None), root)
        raise AssertionError('Changed native loader accepted')
    except RuntimeError as error:
        assert '路径接口' in str(error)
    job = create_job('BV1000000000', lyrics='你，好\n世界！', remember=False, parent=root)
    data = json.loads((job/'request.json').read_text(encoding='utf-8'))
    assert data['lyrics'] == '你好世界' and data['lyric_lines'] == ['你好', '世界']
    for projects in ([], ['svp']):
        for audios in (['wav'], ['flac']):
            request = dict(data, project_formats=projects, audio_formats=audios)
            steps, target, terminal = planned_steps(request)
            names = [key for key, _, _ in steps]
            expected = 'exports' if projects or audios != ['wav'] else 'midi'
            assert names[-1] == target == terminal == expected
            for until in names:
                partial, _, end = planned_steps(request, until)
                assert partial[-1][0] == end == until
    # Slow lyrics stay together in beats; reference line boundaries are exact.
    midi = mido.MidiFile(ticks_per_beat=480, charset='utf8')
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=1000000)]))
    track = mido.MidiTrack()
    for index, text in enumerate('你好世界'):
        track.append(mido.MetaMessage('lyrics', text=text, time=480 if index else 0))
    track.append(mido.MetaMessage('end_of_track', time=480)); midi.tracks.append(track)
    score = root/'slow.mid'; midi.save(score)
    assert [cue.text for cue in midi_cues(score)] == ['你好世界']
    assert [cue.text for cue in midi_cues(score, reference_lines=['你好', '世界'])] == ['你好', '世界']
    assert [cue.text for cue in midi_cues(score, reference_lines=['不匹配'])] == ['你好世界']
    with patch.object(progress_state, 'publish') as published:
        progress_state.from_log(root, '[BVWA Progress] '+json.dumps(
            {'voice':'backing','detail':'文件验证','done':1,'total':1}), '主唱')
        assert published.call_args.args == (root, 'midi', '和声 · 文件验证', 1, 1)
        published.reset_mock()
        for payload in ('bad json', '[]', '{"detail":"x","done":true,"total":1}'):
            progress_state.from_log(root, '[BVWA Progress] '+payload, '主唱')
        published.assert_not_called()
    import publish_browser
    original = json.loads((APP/'publish-selectors.json').read_text(encoding='utf-8'))
    with patch.object(publish_browser, 'ROOT', root):
        (root/'publish-selectors.json').write_text(json.dumps(original))
        assert publish_browser.load_selectors('bilibili')['url'].startswith('https://member.bilibili.com/')
        for changed in ([], dict(original, _meta={'schema':2,'revision':1}),
                        dict(original, bilibili={'url':'https://example.com/'})):
            (root/'publish-selectors.json').write_text(json.dumps(changed))
            try:
                publish_browser.load_selectors('bilibili')
                raise AssertionError('Incompatible/redirected upload selectors accepted')
            except ValueError: pass

from PyQt5.QtWidgets import QApplication, QTextEdit
from workflow_widgets import BufferedLog
application = QApplication.instance() or QApplication([])
editor = QTextEdit(); editor.document().setMaximumBlockCount(1800)
logger = BufferedLog(editor)
for index in range(10000):
    logger.append(f'<b>plain {index}</b>')
assert len(logger.pending) == 1800 and not editor.toPlainText()
logger.flush()
assert '<b>plain 9999</b>' in editor.toPlainText() and editor.document().blockCount() <= 1800
assert not logger.pending
print('PASS: native runtime contracts, ordered stage plans, preserved lyric lines, beat-aware subtitles, structured progress and bounded plain-text GUI logging')
