"""Independent lyric sources, timestamp-only subtitles and recorded task choices."""
import json,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt5.QtCore import QThread,pyqtSignal
from PyQt5.QtWidgets import QApplication,QMessageBox,QFileDialog,QLabel
from app import Window
from lyrics_dialog import LyricsDialog
from lyrics_sources import LyricReference
from subtitles import Cue,load
from pipeline import create_job
import mido,numpy as np,soundfile as sf
application=QApplication([])
w=Window();w.show();application.processEvents()
assert {'source','separation','reference','midi','audio','subtitles','output'} <= set(w.step_cards)
assert w.step_cards['midi'] is w.step_cards['reference']
assert w.step_cards['audio'] is w.step_cards['output']
assert not hasattr(w,'midi_backend')
assert not any(label.text()=='提取后端' for label in w.findChildren(QLabel))
w.workflow.choose_preset('内置 · 中文双轨')
w.workflow.apply_options(dict(w.workflow.options(),midi_backend='v1'))
assert w.options()['midi_backend']=='v2'
w.step_cards['reference'].toggle.setChecked(True)
w.lyrics.setPlainText('本地主唱')
w.backing_lyrics.setText('和声保留')
w.reference_mode.setCurrentIndex(w.reference_mode.findData('netease'))
application.processEvents()
assert w.netease_reference_panel.isVisible() and not w.local_reference_panel.isVisible()
try:
    w.options();raise AssertionError('Unselected NetEase match accepted')
except ValueError as exc: assert '选择网易云歌曲' in str(exc)

class ChosenLyrics:
    Accepted=1
    def __init__(self,parent,purpose='reference'):
        self.reference=LyricReference('网易主唱',[Cue(1,2,'网易主唱')],{'provider':'netease','song_id':'123'})
    def exec_(self): return self.Accepted
    def serialized(self): return {'origin':self.reference.origin,'cues':[{'start':1,'end':2,'text':'网易主唱'}]}

with patch('lyrics_dialog.LyricsDialog',ChosenLyrics): w.load_reference()
assert w.options()['lyrics']=='网易主唱' and w.backing_lyrics.text()=='和声保留'
assert w.options()['reference_mode']=='netease' and w.options()['lyric_reference']['origin']['song_id']=='123'
w.reference_mode.setCurrentIndex(w.reference_mode.findData('local'))
assert w.options()['lyrics']=='本地主唱'
w.reference_mode.setCurrentIndex(w.reference_mode.findData('netease'))
assert w.options()['lyrics']=='网易主唱'
before=(w.netease_lyrics.toPlainText(),w.lyrics.toPlainText(),dict(w.reference_metadata))
with patch('lyrics_dialog.LyricsDialog',ChosenLyrics): w.load_reference(subtitles=True)
assert (w.netease_lyrics.toPlainText(),w.lyrics.toPlainText(),w.reference_metadata)==before
assert w.subtitles.match_mode.currentData()=='netease'
assert w.subtitles.table.item(0,0).text()=='1'
assert not w.subtitles_enabled.isChecked() and not w.subtitles_enabled.isEnabled()
assert not hasattr(w.subtitles,'duration')
w.recognize.setChecked(False)
assert not w.lyrics.isEnabled() and not w.netease_lyrics.isEnabled()
w.recognize.setChecked(True)
assert w.lyrics.isEnabled() and w.netease_lyrics.isEnabled()
d=LyricsDialog(w,purpose='subtitles')
d.show_reference(LyricReference('无时间歌词',[],{'provider':'netease','song_id':'456'}))
assert not d.apply_button.isEnabled()
d.apply();assert d.result()!=d.Accepted
d.reject()
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='reference-modes-') as temporary:
    folder=Path(temporary)
    plain=folder/'local.txt';plain.write_text('作词：署名\n本地导入',encoding='utf-8')
    with patch.object(QFileDialog,'getOpenFileName',return_value=(str(plain),'')): w.import_reference('lead')
    assert w.reference_mode.currentData()=='local' and w.options()['lyrics']=='本地导入'
    assert w.netease_lyrics.toPlainText()=='网易主唱' and w.backing_lyrics.text()=='和声保留'
    try:
        load(plain);raise AssertionError('Untimed text converted to subtitles')
    except ValueError: pass
    with patch.object(QMessageBox,'warning') as warning:
        w.subtitles.load_file(plain)
        assert warning.called
    midi=mido.MidiFile(charset='utf8')
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=500000)]))
    for name,text,start in [('Lead Vocal','主',480),('Backing Vocal','和',960)]:
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('track_name',name=name),
            mido.MetaMessage('lyrics',text=text,time=start),mido.Message('note_on',note=60,velocity=80),
            mido.Message('note_off',note=60,time=480)]))
    midi_path=folder/'voices.mid';midi.save(midi_path)
    w.subtitles.load_file(midi_path)
    assert w.subtitles.match_mode.currentData()=='midi' and w.subtitles.midi_track.count()==2
    assert float(w.subtitles.table.item(0,0).text())==.5
    w.subtitles.midi_track.setCurrentIndex(1)
    assert w.subtitles.table.item(0,2).text()=='和' and float(w.subtitles.table.item(0,0).text())==1
    w.subtitles.table.item(0,2).setText('校准后的和声')
    w.subtitles.match_mode.setCurrentIndex(w.subtitles.match_mode.findData('netease'))
    assert w.subtitles.table.item(0,2).text()=='网易主唱'
    w.subtitles.match_mode.setCurrentIndex(w.subtitles.match_mode.findData('midi'))
    assert w.subtitles.table.item(0,2).text()=='校准后的和声'
    w.subtitles_enabled.setChecked(True)
    w.subtitles.match_mode.setCurrentIndex(w.subtitles.match_mode.findData('netease'))
    assert not w.subtitles_enabled.isChecked()
    w.subtitles.match_mode.setCurrentIndex(w.subtitles.match_mode.findData('midi'))
    w.reference_mode.setCurrentIndex(w.reference_mode.findData('netease'))
    audio=folder/'source.wav';sf.write(audio,np.zeros(44100,dtype='float32'),44100)
    w.output_dir.setText(str(folder/'jobs'))
    w.job=create_job(local_music=audio,remember=False,**w.options())
    request=json.loads((w.job/'request.json').read_text(encoding='utf-8'))
    assert request['midi_backend']=='v2' and request['reference_mode']=='netease' and request['lyrics']=='网易主唱'
    class IdleRunner(QThread):
        line=pyqtSignal(str);result=pyqtSignal(int)
        def __init__(self,job): super().__init__()
        def start(self): pass
    w.reference_mode.setCurrentIndex(w.reference_mode.findData('local'))
    with patch('app.Runner',IdleRunner): w.launch()
    assert w.reference_mode.currentData()=='netease' and w.options()['lyrics']=='网易主唱'
    w.timer.stop();w.set_running(False)
    w.job=None
w.close()
print('PASS: fixed V2, separate reference card/sources, main-only NetEase default, MIDI voice selection, subtitle drafts, no invented text timings and task restore')
