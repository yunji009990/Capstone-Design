"""Download the official sherpa-onnx SenseVoice CPU model and Korean sample.

Only explicitly named files are copied from the archive. No archive-controlled
paths are extracted, and existing model directories are never deleted.
"""
import argparse
import hashlib
import shutil
import tarfile
import urllib.request
from pathlib import Path

ARCHIVE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE}.tar.bz2"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path.home() / "dialogue-models" / "sensevoice")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "source.tar.bz2"
    if not archive.is_file():
        partial = archive.with_suffix(".part")
        print("Downloading SenseVoice CPU model...", flush=True)
        with urllib.request.urlopen(URL, timeout=60) as source, partial.open("wb") as target:
            shutil.copyfileobj(source, target)
        partial.replace(archive)
    with tarfile.open(archive, "r:bz2") as source:
        for name in ("model.int8.onnx", "tokens.txt", "LICENSE", "test_wavs/ko.wav"):
            member = source.getmember(f"{ARCHIVE}/{name}")
            if not member.isfile():
                raise ValueError(f"Expected a regular file: {name}")
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            with source.extractfile(member) as data, partial.open("wb") as output:
                shutil.copyfileobj(data, output)
            partial.replace(target)
    digest = hashlib.sha256()
    with (root / "model.int8.onnx").open("rb") as model:
        for chunk in iter(lambda: model.read(1024 * 1024), b""):
            digest.update(chunk)
    print(f"Ready: {root}")
    print(f"model.int8.onnx SHA256: {digest.hexdigest()}")


if __name__ == "__main__":
    main()
