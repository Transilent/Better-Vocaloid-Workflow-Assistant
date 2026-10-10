"""Timestamp/offset/encoding and real MIDI tempo-map subtitle conversion."""
import sys
import tempfile
from pathlib import Path
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from subtitles import Cue, lrc, plain, timed_csv, midi_cues, render, load
import mido

cues = lrc('[ar:作者]\n[offset:-500]\n[00:01.00]你好\n[00:02.00][00:04.00]世界\n[00:03.00]\n[00:06.000]終わり')
assert [(c.start,c.end,c.text) for c in cues] == [(.5,1.5,'你好'),(1.5,2.5,'世界'),(3.5,5.5,'世界'),(5.5,8.5,'終わり')]
assert lrc('[00:01]主唱\n[00:01]和声')[0].text == '主唱\n和声'
assert lrc('[00:01]<00:01.1>あ<00:01.3>い')[0].text == 'あい'
assert render([Cue(0,1.234,'你好')]).startswith('1\n00:00:00,000 --> 00:00:01,234\n你好')
assert render([Cue(0,1,'a')],'vtt',.5).startswith('WEBVTT\n\n1\n00:00:00.500')
assert '00:00:00,000 --> 00:00:00,500' in render([Cue(0,1,'a')],offset=-.5)
assert plain('一\n\n二',10)[1] == Cue(5,10,'二')
assert timed_csv('start,end,text\n1,2,你好')[0].start == 1
for function in (lambda: lrc('[00:99]bad'), lambda: plain('a',0), lambda: render([Cue(2,1,'bad')]),
                 lambda: render([Cue(0,1,'a')],offset=-2), lambda: render([Cue(float('nan'),1,'a')])):
    try:
        function()
        raise AssertionError('Invalid timing accepted')
    except ValueError:
        pass
with tempfile.TemporaryDirectory(dir=APP/'work',prefix='subtitle-') as folder:
    path = Path(folder)/'日本語.mid'
    midi = mido.MidiFile(charset='utf8')
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=500000),mido.MetaMessage('set_tempo',tempo=1000000,time=480)]))
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage('lyrics',text='あ',time=480),mido.Message('note_on',note=60,velocity=80),mido.Message('note_off',note=60,time=240),mido.MetaMessage('lyrics',text='い',time=0),mido.Message('note_on',note=62,velocity=80),mido.Message('note_off',note=62,time=480)]))
    midi.save(path)
    cues = midi_cues(path)
    assert cues[0].start == .5 and cues[0].end == 2 and cues[0].text == 'あい'
    assert load(path)[0] == cues
    textfile = Path(folder)/'lyrics.lrc'
    textfile.write_text('[00:01.00]你好',encoding='utf-8-sig')
    assert load(textfile)[0][0].text == '你好'
print('PASS: LRC metadata/repeated/empty/enhanced timestamps, SRT/VTT, plain-text estimate, Unicode MIDI tempo map')
