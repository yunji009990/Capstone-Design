"""Download the models used by the dialogue server.

Only explicitly named files are copied from the archive. No archive-controlled
paths are extracted, and existing model directories are never deleted.

새 PC 운영 설치(현재 STT 는 Whisper, 입력 게이트는 Silero VAD):

python setup_dialogue_models.py --whisper --vad

개별 명령:

python setup_dialogue_models.py --whisper  # Whisper large-v3 (운영 STT, GPU)
python setup_dialogue_models.py --vad      # Silero VAD input gate (필수)
python setup_dialogue_models.py            # SenseVoice ASR (되돌리기용 CPU 모델)
python setup_dialogue_models.py --all      # SenseVoice + Silero VAD (Whisper 는 포함하지 않는다)
"""
import argparse
import hashlib
import shutil
import tarfile
import urllib.request
from pathlib import Path

ARCHIVE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE}.tar.bz2"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
# 실제 내려받아 확인한 공식 파일. 어긋나는 파일을 정상으로 취급하지 않는다.
VAD_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
VAD_BYTES = 643854
# 운영 STT. 실제 비교 실험에서 쓴 스냅샷을 그대로 고정한다.
WHISPER_REPO = "Systran/faster-whisper-large-v3"
WHISPER_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
# CTranslate2 실행에 필요한 파일만 받는다. README·예제는 받지 않는다.
WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json",
                 "vocabulary.json", "preprocessor_config.json")
# 실행에 반드시 있어야 하는 파일. 나머지는 리비전에 따라 없을 수 있다.
WHISPER_REQUIRED = ("model.bin", "config.json")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def download(url, target):
    """Write through a .part file so an interrupted run never leaves a half model."""
    partial = target.with_suffix(target.suffix + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as source, partial.open("wb") as output:
        shutil.copyfileobj(source, output)
    partial.replace(target)


def install_sensevoice(root):
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "source.tar.bz2"
    if not archive.is_file():
        print("Downloading SenseVoice CPU model...", flush=True)
        download(URL, archive)
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
    print(f"Ready: {root}")
    print(f"model.int8.onnx SHA256: {digest(root / 'model.int8.onnx')}")


def install_whisper(root, revision=WHISPER_REVISION):
    """운영 STT 모델을 고정 리비전으로 받는다.

    무결성 기준은 Hugging Face 의 고정 리비전이다. 여기서 별도 해시를 고정하지
    않으므로, 받은 model.bin 의 SHA256 을 찍어 두고 다른 PC 설치와 대조한다.
    """
    from huggingface_hub import snapshot_download
    print(f"Downloading Whisper large-v3 ({revision[:12]})...", flush=True)
    snapshot_download(repo_id=WHISPER_REPO, revision=revision,
                      allow_patterns=list(WHISPER_FILES), local_dir=str(root))
    missing = [name for name in WHISPER_REQUIRED if not (root / name).is_file()]
    if missing:
        raise ValueError(f"Whisper download is incomplete: missing {', '.join(missing)}")
    print(f"Ready: {root}")
    print(f"repo: {WHISPER_REPO} revision: {revision}")
    print(f"model.bin SHA256: {digest(root / 'model.bin')}")


def verify(path, expected, size):
    actual = digest(path)
    actual_size = path.stat().st_size
    if expected and (actual != expected or (size and actual_size != size)):
        raise ValueError(
            f"{path.name} does not match the pinned release: expected "
            f"{expected} / {size} bytes, got {actual} / {actual_size} bytes")
    return actual


def install_silero(target, expected=VAD_SHA256, size=VAD_BYTES):
    """입력 후보가 실제 말인지 확인하는 CPU VAD. 대화 서버의 필수 모델이다.

    이미 있는 파일도 고정 해시로 확인한다. 어긋나면 교체하지 않고 오류를 낸다.
    내려받은 파일은 검증을 통과한 뒤에만 목표 경로로 옮긴다.
    """
    if target.is_file():
        print(f"Ready: {target}")
        print(f"silero_vad.onnx SHA256: {verify(target, expected, size)}")
        return
    print("Downloading Silero VAD model...", flush=True)
    staged = target.with_suffix(target.suffix + ".incoming")
    target.parent.mkdir(parents=True, exist_ok=True)
    download(VAD_URL, staged)
    try:
        actual = verify(staged, expected, size)
    except ValueError:
        staged.unlink(missing_ok=True)
        raise
    staged.replace(target)
    print(f"Ready: {target}")
    print(f"silero_vad.onnx SHA256: {actual}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path,
                        default=Path.home() / "dialogue-models" / "sensevoice",
                        help="SenseVoice 설치 폴더(되돌리기용)")
    parser.add_argument("--vad", action="store_true", help="install the Silero VAD input gate")
    parser.add_argument("--all", action="store_true",
                        help="install SenseVoice and the Silero VAD (Whisper 는 포함하지 않는다)")
    parser.add_argument("--whisper", action="store_true",
                        help="install the production Whisper large-v3 STT")
    parser.add_argument("--whisper-output", type=Path,
                        default=Path.home() / "dialogue-models" / "whisper-large-v3",
                        help="Whisper 설치 폴더. DIALOGUE_MODEL_DIR 과 같아야 한다")
    parser.add_argument("--whisper-revision", default=WHISPER_REVISION,
                        help="pinned Hugging Face revision of the official conversion")
    parser.add_argument("--vad-output", type=Path,
                        default=Path.home() / "dialogue-models" / "silero" / "silero_vad.onnx")
    parser.add_argument("--vad-sha256", default=VAD_SHA256,
                        help="pinned digest of the official release; empty disables the check")
    args = parser.parse_args()
    if args.all and args.whisper:
        # --all 의 뜻을 바꾸지 않는다. 두 STT 를 한 번에 받고 싶으면 따로 실행한다.
        parser.error("--all 은 SenseVoice + Silero VAD 조합이다. Whisper 는 --whisper 로 따로 설치한다.")
    if args.whisper:
        install_whisper(args.whisper_output.resolve(), args.whisper_revision)
    if args.all or not (args.vad or args.whisper):
        install_sensevoice(args.output.resolve())
    if args.all or args.vad:
        install_silero(args.vad_output.resolve(), args.vad_sha256,
                       VAD_BYTES if args.vad_sha256 == VAD_SHA256 else 0)


if __name__ == "__main__":
    main()
