"""새 TTS 전용 venv에서 실행: CUDA wheel을 FlashInfer의 toolkit 경로에 맞춘다.

사용: ~/venv/qwentts-stream/bin/python setup_tts_streaming.py
시스템 CUDA/기존 LLM 환경은 수정하지 않는다. requirements를 먼저 설치한다.
"""
import importlib.metadata
from pathlib import Path
import sys


def main():
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Run with the dedicated qwentts-stream virtual environment")
    package = importlib.metadata.distribution("nvidia-cuda-nvcc")
    cuda = Path(package.locate_file("nvidia/cu13")).resolve()
    if not cuda.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("CUDA compiler is outside this virtual environment")
    if package.version != "13.0.88":
        raise RuntimeError("Install the pinned requirements-tts-streaming.txt first")
    for name in ("nvidia-cuda-crt", "nvidia-nvvm"):
        if importlib.metadata.version(name) != "13.0.88":
            raise RuntimeError("CUDA compiler components must all match 13.0.88")
    links = [(cuda / "lib64", Path("lib")), (cuda / "lib/libcudart.so", Path("libcudart.so.13"))]
    for link, target in links:
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target, target_is_directory=link.name == "lib64")
    print("CUDA 13 compiler and FlashInfer library paths prepared:", cuda)


if __name__ == "__main__":
    main()
