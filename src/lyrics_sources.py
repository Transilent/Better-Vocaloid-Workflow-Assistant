"""Public NetEase song/lyric lookup and offline lyric-reference import."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.request

from common import clean_lyrics, direct_urlopen
from subtitles import Cue



def song_id(value):
    text = str(value).strip()
    if re.fullmatch(r'[1-9][0-9]{0,17}', text):
        return text
    parsed = urlparse(text)
    if parsed.hostname not in ('music.163.com', 'y.music.163.com') or parsed.scheme not in ('http','https'):
        raise ValueError('请输入网易云歌曲 ID 或歌曲链接。')
    query = parse_qs(parsed.query or urlparse(parsed.fragment).query)
    route = urlparse(parsed.fragment).path if parsed.fragment.startswith('/') else parsed.path
    if not re.fullmatch(r'/(m/)?song/?',route):
        raise ValueError('请选择歌曲链接，不是歌单或歌手链接。')
    result = (query.get('id') or [''])[0]
    if not re.fullmatch(r'[1-9][0-9]{0,17}', result):
        raise ValueError('链接中没有有效的歌曲 ID。短链接请先在浏览器中展开。')
    return result


def request_api(path, payload, opener=direct_urlopen):
    endpoint = 'api/cloudsearch/pc' if path.startswith('cloudsearch/') else 'api/song/lyric'
    request = urllib.request.Request('https://music.163.com/'+endpoint+'?'+urlencode(payload),
        headers={'User-Agent':'Mozilla/5.0','Referer':'https://music.163.com/'})
    with opener(request,timeout=15) as response:
        data = response.read(4*1024**2+1)
    if len(data) > 4*1024**2:
        raise ValueError('歌词服务响应超过大小限制。')
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OSError('网易云歌词服务返回了异常响应。可重试或导入本地歌词文件。') from exc
    if result.get('code') != 200:
        raise OSError('网易云请求未完成：'+str(result.get('message') or result.get('code'))+'。可重试或导入本地歌词文件。')
    return result


def search(keyword, offset=0, reader=request_api):
    keyword = keyword.strip()
    if not keyword or len(keyword)>120 or offset < 0:
        raise ValueError('请输入歌名或歌手，最多 120 个字符。')
    response = reader('cloudsearch/get/web', {'s':keyword,'type':'1','limit':'20','offset':str(offset),'csrf_token':''})
    songs = (response.get('result') or {}).get('songs') or []
    return [{'id':str(s['id']), 'title':str(s.get('name','')),
             'artists':' / '.join(str(a.get('name','')) for a in (s.get('ar') or s.get('artists') or [])),
             'album':str((s.get('al') or s.get('album') or {}).get('name','')),
             'duration':float(s.get('dt') or s.get('duration') or 0)/1000} for s in songs]


@dataclass
class LyricReference:
    text: str
    cues: list
    origin: dict
    raw: str = ''


def is_credit(text):
    return bool(re.match(r'^\s*(?:作词|作詞|作曲|编曲|編曲|词曲|词|曲|混音|母带|录音|制作人|制作|监制|发行|出品|演唱|歌手|翻译|lyrics?|composer|arranger|vocal)\s*[:：]',text,re.I))


def parse_yrc(text):
    cues=[]
    for line in text.splitlines():
        match=re.match(r'^\[(\d+),(\d+)\](.*)$',line)
        if not match:
            continue
        start,duration=int(match[1]),int(match[2])
        content=re.sub(r'\(\d+,\d+,\d+\)','',match[3]).strip()
        if duration>0 and content:
            cues.append(Cue(start/1000,(start+duration)/1000,content))
    if not cues:
        raise ValueError('没有找到有效的 YRC 歌词行。')
    return cues


def parse_lrc(text):
    offset_match=re.search(r'\[offset:([+-]?\d+)\]',text,re.I)
    offset=int(offset_match[1])/1000 if offset_match else 0
    rows=[]
    for line in text.splitlines():
        times=list(re.finditer(r'\[(\d+):(\d+(?:\.\d+)?)\]',line))
        if not times:
            continue
        content=line[times[-1].end():].strip()
        if content:
            rows.extend((max(0,int(m[1])*60+float(m[2])+offset),content) for m in times)
    rows.sort(key=lambda row:row[0])
    return [Cue(start,max(start+.05,rows[index+1][0] if index+1<len(rows) else start+5),line)
            for index,(start,line) in enumerate(rows)]


def fetch(value, reader=request_api):
    identifier=song_id(value)
    response=reader('song/lyric?csrf_token=', {'id':identifier,'os':'pc','lv':'-1','kv':'-1','tv':'-1','rv':'-1','yv':'-1','ytv':'-1','yrv':'-1','csrf_token':''})
    yrc=(response.get('yrc') or {}).get('lyric','')
    lrc=(response.get('lrc') or {}).get('lyric','')
    if not yrc and not lrc:
        raise ValueError('这首歌没有可用的原文歌词，可选择其他版本或导入本地文件。')
    try:
        parsed = parse_yrc(yrc) if yrc else parse_lrc(lrc)
    except ValueError:
        if not lrc: raise
        parsed, yrc = parse_lrc(lrc), ''
    cues=[cue for cue in parsed if not is_credit(cue.text)]
    text=clean_lyrics(''.join(cue.text for cue in cues))
    if not text:
        raise ValueError('没有找到可用于校准的歌词。')
    return LyricReference(text,cues,{'provider':'netease','song_id':identifier,
        'url':'https://music.163.com/song?id='+identifier,'format':'yrc' if yrc else 'lrc'},yrc or lrc)


def from_file(path):
    path=Path(path)
    if path.stat().st_size > 4*1024**2:
        raise ValueError('歌词文件超过 4 MB，请选择纯歌词文本。')
    raw=path.read_bytes()
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):
        text=raw.decode('utf-16')
    else:
        try: text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try: text=raw.decode('gb18030')
            except UnicodeDecodeError as exc: raise ValueError('歌词文件编码无法识别，请保存为 UTF-8 文本后重试。') from exc
    if '\x00' in text: raise ValueError('请选择文本歌词文件，当前文件包含二进制内容。')
    if path.suffix.lower()=='.yrc':
        cues=parse_yrc(text)
    elif path.suffix.lower()=='.lrc':
        cues=parse_lrc(text)
    elif path.suffix.lower()=='.txt':
        cues=[]
    else:
        raise ValueError('参考歌词支持 LRC、YRC 或 TXT 文件。')
    cues=[cue for cue in cues if not is_credit(cue.text)]
    plain=clean_lyrics(''.join(cue.text for cue in cues) if cues else '\n'.join(line for line in text.splitlines() if not is_credit(line)))
    if not plain:
        raise ValueError('歌词文件没有有效文本。')
    return LyricReference(plain,cues,{'provider':'file','name':path.name,'format':path.suffix[1:].lower()},text)
