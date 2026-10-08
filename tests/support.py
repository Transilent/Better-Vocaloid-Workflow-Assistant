"""Synthetic video/cover fixtures; never fetch media or use a saved account."""
import json
import subprocess
from pathlib import Path


def ensure_fixture(app):
    app = Path(app).resolve()
    from common import atomic_json, load_config
    from publication import load_bundle, prepare
    scratch = app / 'work/test-fixtures'
    scratch.mkdir(parents=True, exist_ok=True)
    pointer = scratch / 'bundle.txt'
    if pointer.is_file():
        bundle = Path(pointer.read_text(encoding='utf-8'))
        load_bundle(bundle, 'bilibili')
        return bundle
    ffmpeg = load_config()['ffmpeg']
    video = scratch / 'synthetic.mp4'
    cover = scratch / 'synthetic-cover.jpg'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=1280x720:rate=30', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
                    '-t', '3', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(video)], check=True, timeout=60, creationflags=flags)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'smptebars=size=1920x1080', '-frames:v', '1', '-q:v', '2', str(cover)],
                   check=True, timeout=30, creationflags=flags)
    job = scratch / 'source'
    job.mkdir(exist_ok=True)
    atomic_json(job / 'request.json', {'bv': 'BV1000000000'})
    atomic_json(job / 'source-info.json', {'title': '合成测试', 'desc': 'STAFF：合成测试作者'})
    draft = '本家：合成测试素材\nSTAFF：合成测试作者'
    (job / '发布简介草稿.txt').write_text(draft, encoding='utf-8')
    content = {name: {'title': '【测试翻唱】合成测试', 'description': draft} for name in ('bilibili', 'xiaohongshu')}
    bundle = prepare(job, video, content, cover_file=cover, parent=scratch / 'bundles')
    pointer.write_text(str(bundle), encoding='utf-8')
    return bundle
