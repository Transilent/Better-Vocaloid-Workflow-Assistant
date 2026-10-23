"""Syllable loss, cross-phrase merging, missing notes and pitch-only noise."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2m_runtime import configure
configure(ROOT/'dependencies/vocal2midi2')
from inference.API import game_api
from inference.io.note_io import NoteInfo
from inference.API.rmvpe_api import RmvpeResult
from transcription_quality import (chinese_boundaries,refine_notes,_plateau_splits,syllables_from_alignment,
    install,expand_reference_match,collapsed_windows,connect_phrase_notes,retry_collapsed_alignment,unvoiced_from_alignment,
    japanese_tail_windows,accept_japanese_tails,japanese_boundaries,continue_releases,confirm_note_boundaries,
    aligned_coverage_gaps)


def word(start,end,text,phone='a'):
    return SimpleNamespace(start=start,end=end,text=text,
        phonemes=[SimpleNamespace(start=start,end=end,text=phone)])


# Read known unvoiced phones from real alignment times. Voiced consonants,
# vowels, unknown phones, short labels and missing chunks supply no veto.
phone_outcome=SimpleNamespace(pred_dict={'chunk_0':(None,1.,[
    word(0,.1,'ka','ja/k'),word(.1,.2,'a','a'),word(.2,.3,'ma','m'),
    word(.3,.4,'na','N'),word(.4,.5,'za','z'),word(.5,.51,'ta','t'),word(.6,.7,'?','unknown')])})
assert unvoiced_from_alignment(phone_outcome,[{'offset':5.},{'offset':9.}],'ja')==[{'start':5.,'end':5.1,'phone':'k'}]
assert unvoiced_from_alignment(phone_outcome,[{'offset':5.}],'en')==[]
# An ASR-recognized final mora can collapse to 10 ms after SP even though a
# sung tail exists before it. Locate the failed anchor without inventing text.
tail_words=[word(.1,.4,'hi','i'),word(.4,.8,'SP','SP'),word(.8,.81,'bo','o'),word(.81,1.,'SP','SP')]
tail_outcome=SimpleNamespace(pred_dict={'chunk_0':(None,1.,tail_words)},chars_dict={'chunk_0':['ひ','ぼ']})
windows=japanese_tail_windows(tail_outcome,[{'offset':2.}])
assert len(windows)==1 and windows[0]['lyric']=='ぼ' and abs(windows[0]['collapsed_seconds']-.01)<1e-9
assert japanese_tail_windows(SimpleNamespace(pred_dict={'chunk_0':(None,1.,[word(.1,.4,'hi','i'),word(.4,.6,'bo','o')])},chars_dict={}),[{}])==[]
# A collapsed recognized ending needs no artificial SP before it.
contiguous=SimpleNamespace(pred_dict={'chunk_0':(None,1.,[word(.1,.4,'hi','i'),word(.4,.41,'bo','o'),word(.41,1.,'SP','SP')])},chars_dict={'chunk_0':['hi','bo']})
assert abs(japanese_tail_windows(contiguous,[{}])[0]['tail_end']-.66)<1e-9
# Dropping a zero-duration / unpronounceable unit must still consume its token;
# later words must not receive the missing word's character.
words=[word(0,.4,'zhong'),word(.4,.4,'wu'),word(.4,.8,'yun')]
d,v,l=chinese_boundaries(words,['中','乌','云'],game_api)
assert [x for x,flag in zip(l,v) if flag]==['中','云']
words=[word(0,.4,'zhong'),word(.4,.6,'cl','cl'),word(.6,1,'yun')]
d,v,l=chinese_boundaries(words,['中','乌','云'],game_api)
assert [x for x,flag in zip(l,v) if flag]==['中','云']
assert abs(sum(d)-1)<1e-9
# Kana lyrics consume collapsed and consonant-only mora tokens, too.
ja_words=[word(0,.3,'hi','i'),word(.3,.3,'bo','o'),word(.3,.6,'ka','a')]
_,voiced,lyrics=japanese_boundaries(ja_words,['ひ','ぼ','か'],game_api)
assert [lyric for lyric,flag in zip(lyrics,voiced) if flag]==['ひ','か']
_,voiced,lyrics=japanese_boundaries([word(0,.1,'cl','cl'),word(.1,.4,'bo','o')],['っ','ぼ'],game_api)
assert [lyric for lyric,flag in zip(lyrics,voiced) if flag]==['ぼ']
# Do not extend an earlier vowel through a long pause when HFA omitted SP.
words=[word(0,.4,'zhong'),word(2,2.5,'wu')]
d,v,l=chinese_boundaries(words,['中','乌'],game_api)
assert d==[.4,1.6,.5] and v==[1,0,1]
# A long consonant is not a rest. Keep adjacent vowels connected when the
# aligned words touch, while a real phrase pause still remains above.
words=[word(0,.4,'zhong'),word(.4,1,'sheng')]
words[1].phonemes[0].start=.59
d,v,l=chinese_boundaries(words,['中','声'],game_api)
assert v==[1,1] and abs(d[0]-.59)<1e-9

# Legato closes only short gaps backed by contiguous syllable alignment.
connected=[NoteInfo(.2,.45,60,'中'),NoteInfo(.6,1.,62,'声'),NoteInfo(1.5,2.,64,'你')]
anchors=[{'start':.2,'end':.6,'lyric':'中'},{'start':.6,'end':1.,'lyric':'声'},
         {'start':1.5,'end':2.,'lyric':'你'}]
changes=connect_phrase_notes(connected,anchors)
assert len(changes)==1 and connected[0].offset==.6 and connected[1].offset==1.
assert connect_phrase_notes([NoteInfo(0,.3,60,''),NoteInfo(.4,.8,60,'')],[])==[]
missing=[NoteInfo(.2,.4,60,'甲'),NoteInfo(.7,1.,62,'丙')]
assert not connect_phrase_notes(missing,[{'start':.2,'end':.4,'lyric':'甲'},
    {'start':.4,'end':.7,'lyric':'乙'},{'start':.7,'end':1.,'lyric':'丙'}])

# A collapsed reference cluster is re-aligned in a larger local window,
# retaining the existing model state and word sequence. This test simulates
# the decoder protocol without loading another model or changing weights.
import tempfile
import soundfile as sf
from inference.HubertFA.tools.align_word import Word,Phoneme
def aligned(a,b,text,nucleus=None):
    w=Word(a,b,text)
    if nucleus is not None and nucleus>a:w.append_phoneme(Phoneme(a,nucleus,'b'))
    w.append_phoneme(Phoneme(a if nucleus is None else nucleus,b,'SP' if text=='SP' else 'a'))
    return w
broken=[aligned(0,.5,'da'),aligned(.5,.52,'bei',.51),aligned(.52,.6,'pan'),
    aligned(.6,.8,'le',.79),aligned(.8,1.2,'SP'),aligned(1.2,1.6,'wo',1.58),
    aligned(1.6,1.8,'de'),aligned(1.8,2.,'zhe'),aligned(2.,2.3,'sheng'),aligned(2.3,3.5,'xia')]
assert collapsed_windows(broken,game_api)==[(1,9)]
assert not collapsed_windows([word(0,.02,'a'),word(.02,.05,'i'),word(.05,.4,'u')],game_api)
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='alignment-retry-test-') as temp:
    folder=Path(temp);sf.write(folder/'chunk_0.wav',np.zeros(64000),16000)
    model=SimpleNamespace(dataset=['original'],predictions=['original'],alignment_errors={'original':True})
    prior=(model.dataset,model.predictions,model.alignment_errors)
    def retry(model,folder,**kwargs):
        assert (folder/'retry.lab').read_text(encoding='utf-8')=='bei pan le wo de zhe sheng xia'
        model.dataset=['retry'];model.predictions=['retry'];model.alignment_errors={}
        starts=[0,.35,.7,.85,1.2,1.4,1.65,1.9,3.1]
        labels=['bei','pan','le','wo','de','zhe','sheng','xia']
        replacement=[aligned(a,b,label) for a,b,label in zip(starts,starts[1:],labels)]
        return {'retry':(folder/'retry.wav',3.1,replacement)}
    pred={'chunk_0':(folder/'chunk_0.wav',4.,broken.copy())}
    audit=retry_collapsed_alignment(model,folder,pred,'zh',retry)
    assert audit and audit[0]['accepted'] and audit[0]['after_usable']>audit[0]['before_usable']
    assert all(a is b for a,b in zip(prior,(model.dataset,model.predictions,model.alignment_errors)))
    assert [w.text for w in pred['chunk_0'][2]]==['da','bei','pan','le','wo','de','zhe','sheng','xia']
    assert retry_collapsed_alignment(model,folder,pred,'ja',retry)==[]
    unchanged=broken.copy()
    def wrong_sequence(model,folder,**kwargs):
        return {'retry':(folder/'retry.wav',3.1,[aligned(0,3.1,'wrong')])}
    pred={'chunk_0':(folder/'chunk_0.wav',4.,unchanged)}
    audit=retry_collapsed_alignment(model,folder,pred,'zh',wrong_sequence)
    assert audit and not audit[0]['accepted'] and unchanged==broken
    assert all(a is b for a,b in zip(prior,(model.dataset,model.predictions,model.alignment_errors)))

t=np.arange(400)*.01
pitch=60+.12*np.sin(t*2*np.pi*5.5)
result=RmvpeResult(.01,pitch.astype('float32'),np.ones(len(t),bool))
tail_candidates=[{'lyric':'bo','asr_mora':'bo','notes':[{'onset':.5,'offset':.78,'pitch':60.}],
                  'offset':0.,'tail_end':.81,'collapsed_seconds':.01}]
tail_anchors=[{'start':.1,'end':.4,'lyric':'hi'}]
added,audit=accept_japanese_tails(tail_candidates,result,tail_anchors,(t,np.full(400,-20.)))
assert len(added)==1 and added[0]['lyric']=='bo' and audit[0]['status']=='applied'
rebuilt,audit_notes=refine_notes([NoteInfo(.1,.4,60,'hi')],result,tail_anchors+added)
assert [n.lyric for n in rebuilt]==['hi','bo']
no_voice=RmvpeResult(.01,np.full(400,np.nan),np.zeros(400,bool))
release_pitch=np.full(400,np.nan,dtype='float32');release_pitch[20:95]=60+.1*np.sin(t[20:95]*30)
release_result=RmvpeResult(.01,release_pitch,np.isfinite(release_pitch))
release_units=[{'start':.2,'end':.6,'lyric':'bo'}]
release_note=NoteInfo(.2,.6,60,'bo');release_energy=(t,np.where(np.isfinite(release_pitch),-22.,-100.))
extended,extensions=continue_releases([release_note],release_result,release_units,release_energy)
assert abs(extended[0].offset-.95)<1e-8 and release_note.offset==.6 and extensions
assert extended[0].lyric=='bo' and extended[0].onset==.2 and extended[0].pitch==60
assert not continue_releases([release_note],release_result,[],release_energy)[1]
assert not continue_releases([release_note],no_voice,release_units,release_energy)[1]
quiet=np.full(400,-22.);quiet[60:]=-90
assert not continue_releases([release_note],release_result,release_units,(t,quiet))[1]
later=release_pitch.copy();later[60:68]=np.nan
assert not continue_releases([release_note],RmvpeResult(.01,later,np.isfinite(later)),release_units,release_energy)[1]
next_notes=[release_note,NoteInfo(.8,1.1,62,'ku')]
joined,_=continue_releases(next_notes,release_result,release_units,release_energy)
assert joined[0].offset<=.8 and joined[1].onset==.8
changed=release_pitch.copy();changed[60:]+=4
assert not continue_releases([release_note],RmvpeResult(.01,changed,np.isfinite(changed)),release_units,release_energy)[1]
assert accept_japanese_tails(tail_candidates,no_voice,tail_anchors,(t,np.full(400,-20.)))[0]==[]
quiet_tail=np.full(400,-20.);quiet_tail[50:78]=-80
assert accept_japanese_tails(tail_candidates,result,tail_anchors,(t,quiet_tail))[0]==[]
assert accept_japanese_tails(tail_candidates,result,tail_anchors+added,(t,np.full(400,-20.)))[0]==[]
assert accept_japanese_tails(tail_candidates,None,tail_anchors,(t,np.full(400,-20.)))[0]==[]
# An otherwise clear final vowel must not disappear when GAME includes an
# unsupported trailing release in its candidate duration.
released_pitch=result.midi_pitch.copy();released_pitch[78:90]=np.nan
released=RmvpeResult(.01,released_pitch,np.isfinite(released_pitch))
long_release=[{**tail_candidates[0],'notes':[{'onset':.5,'offset':.9,'pitch':60.}]}]
trimmed,_=accept_japanese_tails(long_release,released,tail_anchors,(t,np.full(400,-20.)))
assert len(trimmed)==1 and trimmed[0]['end']==.78
units=[{'start':.2,'end':.53,'lyric':'中'},{'start':.53,'end':1.2,'lyric':'乌'}]
original=NoteInfo(.2,1.2,60,'中')
notes,report=refine_notes([original],result,units)
assert len(notes)==2 and [n.lyric for n in notes]==['中','乌']
assert notes[0].offset==.53 and notes[1].onset==.53
assert original.offset==1.2 and original.lyric=='中'
assert report['splits'] and not report['removed']
# Fast, explicitly aligned syllables retain separate lyrics at 35–60 ms. A
# future syllable must not steal the preceding short note's lyric.
for width in (.035,.05,.06):
    fast_units=[{'start':.2,'end':.2+width,'lyric':'中'},
                {'start':.2+width,'end':.2+width*2,'lyric':'乌'}]
    fast,audit=refine_notes([NoteInfo(.2,.2+width*2,60,'中乌')],result,fast_units)
    assert len(fast)==2 and [n.lyric for n in fast]==['中','乌'],(width,fast)
    assert abs(fast[0].offset-(.2+width))<1e-9
    assert all(n.offset>n.onset for n in fast)
# A note spanning two phrases must not turn their aligned, unvoiced pause into
# a sustained vowel. Unaligned gaps and continuously voiced material remain.
pause_units=[{'start':.2,'end':.5,'lyric':'中'},{'start':1.2,'end':1.5,'lyric':'乌'}]
pause_pitch=result.midi_pitch.copy();pause_pitch[50:120]=np.nan
pause_result=RmvpeResult(.01,pause_pitch,np.isfinite(pause_pitch))
separated,audit=refine_notes([NoteInfo(.2,1.5,60,'中乌')],pause_result,pause_units)
assert [(n.onset,n.offset,n.lyric) for n in separated]==[(.2,.5,'中'),(1.2,1.5,'乌')]
assert audit['phrase_rests']==[{'start':.5,'end':1.2}]
continuous,audit=refine_notes([NoteInfo(.2,1.5,60,'中乌')],result,pause_units)
assert continuous[0].offset==1.2 and not audit['phrase_rests']
# Fill a genuinely aligned missing syllable, and uncovered tails, from stable f0.
notes,report=refine_notes([],result,units)
assert len(notes)==2 and len(report['recovered'])==2
notes,report=refine_notes([NoteInfo(.2,.4,60,'中')],result,[{'start':.2,'end':1.2,'lyric':'中'}])
assert len(notes)==2 and notes[-1].lyric=='-' and notes[-1].offset==1.2
assert report['aligned_continuation_policy']=='separate' and not report['aligned_continuations']
# A stable missing tail must not acquire a new attack solely at GAME's old
# offset. Preserve the input, existing rearticulations and distinct words.
held=NoteInfo(.2,.4,60,'中')
continued,audit=refine_notes([held],result,[{'start':.2,'end':1.2,'lyric':'中'}],merge_aligned_continuations=True)
assert held.offset==.4 and continued[0] is not held
assert len(continued)==1 and continued[0].offset==1.2 and continued[0].lyric=='中'
assert len(audit['aligned_continuations'])==1 and not audit['recovered']
rearticulated=[NoteInfo(.2,.4,60,'中'),NoteInfo(.4,.6,60,'-')]
continued,audit=refine_notes(rearticulated,result,[{'start':.2,'end':1.2,'lyric':'中'}],merge_aligned_continuations=True)
assert len(continued)==2 and [n.onset for n in continued]==[.2,.4]
assert continued[-1].offset==1.2 and rearticulated[-1].offset==.6
separate,audit=refine_notes([NoteInfo(.2,.4,60,'中')],result,
    [{'start':.2,'end':.4,'lyric':'中'},{'start':.4,'end':1.2,'lyric':'乌'}],merge_aligned_continuations=True)
assert len(separate)==2 and [n.lyric for n in separate]==['中','乌']
# A real melodic change in the uncovered part still creates a continuation
# note at its own pitch, while the measured curve remains byte-for-byte intact.
tail_step=result.midi_pitch.copy();tail_step[40:]=64.
melodic=RmvpeResult(.01,tail_step,np.ones(len(tail_step),bool))
separate,audit=refine_notes([held],melodic,[{'start':.2,'end':1.2,'lyric':'中'}],merge_aligned_continuations=True)
assert len(separate)==2 and [n.pitch for n in separate]==[60,64] and not audit['aligned_continuations']
assert np.array_equal(melodic.midi_pitch,tail_step)
# No voiced signal means no invented note even when alignment supplied a token.
silent=RmvpeResult(.01,np.full(400,np.nan),np.zeros(400,bool))
assert refine_notes([],silent,units)[0]==[]
brief_unit=[{'start':.2,'end':.28,'lyric':'中'}]
brief_note,audit=refine_notes([],result,brief_unit)
assert len(brief_note)==1 and brief_note[0].lyric=='中' and len(audit['recovered'])==1
assert refine_notes([],silent,brief_unit)[0]==[]
# A varying voiced syllable can remain unresolved by the stable-center
# recovery rule. Surface its exact gap for review without fabricating a note.
varying=RmvpeResult(.01,np.where(np.arange(400)%20<10,60.,64.).astype('float32'),np.ones(400,bool))
unresolved,audit=refine_notes([],varying,[{'start':.2,'end':.5,'lyric':'中'}])
assert not unresolved and audit['aligned_coverage']['voiced_gap_count']==1
assert audit['aligned_coverage']['gaps'][0]['start']==.2 and not audit['aligned_coverage']['gaps'][0]['confirmed_missed_note']
assert not aligned_coverage_gaps([NoteInfo(.2,.5,60,'中')],[{'start':.2,'end':.5,'lyric':'中'}],varying)['gaps']
assert aligned_coverage_gaps([],brief_unit,silent)['voiced_gap_count']==0
assert aligned_coverage_gaps([],brief_unit,None)['gaps'][0]['status']=='pitch_unavailable'
assert aligned_coverage_gaps([],[],result)['gaps']==[]
# Touching notes collectively cover one syllable; short quantization margins
# are ignored. Detection must not mutate or consolidate the existing notes.
covered=[NoteInfo(.2,.35,60,'中'),NoteInfo(.35,.5,64,'-')]
assert aligned_coverage_gaps(covered,[{'start':.2,'end':.5,'lyric':'中'}],varying)['gaps']==[]
assert [(n.onset,n.offset) for n in covered]==[(.2,.35),(.35,.5)]
for intentional_reason in ('quiet_unmatched_filler','residual_shared_lead'):
    explained=[{'onset':.2,'offset':.5,'reason':intentional_reason}]
    assert not aligned_coverage_gaps([],[{'start':.2,'end':.5,'lyric':'中'}],varying,explained)['gaps']
# Ordinary vibrato and a slow slide do not turn into extra note boundaries.
vibrato=RmvpeResult(.01,(60+.65*np.sin(t*2*np.pi*5.5)).astype('float32'),np.ones(400,bool))
assert not _plateau_splits(NoteInfo(0,4,60,'啊'),vibrato)
slide=RmvpeResult(.01,(60+t).astype('float32'),np.ones(400,bool))
assert not _plateau_splits(NoteInfo(0,4,60,'啊'),slide)
# Brief adjacent semitone plateaus can be real melismas within one syllable.
# Preserve both note centers; an ordinary slide/vibrato remains a pitch curve.
brief=RmvpeResult(.01,np.where(np.arange(40)*.01<.12,60.,61.).astype('float32'),np.ones(40,bool))
assert not _plateau_splits(NoteInfo(0,.32,61,'mi'),brief)
cuts=_plateau_splits(NoteInfo(0,.32,61,'mi'),brief,propose=True)
assert len(cuts)==1 and abs(cuts[0]-.12)<=.01
confirmed=confirm_note_boundaries(cuts,[NoteInfo(0,.12,60,''),NoteInfo(.12,.32,61,'')],brief)
assert confirmed==cuts
assert not confirm_note_boundaries(cuts,[NoteInfo(0,.32,61,'')],brief)
assert not confirm_note_boundaries(cuts,[NoteInfo(0,.12,59,''),NoteInfo(.12,.32,61,'')],brief)
assert not confirm_note_boundaries(cuts,[NoteInfo(0,.12,60.49,''),NoteInfo(.12,.32,61,'')],brief)
assert not confirm_note_boundaries(cuts,[NoteInfo(0,.12,60,''),NoteInfo(.12,.32,60.51,'')],brief)
assert confirm_note_boundaries(cuts,[NoteInfo(0,.12,60.1,''),NoteInfo(.12,.32,60.9,'')],brief)==cuts
split,detail=refine_notes([NoteInfo(0,.32,61,'mi')],brief,confirmed_boundaries=confirmed)
assert len(split)==2 and round(split[0].pitch)==60 and round(split[1].pitch)==61
assert split[0].lyric=='mi' and split[1].lyric=='-'
wide=RmvpeResult(.01,(60+np.sin(t*2*np.pi*2)).astype('float32'),np.ones(400,bool))
assert not _plateau_splits(NoteInfo(0,4,60,'啊'),wide)
trill=RmvpeResult(.01,np.where((np.arange(120)//20)%2,62.,60.).astype('float32'),np.ones(120,bool))
assert len(_plateau_splits(NoteInfo(0,1.2,60,'a'),trill))>=3
# A stable late release bend after the correct GAME center remains a curve.
release=RmvpeResult(.01,np.where(np.arange(40)*.01<.17,60.,61.).astype('float32'),np.ones(40,bool))
assert not _plateau_splits(NoteInfo(0,.33,60,'ta'),release)
step=np.where(t<1.5,60,64).astype('float32')
steps=RmvpeResult(.01,step,np.ones(400,bool))
notes,report=refine_notes([NoteInfo(.2,3,60,'啊')],steps)
assert len(notes)==2 and abs(notes[0].offset-1.5)<.03 and notes[1].pitch==64
# A stable one-semitone note-center error is repaired; an unsupported octave
# disagreement is retained for review, rather than guessed away.
notes,_=refine_notes([NoteInfo(.2,1,59,'中')],result)
assert notes[0].pitch==60
# Compare the center the editor will receive. A fractional GAME value at
# 59.48 rounds down even though a stable reference is centered on MIDI 60.
fractional=RmvpeResult(.01,np.full(400,60.02,dtype='float32'),np.ones(400,bool))
notes,audit=refine_notes([NoteInfo(.2,1,59.48,'中')],fractional)
assert notes[0].pitch==60 and audit['pitch_changes'][0]['old']==59.48
# Brief notes use fewer but stricter stable frames, not a lower stability bar.
notes,audit=refine_notes([NoteInfo(.2,.28,58.2,'中')],fractional)
assert notes[0].pitch==60 and audit['pitch_changes'][0]['brief_evidence']
wild=RmvpeResult(.01,(60+np.sin(t*40)).astype('float32'),np.ones(400,bool))
notes,_=refine_notes([NoteInfo(.2,.28,58.2,'中')],wild)
assert notes[0].pitch==58.2
notes,_=refine_notes([NoteInfo(.2,1,48,'中')],result)
assert notes[0].pitch==48
notes,_=refine_notes([NoteInfo(.2,1,48,'中')],result,[{'start':.2,'end':1,'lyric':'中'}],verify_pitch=lambda *args:True)
assert notes[0].pitch==60
notes,_=refine_notes([NoteInfo(.2,1,48,'中')],result,[{'start':.2,'end':1,'lyric':'中'}],verify_pitch=lambda *args:False)
assert notes[0].pitch==48
# Splitting a merged lyric is not evidence of a new melodic plateau. It must
# not bypass the independent check for a sustained octave disagreement.
octave_ambiguous=RmvpeResult(.01,np.full(400,72,dtype='float32'),np.ones(400,bool))
notes,audit=refine_notes([NoteInfo(.2,1.2,60,'中乌')],octave_ambiguous,units)
assert len(notes)==2 and [n.pitch for n in notes]==[60,60] and not audit['pitch_changes']
# Reference matching must recover an internal omitted syllable, not merely
# substitute the surviving characters, and must not invent prefix/suffix text.
query='甲 乙 中 云 丙 丁';ref=list('甲乙中乌云丙丁');phones=['jia','yi','zhong','wu','yun','bing','ding']
text,phonetic,cursor,count=expand_reference_match(query,'jia yi zhong yun bing ding',ref,phones)
assert text=='甲 乙 中 乌 云 丙 丁' and count==1 and 'wu' in phonetic.split()
assert expand_reference_match(query,'old',list('乌甲乙中云丙丁'),phones)[3]==0
# A long matched phrase can recover an internal pair after its first anchor,
# while an unsung prefix, suffix or whole absent line is never inserted.
ref=list('因果与对错如尘埃的你我又要如何将法则的不公言说')
query='因 与 对 错 尘 埃 的 你 我 又 要 如 何 将 法 则 的 不 公 言 说'
expanded,_,_,count=expand_reference_match(query,'unused',ref,['p'+str(i) for i in range(len(ref))])
assert expanded.replace(' ','')==''.join(ref) and count==2
assert expand_reference_match('因 对 错 尘 埃 的 你 我 又 要','unused',ref,['p']*len(ref))[3]==3
many=list('甲一二三四五乙丙丁戊己庚辛壬癸')
assert expand_reference_match('甲 乙 丙 丁 戊 己 庚 辛 壬 癸','unused',many,['p']*len(many))[3]==0
# A stable vowel narrowly below 80% after the unvoiced-phone veto survives;
# noisy/poorly supported alignments still do not acquire a note.
core=np.full(400,60.,dtype='float32');core[24:28]=np.nan
supported=RmvpeResult(.01,core,np.isfinite(core))
restored,audit=refine_notes([],supported,[{'start':.2,'end':.5,'lyric':'对'}])
assert len(restored)==1 and restored[0].lyric=='对' and audit['recovered']
core[29:41]=np.nan
unsupported=RmvpeResult(.01,core,np.isfinite(core))
assert not refine_notes([],unsupported,[{'start':.2,'end':.5,'lyric':'对'}])[0]

# A long measured vowel falsely labelled SP is re-aligned as a phrase, using
# the same recognized word sequence. Real rests and worse candidates stay.
from transcription_quality import retry_voiced_pauses,chinese_gap_windows,bounded_candidates
pause_words=[aligned(0,.2,'a'),aligned(.2,.4,'b'),aligned(.4,1.2,'SP'),
    aligned(1.2,1.3,'c'),aligned(1.3,1.8,'d'),aligned(1.8,2.5,'SP')]
f0=np.full(251,60.,dtype='float32');f0[180:]=np.nan
pause_f0=RmvpeResult(.01,f0,np.isfinite(f0))
chunk={'offset':0.,'waveform':np.full(40000,.1,dtype='float32')}
model=SimpleNamespace(dataset=['source'],predictions=['source'])
def retry_pause(model,folder,**kwargs):
    assert (folder/'retry.lab').read_text(encoding='utf-8')=='a b c d'
    model.dataset=['retry'];model.predictions=['retry']
    return {'retry':(folder/'retry.wav',1.88,[aligned(0,.2,'a'),aligned(.2,.4,'b'),
        aligned(.4,1.3,'c'),aligned(1.3,1.8,'d')])}
outcome=SimpleNamespace(pred_dict={'chunk_0':(None,2.5,pause_words.copy())})
audit=retry_voiced_pauses(model,outcome,[chunk],16000,pause_f0,retry_pause)
assert audit and audit[0]['accepted'] and model.dataset==['source'] and model.predictions==['source']
assert [w.text for w in outcome.pred_dict['chunk_0'][2]]==['a','b','c','d','SP']
rest_f0=f0.copy();rest_f0[40:120]=np.nan
outcome=SimpleNamespace(pred_dict={'chunk_0':(None,2.5,pause_words.copy())})
def unexpected(*args,**kwargs):raise AssertionError('A real rest started a retry')
assert not retry_voiced_pauses(model,outcome,[chunk],16000,RmvpeResult(.01,rest_f0,np.isfinite(rest_f0)),unexpected)
def rejected(model,folder,**kwargs):return {'retry':(None,1.88,[aligned(0,1.8,'wrong')])}
audit=retry_voiced_pauses(model,outcome,[chunk],16000,pause_f0,rejected)
assert audit and not audit[0]['accepted'] and outcome.pred_dict['chunk_0'][2]==pause_words
# If a recognized internal syllable remains collapsed after re-alignment,
# local GAME plus independent f0 can recover its actual sung note. It needs
# one known token and intact word anchors, never a guessed extra lyric.
gap_words=[word(0,.3,'guo'),word(.3,1.1,'SP','SP'),word(1.1,1.11,'yu'),word(1.11,1.5,'dui')]
gap_outcome=SimpleNamespace(pred_dict={'chunk_0':(None,1.5,gap_words)},chars_dict={'chunk_0':['果','与','对']})
gap_chunks=[{'offset':0.,'waveform':np.ones(44100*2,dtype='float32')*.1}]
windows=chinese_gap_windows(gap_outcome,gap_chunks,result)
assert len(windows)==1 and windows[0]['lyric']=='与'
# The local crop is in the caller's sample rate, including 16 kHz input. A
# hard-coded 44.1 kHz divisor used to put right before its voiced gap.
short_chunks=[{'offset':0.,'waveform':np.ones(16000*2,dtype='float32')*.1}]
resampled=chinese_gap_windows(gap_outcome,short_chunks,result,16000)
assert len(resampled)==1 and abs(resampled[0]['right']-windows[0]['right'])<1e-9
assert resampled[0]['right']>resampled[0]['left']
candidate=NoteInfo(.22,1.15,60,'')
assert not bounded_candidates([candidate],.3,1.1)
assert bounded_candidates([candidate],.3,1.1,internal=True)[0]['onset']==.3
proposal={**windows[0],'offset':0.,'notes':[{'onset':.3,'offset':1.1,'pitch':60.}]}
added,audit=accept_japanese_tails([proposal],result,[{'start':0.,'end':.3,'lyric':'果'},
    {'start':1.11,'end':1.5,'lyric':'对'}],(t,np.full(400,-20.)))
assert len(added)==1 and added[0]['lyric']=='与' and added[0]['source']=='local_GAME_internal'
assert not chinese_gap_windows(gap_outcome,gap_chunks,silent)
gap_words[2]=word(1.1,1.3,'yu')
assert not chinese_gap_windows(gap_outcome,gap_chunks,result)
# Very weak fallback residuals disappear, but a lyric-anchored quiet note stays.
levels=np.full(400,-18.);levels[:50]=-50
notes,report=refine_notes([NoteInfo(0,.4,33,''),NoteInfo(1,1.5,60,'中')],result,
    [{'start':1,'end':1.5,'lyric':'中'}],(t,levels))
assert len(notes)==1 and len(report['removed'])==1
notes,_=refine_notes([NoteInfo(.1,.4,60,'中')],result,[{'start':.1,'end':.4,'lyric':'中'}],(t,levels))
assert len(notes)==1
adlibs=[{'start':.1,'end':.4,'lyric':'啊'},{'start':1,'end':1.5,'lyric':'中'}]
reference='甲乙丙丁戊己庚辛壬癸中'
notes,report=refine_notes([NoteInfo(.1,.4,60,'啊'),NoteInfo(1,1.5,60,'中')],result,adlibs,(t,levels),reference_text=reference)
assert len(notes)==1 and report['removed'][0]['reason']=='quiet_unmatched_filler'
notes,_=refine_notes([NoteInfo(.1,.4,60,'啊')],result,[adlibs[0]],(t,levels),reference_text=reference+'啊')
assert len(notes)==1
# Public hooks preserve the Japanese route and do not edit installed source.
# MIDI-only jobs must not run local GAME retries without independent pitch.
from unittest.mock import patch
import inference.pipeline.auto_lyric_hybrid as hybrid
stage_notes=[]
hybrid._run_game_stage=lambda *args,**kwargs:stage_notes[:]
pitch_calls=[]
def native_pitch(waveform,sr,**kwargs):
    pitch_calls.append(kwargs.copy());return kwargs
hybrid._extract_pitch_curve=native_pitch
hybrid._export_outputs=lambda notes,ctx,**kwargs:kwargs
install('ja',ROOT/'work/not-read.wav',ROOT/'work/not-written.json',pitch_available=False)
arguments=dict(rmvpe_model_path='not-loaded',output_format_set=frozenset({'mid'}),
               output_pitch_curve=False,device='cpu',cancel_checker=None)
hybrid._extract_pitch_curve(np.zeros(100),44100,**arguments)
assert not pitch_calls[-1]['output_pitch_curve']
with patch('transcription_quality.japanese_tail_windows',side_effect=AssertionError('Pitch-disabled job started tail inference')):
    assert hybrid._run_game_stage([],44100,SimpleNamespace(fa_language='ja'),SimpleNamespace(pred_dict={},chars_dict={}))==[]
# Internal verification is independent of exported curves. The real export
# hook receives measured f0 for checks, then withholds it from a pitch-off writer.
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='note-pitch-policy-') as directory:
    report=Path(directory)/'quality.json'
    install('ja',ROOT/'work/not-read.wav',report,pitch_available=True)
    hybrid._extract_pitch_curve(np.zeros(100),44100,**arguments)
    assert pitch_calls[-1]['output_pitch_curve'] and 'ustx' in pitch_calls[-1]['output_format_set']
    assert not arguments['output_pitch_curve'] and arguments['output_format_set']=={'mid'}
    measured=RmvpeResult(.01,np.full(100,60.,dtype='float32'),np.ones(100,bool))
    with patch('vocal_expression.energy_frames',return_value=(np.arange(100)*.01,np.full(100,-20.),None)):
        output=hybrid._export_outputs([NoteInfo(.1,.4,60,'a')],SimpleNamespace(),rmvpe_result=measured)
        assert output['rmvpe_result'] is None
        audit=json.loads(report.read_text(encoding='utf-8'))
        assert audit['note_pitch_analysis']['available'] and not audit['note_pitch_analysis']['curve_export_requested']
        hybrid._extract_pitch_curve(np.zeros(100),44100,**dict(arguments,output_pitch_curve=True,output_format_set={'ustx'}))
        output=hybrid._export_outputs([NoteInfo(.1,.4,60,'a')],SimpleNamespace(),rmvpe_result=measured)
        assert output['rmvpe_result'] is not None
        import transcription_quality as quality_module
        quality_module._state['syllables']=[{'start':.2,'end':.5,'lyric':'中'}]
        hybrid._export_outputs([],SimpleNamespace(),rmvpe_result=varying)
        assert json.loads(report.read_text(encoding='utf-8'))['aligned_coverage']['voiced_gap_count']==1
        quality_module._state['peer']=object()
        excluded={'onset':.2,'offset':.5,'pitch':60,'lyric':'中','reason':'residual_shared_lead'}
        with patch('vocal_partition.suppress_shared_notes',return_value=([],[excluded])):
            hybrid._export_outputs([NoteInfo(.2,.5,60,'中')],SimpleNamespace(),rmvpe_result=measured)
        receipt=json.loads(report.read_text(encoding='utf-8'))
        assert receipt['output_notes']==0 and receipt['aligned_coverage']['voiced_gap_count']==0
        assert receipt['aligned_coverage']['intentional_exclusions_ignored']==1
# The actual stage protocol reuses local inference, records failures and does
# not accept a contour-only split when that inference cannot confirm it.
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='local-note-boundary-') as directory:
    path=Path(directory)/'audio.wav';sf.write(path,np.zeros(44100),44100)
    stage_notes[:]=[NoteInfo(0,.32,61,'ka')]
    outcome=SimpleNamespace(pred_dict={'chunk_0':(None,1.,[word(0,.32,'ka','a')])},chars_dict={'chunk_0':['ka']})
    args=([{'offset':0.,'waveform':np.zeros(44100,dtype='float32')}],44100,SimpleNamespace(fa_language='ja',device='cpu'),outcome)
    kwargs=dict(game_model_dir='not-loaded',ts=[0.],seg_threshold=.2,seg_radius=.02,est_threshold=.2)
    install('ja',path,Path(directory)/'quality.json')
    with patch('pitch_accuracy.current_pitch',return_value=brief),patch.object(game_api,'load_game_model',return_value=object()), \
            patch.object(game_api,'extract_pitches_only',return_value=[NoteInfo(0,.12,60,''),NoteInfo(.12,.32,61,'')]) as local:
        assert hybrid._run_game_stage(*args,**kwargs)==stage_notes
        import transcription_quality as quality_module
        assert len(quality_module._state['confirmed_boundaries'])==1 and local.call_count==1
    install('ja',path,Path(directory)/'failed.json')
    with patch('pitch_accuracy.current_pitch',return_value=brief),patch.object(game_api,'load_game_model',return_value=object()), \
            patch.object(game_api,'extract_pitches_only',side_effect=RuntimeError('local trial failed')):
        assert hybrid._run_game_stage(*args,**kwargs)==stage_notes
        assert not quality_module._state['confirmed_boundaries']
        assert 'local trial failed' in quality_module._state['note_boundary_checks'][0]['error']
    stage_notes.clear()
old=Path(game_api.__file__).read_bytes()
install('zh',ROOT/'work/not-read.wav',ROOT/'work/not-written.json')
new=game_api.extract_vowel_boundaries(words,['中','乌'],'zh')
assert new==chinese_boundaries(words,['中','乌'],game_api)
assert Path(game_api.__file__).read_bytes()==old
print('PASS: token consumption, phrase pauses, same-pitch syllables, missing notes, plateau/vibrato/slide preservation and weak fallback filtering')
