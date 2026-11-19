"""Edit VSQX in its native XML and project shared VOCALOID score/controller data."""
from bisect import bisect_right
import os
from pathlib import Path
from xml.dom import Node, minidom
from xml.parsers.expat import ExpatError

from agent_pronunciation import MAX_GROUPS, MAX_NOTES, MAX_STRUCTURE_DEPTH, MAX_TRACKS, MAX_XML_ELEMENTS
from agent_pronunciation import VSQX_NAMESPACES, _decode_vsqx
from vocal_expression import SV_QUARTER
from vocaloid_pitch import pre_measure_ticks
import vpr_tuning as vocaloid


MAX_BYTES = 64 * 1024**2
STYLE_ONLY = 'VSQX 支持「保留原表现」下的 DYN 力度与 PIT 音高弯曲。'
_NAMES = {
    3: dict(part='musicalPart', name='trackName', tick='posTick', duration='durTick',
            pitch='noteNum', lyric='lyric', phoneme='phnms', note_style='noteStyle',
            bank='vBS', controller='mCtrl', value='attr', numerator='nume',
            denominator='denomi', measure='posMes', bpm='bpm'),
    4: dict(part='vsPart', name='name', tick='t', duration='dur', pitch='n', lyric='y',
            phoneme='p', note_style='nStyle', bank='bs', controller='cc', value='v',
            numerator='nu', denominator='de', measure='m', bpm='v'),
}
_CONTROL_IDS = {
    3: {'DYN': vocaloid.DYN, 'PIT': vocaloid.PIT, 'PBS': vocaloid.PBS},
    4: {'D': vocaloid.DYN, 'P': vocaloid.PIT, 'S': vocaloid.PBS},
}


def _elements(parent, name=None):
    return [node for node in parent.childNodes if node.nodeType == Node.ELEMENT_NODE
            and node.namespaceURI == parent.namespaceURI and (name is None or node.localName == name)]


def _child(parent, name):
    nodes = _elements(parent, name)
    if len(nodes) > 1:
        raise ValueError(f'VSQX 包含重复的 {name} 节点。')
    return nodes[0] if nodes else None


def _text(node):
    if node is None:
        return ''
    if any(part.nodeType == Node.ELEMENT_NODE for part in node.childNodes):
        raise ValueError(f'VSQX 的 {node.localName} 不是标量。')
    return ''.join(part.data for part in node.childNodes if part.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))


def _integer(parent, name, default=None):
    value = _text(_child(parent, name)).strip()
    if not value and default is not None:
        return default
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError(f'VSQX 的 {name} 必须是有效整数。') from error
    if not -(2**31) <= number < 2**31:
        raise ValueError(f'VSQX 的 {name} 超出原生刻度范围。')
    return number


def _document(text):
    try:
        document = minidom.parseString(text)
    except (ExpatError, RecursionError) as error:
        raise ValueError(f'无法解析 VSQX XML：{error}') from error
    version = _version(document)
    return document, version


def _version(document):
    root = document.documentElement
    for version, namespace in VSQX_NAMESPACES.items():
        if root.localName == f'vsq{version}' and root.namespaceURI == namespace:
            return version
    raise ValueError('请选择命名空间正确的 VSQX3 或 VSQX4 工程。')


def _payload(document):
    # Keep declarations, foreign namespaces, CDATA, comments and processing
    # instructions. The unchanged snapshot can still be copied byte-for-byte.
    return {'xml': document.toxml(encoding='utf-8', standalone=document.standalone).decode('utf-8')}


