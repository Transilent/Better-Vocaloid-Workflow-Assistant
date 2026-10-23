"""Synthetic positional evidence, not lyric/emotion accuracy evaluation."""
import copy
from pathlib import Path
import sys
import unicodedata

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP/'src'))
from context_evidence import build_evidence
from context_structure import analyze_structure


def fixture(text, language='zh', words=(), *, continuation=False):
    chars = [c for c in text if unicodedata.category(c)[0] in 'LNM']
    notes = []
    clock = .75
    for index, char in enumerate(chars):
        duration = 1.1 if index == len(chars)-1 else .3
        notes.append({'lyric': char, 'pitch': 60+index%4, 'start': clock, 'end': clock+duration})
        clock += duration
        if continuation and index == 1:
            notes.append({'lyric': '-', 'pitch': 61, 'start': clock, 'end': clock+.35})
            clock += .35
    return build_evidence(text, language, notes, word_spans=[
        {'span': span, 'source': 'manual_fixture'} for span in words])


original = fixture('我还在这里等你', words=([0,1],[1,2],[2,5],[5,7]), continuation=True)
protected = copy.deepcopy(original)
value = analyze_structure(original)
assert original == protected and value == analyze_structure(original)
assert value['notes'][0]['rest_before'] == .75
assert value['notes'][-1]['rest_after'] is None
assert 'rest_before' in value['notes'][0]['features']
assert 'long_note' in value['notes'][-1]['features']
word = next(w for w in value['words'] if w['text'] == '还')
assert word['position'] == 'middle', 'Partial word annotations must not relabel middle words as phrase starts'
members = [n for n in value['notes'] if word['word_id'] in n['word_ids']]
assert len(members) == 2 and members[1]['continuation']
assert members[0]['word_positions'][0]['position'] == 'start'
assert 'word_start' in members[0]['features']
assert members[1]['word_positions'][0]['position'] == 'end'
assert 'word_end' in members[1]['features']
assert len(value['stanzas']) == 1
assert len(value['phrases']) == 1

repeated = analyze_structure(fixture('你，好？\n\n你，好？', words=([0,1],[2,3],[6,7],[8,9])))
assert len(repeated['stanzas']) == 2 and len(repeated['phrases']) == 4
assert all(p['repeat_count'] == 2 for p in repeated['phrases'])
assert len([c for c in repeated['cues'] if c['kind']=='question']) == 2
for row in repeated['phrases'] + repeated['stanzas'] + repeated['cues']:
    assert row['text'] == '你，好？\n\n你，好？'[row['span'][0]:row['span'][1]]

ja = analyze_structure(fixture('行かない、でも朝は来る。もし明日会えたら？', 'ja',
    ([0,4],[5,7],[7,8],[8,9],[9,11],[12,14],[14,16],[16,20])))
kinds = {row['kind'] for row in ja['cues']}
assert {'negation','contrast','conditional','question'} <= kinds
assert all(c['scope']=='unknown' and c['status']=='surface_cue' for c in ja['cues'])
false_markers = analyze_structure(fixture('ないしょの歌。もしもし。君主。', 'ja'))
assert not any(c['kind'] in ('negation','conditional','addressee') for c in false_markers['cues'])
zh_proper = analyze_structure(fixture('不丹的风景'))
assert not any(c['kind']=='negation' for c in zh_proper['cues'])

unsegmented = analyze_structure(fixture('夜空には星', 'ja'))
assert unsegmented['words'] == []
assert any(x['code']=='word_segmentation_unavailable_or_partial' for x in unsegmented['unknowns'])
phonetic = build_evidence('你好', 'zh', [
    {'lyric':'ni','pitch':60,'start':.75,'end':1.},
    {'lyric':'hao','pitch':61,'start':1.,'end':2.}])
unknown = analyze_structure(phonetic)
assert all(n['phrase_position']=='unknown' and not n['word_ids'] for n in unknown['notes'])
assert all(not p['note_ids'] for p in unknown['phrases'])
assert any(x['code']=='text_note_alignment_unavailable' for x in unknown['unknowns'])

empty = analyze_structure(build_evidence('', 'ja', []))
assert empty['phrases']==[] and empty['stanzas']==[] and empty['notes']==[]
forged = copy.deepcopy(original)
forged['text']['original'] = '改变了'
try:
    analyze_structure(forged)
except ValueError:
    pass
else:
    raise AssertionError('Stale content fingerprint accepted')
print('PASS: zh/ja positions, explicit words, continuations, leading rest, repeats, conservative cues, unknowns and no mutation')

