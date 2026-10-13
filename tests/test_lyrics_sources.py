"""Reference import preserves original text/timing and strips metadata, not lyrics."""
from pathlib import Path
import sys
import tempfile

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from lyrics_sources import song_id,parse_lrc,parse_yrc,search,fetch,from_file

assert song_id('12345')=='12345'
assert song_id('https://music.163.com/#/song?id=12345')=='12345'
for invalid in ('https://example.com/song?id=12345','https://music.163.com/playlist?id=12345','0','../123'):
    try: song_id(invalid); raise AssertionError(invalid)
    except ValueError: pass
cues=parse_lrc('[ar:Artist]\n[offset:500]\n[00:01.00][00:03.00]日本語\n[00:05.00]下一行')
assert [c.start for c in cues]==[1.5,3.5,5.5]
assert cues[0].text=='日本語'
assert parse_yrc('[1000,500](1000,200,0)あ(1200,300,0)い')[0].text=='あい'
def reader(path,data):
    if path.startswith('cloudsearch'):
        assert data['s']=='歌名 歌手'
        return {'result':{'songs':[{'id':12345,'name':'示例','ar':[{'name':'作者'}],'dt':90000}]}}
    return {'code':200,'lrc':{'lyric':'[00:00.00]作词：作者\n[00:01.00]原文歌词'},'tlyric':{'lyric':'Translation must not replace the original'}}
assert search('歌名 歌手',reader=reader)[0]['artists']=='作者'
ref=fetch('12345',reader=reader)
assert ref.text=='原文歌词' and len(ref.cues)==1
with tempfile.TemporaryDirectory(dir=APP/'work',prefix='lyrics-') as temp:
    file=Path(temp)/'reference.yrc'
    file.write_text('[1000,500](1000,200,0)あ(1200,300,0)い',encoding='utf-8')
    assert from_file(file).text=='あい'
    legacy=Path(temp)/'reference.lrc'
    for encoding in ('utf-16','gb18030'):
        legacy.write_text('[00:01.00]原文歌词',encoding=encoding)
        assert from_file(legacy).text=='原文歌词'
    repaired=fetch('12345',reader=lambda *args:{'code':200,'yrc':{'lyric':'invalid YRC'},'lrc':{'lyric':'[00:01.00]有效歌词'}})
    assert repaired.text=='有效歌词' and repaired.origin['format']=='lrc'
print('PASS: IDs/URLs, timing offsets, repeated LRC rows, YRC, original text, credit filtering and file import')
