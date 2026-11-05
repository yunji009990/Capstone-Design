"""배포 폴더용 시작 프로그램. 웹 친구 등록 후 Unity를 실행하고 자신이 시작한 서비스를 정리한다."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "Server"), str(ROOT / "tools")]
import local_demo as demo
from local_demo_config import CONNECTION, DATA, PACKAGED, address, initialize


def read_openai_key():
    """환경변수만 읽는다. 키를 설정 파일·명령 인수·로그에 남기지 않는다."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key or os.name != "nt":
        return key
    import winreg
    for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, path) as handle:
                value, _ = winreg.QueryValueEx(handle, "OPENAI_API_KEY")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except OSError:
            pass
    return ""


def current_session(config):
    value = demo.fetch(config, "registration", "/session/current")
    return value.get("session") if value.get("has_model") else None


def run(player_args=(), open_browser=True):
    if not PACKAGED:
        raise RuntimeError("배포 폴더의 시작 프로그램으로 실행해 주세요.")
    player = ROOT.parent / "Game" / "DasiBom.exe"
    if not player.is_file():
        raise RuntimeError("Unity 실행 파일이 없습니다. 배포 폴더 전체를 복사해 주세요.")
    key = read_openai_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다. Windows 사용자 환경변수에 설정한 뒤 다시 실행하세요.")
    os.environ["OPENAI_API_KEY"] = key
    # 설치된 다른 Python이나 모델 캐시 대신 이 배포본의 실행 환경·자료만 사용한다.
    os.environ.pop("PYTHONHOME", None)
    os.environ.pop("PYTHONPATH", None)
    from filelock import FileLock, Timeout
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(DATA / "demo-launch.lock"), timeout=0)
        lock.acquire()
    except Timeout:
        raise RuntimeError("다시봄 시작 프로그램이 이미 실행 중입니다.") from None
    config = initialize()
    original = {s for s, r in demo.load_state().items() if demo.owned(r, s)}
    child = None
    try:
        print("다시, 봄 — 로컬 API 친구 체험", flush=True)
        print("음성 모델과 대화 연결을 준비합니다. 처음 실행은 조금 걸립니다.", flush=True)
        with FileLock(str(DATA / "launcher.lock"), timeout=3):
            demo.start(config)
        previous = current_session(config)
        url = address(config, "web") + "/?v=bundled-friend"
        if open_browser:
            webbrowser.open(url)
        print("웹에서 [테스트] → [친구]를 누르면 VR 프로그램이 자동으로 열립니다.", flush=True)
        print("등록 페이지: " + url, flush=True)
        print("취소하려면 이 창에서 Ctrl+C를 누르세요.", flush=True)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            sid = current_session(config)
            if sid and sid != previous:
                break
            time.sleep(.5)
        else:
            raise RuntimeError("등록 대기 시간이 끝났습니다. 다시 시작해 주세요.")
        print("친구가 준비됐습니다. VR 프로그램을 실행합니다.", flush=True)
        env = dict(os.environ)
        env.pop("OPENAI_API_KEY", None)  # Unity에는 로컬 연결 설정만 전달한다.
        child = subprocess.Popen([str(player), "--local-demo-config", str(CONNECTION), *player_args],
                                 cwd=player.parent, env=env)
        while child.poll() is None:
            time.sleep(.5)
        if child.returncode:
            raise RuntimeError("VR 프로그램이 오류로 종료됐습니다. 사용 안내의 로그 위치를 확인하세요.")
    finally:
        if child is not None and child.poll() is None:
            import psutil
            # 이 시작 프로그램이 직접 띄운 실행 파일에만 종료를 요청한다.
            process = psutil.Process(child.pid)
            if Path(process.exe()).resolve() == player.resolve():
                process.terminate()
        with FileLock(str(DATA / "launcher.lock"), timeout=3):
            state = demo.load_state()
            for service in reversed(demo.SERVICES):
                if service not in original:
                    demo.stop_service(config, state, service)
        lock.release()
        print("체험 서비스를 종료했습니다.", flush=True)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("체험 시작을 취소했습니다.")
    except (OSError, ValueError, RuntimeError) as exc:
        print("실행 안내: " + str(exc), file=sys.stderr)
        sys.exit(1)