def read(raw):
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('VSQX 工程为空或超过 64 MiB。')
    text, _ = _decode_vsqx(raw)
    document, _ = _document(text)
    stack = [(document.documentElement, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_STRUCTURE_DEPTH or count > MAX_XML_ELEMENTS:
            raise ValueError('VSQX XML 超出结构安全上限。')
        stack.extend((child, depth + 1) for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE)
    return _payload(document)


def encode(output, raw):
    payload = output['xml'].encode('utf-8')
    if raw.startswith(b'\xef\xbb\xbf'):
        payload = b'\xef\xbb\xbf' + payload
    if len(payload) > MAX_BYTES:
        raise ValueError('候选 VSQX 超过 64 MiB。')
    return payload


def _tempo(root, names):
    master = _child(root, 'masterTrack')
    if master is None:
        raise ValueError('VSQX 缺少主轨。')
    resolution = _integer(master, 'resolution')
    count = _integer(master, 'preMeasure', 0)
    if not 1 <= resolution <= 1_000_000 or SV_QUARTER % resolution or not 0 <= count <= 10_000:
        raise ValueError('VSQX 拍分辨率或预备小节无法安全换算。')
    signatures = [(_integer(row, names['measure']), _integer(row, names['numerator']),
                   _integer(row, names['denominator'])) for row in _elements(master, 'timeSig')]
    origin = pre_measure_ticks(signatures, resolution, count)
    step = SV_QUARTER // resolution
    tempos = {}
    for row in _elements(master, 'tempo'):
        tick, value = _integer(row, names['tick']), _integer(row, names['bpm'])
        if tick in tempos and tempos[tick] != value:
            raise ValueError('VSQX 同一位置包含相互冲突的速度。')
        tempos[tick] = value
    if any(tick < 0 or not 0 < value <= 100000 for tick, value in tempos.items()):
        raise ValueError('VSQX 速度事件位置或 BPM 无效。')
    tempos.setdefault(0, 12000)
    events = [{'position': (tick - origin) * step, 'bpm': value / 100.}
              for tick, value in sorted(tempos.items())]
    meters = [{'index': bar - count, 'numerator': numerator, 'denominator': denominator}
              for bar, numerator, denominator in signatures]
    return events, meters, origin, step, count


def _control_rows(part, names, ids, length):
    """Keep original nodes addressable, with one held stream per controller."""
    grouped, known_nodes = {}, {}
    malformed = False
    for control in _elements(part, names['controller']):
        try:
            position = _integer(control, names['tick'])
            field = _child(control, names['value'])
            identity = field.getAttribute('id') if field else ''
            if not identity or not 0 <= position <= length:
                raise ValueError('VSQX controller')
            value = _integer(control, names['value'])
        except ValueError:
            malformed = True
            continue
        stream = grouped.setdefault(identity, [])
        stream.append({'pos': position, 'value': value})
        if identity in ids:
            known_nodes[identity, position] = field
    currents = []
    for identity, name in ids.items():
        events = grouped.get(identity, [])
        if malformed or events:
            currents.append({'name': name, 'events': None if malformed else sorted(events, key=lambda row: row['pos'])})
    for identity, rows in grouped.items():
        if identity not in ids:
            # An unknown time-varying lane is a reason to protect note timing.
            malformed |= rows[0]['pos'] != 0 or any(row['value'] != rows[0]['value'] for row in rows)
    return currents, known_nodes, malformed


def _note(note, names, singers, singer_ticks):
    position = _integer(note, names['tick'])
    singer = bisect_right(singer_ticks, position) - 1
    phoneme = _child(note, names['phoneme'])
    lock = phoneme.getAttribute('lock') if phoneme else ''
    style = _child(note, names['note_style'])
    vibrato = 0
    if style is not None:
        for field in _elements(style, names['value']):
            if field.getAttribute('id') == 'vibLen' and _text(field).strip() not in ('', '0'):
                vibrato = 1
    return {'pos': position, 'duration': _integer(note, names['duration']), 'number': _integer(note, names['pitch']),
            'lyric': _text(_child(note, names['lyric'])), 'phoneme': _text(phoneme),
            'isProtected': lock not in ('', '0'), 'langID': singers[singer][1] if singer >= 0 else None,
            'vibrato': {'duration': vibrato}}


class Layout(vocaloid.Layout):
    format = 'vsqx'
    versions = (3, 4)
    tempo = staticmethod(lambda data: data['tempos'])
    meters = staticmethod(lambda data: data['meters'])
    bounded_curves = True
    style_only = STYLE_ONLY
    notice = STYLE_ONLY

    def __init__(self, data):
        document, version = _document(data['xml'])
        names = _NAMES[version]
        ids = _CONTROL_IDS[version]
        root = document.documentElement
        tempos, meters, origin, self.step, self.measure_prefix = _tempo(root, names)
        self.native_origin = origin
        self.native_tracks = tuple(_elements(root, 'vsTrack'))
        self.native_parts = {}
        self.native_media = tuple((part, _text(_child(part, 'filePath')))
                                  for part in root.getElementsByTagNameNS(root.namespaceURI, 'wavPart'))
        tracks = []
        total_notes = 0
        for track_index, track in enumerate(self.native_tracks):
            parts = []
            for part_index, part in enumerate(_elements(track, names['part'])):
                position, length = _integer(part, names['tick']), _integer(part, 'playTime')
                singers = sorted((_integer(row, names['tick']), _integer(row, names['bank']))
                                 for row in _elements(part, 'singer'))
                singer_ticks = [tick for tick, _ in singers]
                nodes = _elements(part, 'note')
                self.native_parts[track_index, part_index] = part, nodes
                notes = [_note(note, names, singers, singer_ticks) for note in nodes]
                total_notes += len(notes)
                if len(parts) >= MAX_GROUPS or total_notes > MAX_NOTES:
                    raise ValueError('VSQX 片段或音符超过安全上限。')
                controllers, _, unknown = _control_rows(part, names, ids, length)
                singer_changes = bool(singer_ticks and singer_ticks != [0])
                parts.append({'pos': position - origin, 'duration': length, 'notes': notes,
                              'controllers': controllers,
                              'timingProtected': unknown or singer_changes or bool(_elements(part, 'sCtrl'))})
            if len(tracks) >= MAX_TRACKS:
                raise ValueError('VSQX 轨道超过安全上限。')
            tracks.append({'type': 0, 'name': _text(_child(track, names['name'])), 'parts': parts})
        self.sequence = {'version': {'major': version, 'minor': 0, 'revision': 0},
                         'tempos': tempos, 'meters': meters, 'tracks': tracks}
        super().__init__(self.sequence)
        self.version = str(version)


def _element(parent, name):
    qualified = f'{parent.prefix}:{name}' if parent.prefix else name
    return parent.ownerDocument.createElementNS(parent.namespaceURI, qualified)


def _write_text(node, value):
    value = str(value)
    text = [part for part in node.childNodes if part.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE)]
    if len(text) == 1 and (text[0].nodeType == Node.TEXT_NODE or ']]>' not in value):
        text[0].data = value
    else:
        for part in text:
            node.removeChild(part)
        node.appendChild(node.ownerDocument.createTextNode(value))


def _set_child(parent, name, value, *, before=None):
    field = _child(parent, name)
    if field is None:
        field = _element(parent, name)
        parent.insertBefore(field, before)
    _write_text(field, value)
    return field


def _merge_controls(part, names, ids, old, updated):
    before = {row['name']: row for row in old['controllers']}
    _, nodes, _ = _control_rows(part, names, ids, old['duration'])
    pending = []
    for identity, name in ids.items():
        stream = next((row for row in updated['controllers'] if row['name'] == name), None)
        if stream is None or stream == before.get(name):
            continue
        source = {row['pos']: row['value'] for row in before.get(name, {}).get('events', [])}
        for event in stream['events']:
            position, value = event['pos'], event['value']
            if position not in source:
                pending.append((position, identity, value))
            elif value != source[position]:
                _write_text(nodes[identity, position], value)
    if not pending:
        return
    pending.sort(reverse=True)
    for anchor in [*_elements(part), None]:
        if anchor is None or anchor.localName in ('sCtrl', 'note', 'plane'):
            tick = float('inf')
        elif anchor.localName == names['controller']:
            tick = _integer(anchor, names['tick'])
        else:
            continue
        while pending and pending[-1][0] < tick:
            position, identity, value = pending.pop()
            control = _element(part, names['controller'])
            _set_child(control, names['tick'], position)
            _set_child(control, names['value'], value).setAttribute('id', identity)
            part.insertBefore(control, anchor)
        if not pending:
            break


def merge_sequence(data, original, updated):
    """Patch just the changed native scalar fields and controller knots."""
    if original == updated:
        return dict(data)
    document, version = _document(data['xml'])
    names = _NAMES[version]
    fields = {'pos': 'tick', 'duration': 'duration', 'number': 'pitch', 'lyric': 'lyric', 'phoneme': 'phoneme'}
    for track, before, after in zip(_elements(document.documentElement, 'vsTrack'), original['tracks'], updated['tracks']):
        if before == after:
            continue
        for part, old, new in zip(_elements(track, names['part']), before['parts'], after['parts']):
            if old == new:
                continue
            for node, a, b in zip(_elements(part, 'note'), old['notes'], new['notes']):
                for field, name in fields.items():
                    if a[field] != b[field]:
                        anchor = _child(node, names['note_style'])
                        if field == 'lyric':
                            anchor = _child(node, names['phoneme']) or anchor
                        _set_child(node, names[name], b[field], before=anchor)
            if old['controllers'] != new['controllers']:
                _merge_controls(part, names, _CONTROL_IDS[version], old, new)
    return _payload(document)


def verify_sequence(data, expected):
    actual = Layout(data).sequence
    for sequence in (expected, actual):
        for track in sequence['tracks']:
            for part in track['parts']:
                part['controllers'].sort(key=lambda row: row['name'])
    if actual != expected:
        raise ValueError('VSQX 音符或控制事件回读不一致。')


def selected_indices(data, options, *, layout=None):
    layout = layout or Layout(data)
    return vocaloid.selected_indices(layout.sequence, options, layout=layout)


def process_project(data, options, *, cancel=lambda: None, progress=lambda text: None):
    from tuning_engine import digest

    layout = Layout(data)
    sequence, report = vocaloid.process_project(layout.sequence, options, layout=layout, cancel=cancel, progress=progress)
    output = merge_sequence(data, layout.sequence, sequence)
    # Read back the projection before allowing the session transaction to write.
    verify_sequence(output, sequence)
    report.update(source_content_sha256=digest(data), output_content_sha256=digest(output))
    return output, report


def relocate_media(data, original):
    """Bind relative WAV-part paths to the original companion directory."""
    document, version = _document(data['xml'])
    root = document.documentElement
    base = original.with_suffix('.wavparts')
    changes = []
    for index, part in enumerate(root.getElementsByTagNameNS(VSQX_NAMESPACES[version], 'wavPart')):
        field = _child(part, 'filePath')
        name = _text(field)
        if not name or not name.strip():
            continue
        reference = Path(name)
        if reference.is_absolute():
            continue
        if reference.drive or '\0' in name or reference.root:
            raise ValueError('VSQX 包含无法准确定位的伴奏引用。')
        resolved = os.path.abspath(base / reference)
        _write_text(field, resolved)
        changes.append({'wave_part_index': index, 'original_reference': name,
                        'output_reference': resolved, 'audio_modified': False})
    if changes:
        data.update(_payload(document))
    return changes
