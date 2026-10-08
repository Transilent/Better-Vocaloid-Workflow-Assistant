"""Reusable WAV/media -> stems command; no desktop automation is involved."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, atomic_json, load_config
from pipeline import check_audio, run_process


def run(source, destination, dual=True):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_file():
        raise ValueError("输入音频文件不存在。")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("输出文件夹已有内容，请选择新的文件夹以保留旧结果。")
    destination.mkdir(parents=True, exist_ok=True)
    config = load_config()
    # Always decode into a distinct WAV; input media is never edited.
    run_process([config["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(source), "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le",
                 str(destination / "source.wav")], destination, "decode")
    original = check_audio(destination / "source.wav")
    from mdx import separate
    report = separate(destination / "source.wav", destination, config, destination)
    if dual:
        chorus_config = dict(config)
        chorus_config["mdx_model"] = str(ROOT / config["chorus_model"])
        chorus_config["mdx_metadata"] = str(ROOT / config["chorus_metadata"])
        report["chorus"] = separate(destination / "vocals.wav", destination,
                                      chorus_config, destination, output_names=("lead.wav", "backing.wav"))
    for name in (("vocals", "instrumental", "lead", "backing") if dual else ("vocals", "instrumental")):
        if check_audio(destination / (name + ".wav")).frames != original.frames:
            raise RuntimeError("输出音频时长与输入不一致。")
    report["input_file"] = str(source)
    atomic_json(destination / "separation-report.json", report)
    print("分离完成：", destination, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="无需打开 UVR/SpectraLayers，将音频分为主唱、和声、伴奏")
    parser.add_argument("--input", required=True, help="WAV / MP3 / MP4 等媒体路径")
    parser.add_argument("--output", required=True, help="新的空输出文件夹")
    parser.add_argument("--single", action="store_true", help="仅分离合并人声和伴奏")
    args = parser.parse_args()
    try:
        run(args.input, args.output, not args.single)
    except (ValueError, RuntimeError, OSError) as exc:
        print("分离失败：", exc, file=sys.stderr, flush=True)
        sys.exit(1)
