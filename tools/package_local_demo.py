"""기존 친구 자료·독립 Python·실행 코드를 Windows 시연 폴더에 묶는다. 키·세션 기록은 복사하지 않는다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "Server"), str(ROOT / "Web"), str(ROOT / "tools")]
from local_demo_config import DATA, VENV, VOX_SOURCE
from prepared_friend import load_bundle
from service_bundle import files_for

PYTHON_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
VC_RUNTIME_URL = "https://aka.ms/vc14/vc_redist.x64.exe"


def copy_tree(source, target):
    def copy_changed(src, dst):
        a, b = Path(src), Path(dst)
        if b.is_file() and a.stat().st_size == b.stat().st_size and a.stat().st_mtime_ns == b.stat().st_mtime_ns:
            return str(b)
        return shutil.copy2(src, dst)
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".git", ".cache", "*.pyc"), copy_function=copy_changed)


def package(destination):
    target = destination.resolve()
    if target == ROOT or ROOT in target.parents:
        raise ValueError("배포 대상은 소스 저장소 바깥의 별도 폴더여야 합니다.")
    target.mkdir(parents=True, exist_ok=True)
    backend, runtime = target / "Backend", target / "runtime"
    if backend.exists() and not (backend / "bundle.json").exists():
        raise ValueError("기존 Backend 폴더를 덮어쓰지 않습니다.")
    prepared = load_bundle(DATA / "prepared-friend")
    preview = json.loads((ROOT / "UserSettings/PersonaLocalBodyPreview.json").read_text(encoding="utf-8"))
    body_path = (ROOT / preview["modelRelativePath"]).resolve()
    if not preview.get("enabled") or not body_path.is_relative_to((ROOT / "tools/_work").resolve()):
        raise ValueError("현재 몸체의 시연 자료를 확인하지 못했습니다.")
    body = body_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != preview["modelSha256"]:
        raise ValueError("시연 몸체가 변경됐습니다.")
    if hashlib.sha256(prepared["files"]["model.glb"]).hexdigest() != preview["sourceSha256"]:
        raise ValueError("친구의 등록 모델과 현재 몸체 연결이 일치하지 않습니다.")
    backend.mkdir(exist_ok=True)
    (backend / "bundle.json").write_text(json.dumps({"version": 1, "mode": "prepared_friend",
        "python": "3.11.9", "platform": "windows-x64", "api_key": "OPENAI_API_KEY"}, indent=2), encoding="utf-8")
    selected = [p for p, _ in files_for("dialogue") if p.suffix in (".py", ".yaml")]
    selected += list((ROOT / "Server/registration").glob("*.py"))
    selected += [ROOT / "Server" / n for n in ("local_demo_config.py", "local_demo_apps.py", "local_demo_web.py", "local_demo_providers.py")]
    selected += [ROOT / "tools" / n for n in ("local_demo.py", "local_demo_launcher.py")]
    selected += [ROOT / "Web" / n for n in ("app.py", "survey_v2.py", "prepared_friend.py")]
    selected += list((ROOT / "Survey/core").glob("*.py"))
    for p in selected:
        out = backend / p.relative_to(ROOT)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
    copy_tree(ROOT / "Web/static", backend / "Web/static")
    copy_tree(DATA / "prepared-friend", backend / "prepared-friend")
    print("실행 코드·친구 자료 복사 완료", flush=True)
    copy_tree(DATA / "models/VoxCPM2", backend / "models/VoxCPM2")
    shutil.copy2(DATA / "models/silero_vad.onnx", backend / "models/silero_vad.onnx")
    copy_tree(VOX_SOURCE / "src", backend / "voxcpm-source/src")
    for name in ("LICENSE", "README.md", "pyproject.toml"):
        if (VOX_SOURCE / name).is_file():
            shutil.copy2(VOX_SOURCE / name, backend / "voxcpm-source" / name)
    print("음성 모델 복사 완료", flush=True)
    cache = ROOT / "tools/_work/local_demo/package"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "python-3.11.9-embed-amd64.zip"
    if not archive.is_file():
        urllib.request.urlretrieve(PYTHON_URL, archive)
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if Path(item.filename).name != item.filename:
                raise ValueError("Python 배포 파일의 경로가 올바르지 않습니다.")
        z.extractall(runtime)
    (runtime / "python311._pth").write_text("python311.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
    # venv 실행기·pyvenv.cfg를 복사하지 않는다. 독립 CPython에 검증된 패키지만 포함한다.
    copy_tree(VENV / "Lib/site-packages", runtime / "Lib/site-packages")
    prerequisites = target / "Prerequisites"
    prerequisites.mkdir(exist_ok=True)
    vc_runtime = prerequisites / "vc_redist.x64.exe"
    if not vc_runtime.is_file():
        urllib.request.urlretrieve(VC_RUNTIME_URL, vc_runtime)
    streaming = target / "Game/DasiBom_Data/StreamingAssets"
    (streaming / "models").mkdir(parents=True, exist_ok=True)
    (streaming / "models/friend-body.glb").write_bytes(body)
    preview["modelRelativePath"] = "models/friend-body.glb"
    (streaming / "LocalDemoBody.json").write_text(json.dumps(preview, indent=2), encoding="utf-8")
    (streaming / "LocalDemoRequired.txt").write_text("Launch using Start.cmd\n", encoding="utf-8")
    (target / "Start.cmd").write_text('@echo off\r\nchcp 65001 >nul\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0Backend\\tools\\local_demo_launcher.py"\r\nif errorlevel 1 pause\r\n', encoding="ascii")
    (target / "Stop.cmd").write_text('@echo off\r\nchcp 65001 >nul\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0Backend\\tools\\local_demo.py" stop\r\npause\r\n', encoding="ascii")
    (target / "사용 안내.txt").write_text(
        "다시, 봄 — 로컬 API 친구 시연\n\n"
        "1. Windows 사용자 환경변수 OPENAI_API_KEY에 본인의 OpenAI API 키를 설정합니다.\n"
        "2. NVIDIA 드라이버, VR 기기의 PC 연결 프로그램과 OpenXR 런타임을 준비합니다.\n"
        "   Visual C++ 런타임이 없는 PC는 Prerequisites/vc_redist.x64.exe를 먼저 설치합니다.\n"
        "3. Start.cmd를 실행합니다. 처음 음성 모델 로딩은 시간이 걸릴 수 있습니다.\n"
        "4. 열린 웹에서 테스트 → 친구를 누릅니다. 사진·음성·모델이 등록되면 Unity가 자동 실행됩니다.\n"
        "5. Unity에서 체험을 시작합니다. 종료하면 로컬 서비스도 종료됩니다.\n\n"
        "Windows 10/11 x64, NVIDIA GPU와 드라이버, 인터넷이 필요합니다. Unity 편집기·Python·Blender를 따로 설치할 필요는 없습니다.\n"
        "준비된 친구 전용이며 Tripo 생성 요청은 하지 않습니다. OpenAI API 비용은 설정한 키 계정에 발생합니다.\n"
        "다른 컴퓨터에는 이 폴더 전체를 복사하세요. Game의 exe만 복사하면 동작하지 않습니다.\n"
        "키는 배포 파일에 포함하지 않습니다. 친구의 개인 사진·음성·모델이 포함된 비공개 시연 자료입니다.\n"
        "실행 자료와 서비스 로그: %LOCALAPPDATA%\\DasiBom\\LocalApiDemo\\\n"
        "비정상 종료 후 서비스가 남으면 Stop.cmd를 실행하세요. 학교 서버와 개발 중인 로컬 서비스는 종료하지 않습니다.\n"
        "다른 PC와 실제 HMD 착용 검증은 별도입니다.\n",
        encoding="utf-8-sig")
    manifest = {"version": 1, "python_source": PYTHON_URL,
                "python_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "vc_runtime_source": VC_RUNTIME_URL,
                "vc_runtime_sha256": hashlib.sha256(vc_runtime.read_bytes()).hexdigest(),
                "key_included": False, "prepared_only": True}
    (target / "Package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("독립 Python·몸체·시작 프로그램 패키지 완료", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    package(args.destination)
