"""서버와 분리된 Windows 로컬 시연: init/setup-models/import-server/start/stop/status.

키는 OPENAI_API_KEY 환경 변수에서만 읽는다. 운영 서버는 시작하거나 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import contextlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server"))
from local_demo_config import (CONFIG, CONNECTION, DATA, MODELS, PACKAGED, RUNTIME_PYTHON,
                               VENV, WEB_VENV, VOX_SOURCE, VOX_COMMIT,
                               VOX_REVISION, address, connection, initialize, read_config, save_json)

SCRIPT = Path(__file__).resolve()
STATE = DATA / "processes.json"
WEB_SERVICES = ("registration", "web") if PACKAGED else ("registration", "worker", "web")
SERVICES = (*WEB_SERVICES, "tts", "dialogue")
PYTHON = RUNTIME_PYTHON if PACKAGED else VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PERSON_FILES = {"persona.md": 131072, "knowledge.md": 131072, "rules.md": 131072,
                "voice.wav": 30 * 1024 * 1024, "model.glb": 256 * 1024 * 1024}


def service_python(service):
    if PACKAGED:
        return RUNTIME_PYTHON
    return (WEB_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if service in ("web", "worker") else PYTHON)


def fetch(config, service, path, *, method="GET", timeout=5):
    req = urllib.request.Request(address(config, service) + path, method=method,
                                 headers={"X-Token": config["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def owned(record, service):
    """PID 재사용·다른 프로젝트·다른 역할의 프로세스는 종료 대상으로 삼지 않는다."""
    import psutil
    try:
        process = psutil.Process(record["pid"])
        args = process.cmdline()
        return (abs(process.create_time() - record["created"]) < .01
                and Path(process.exe()).resolve() == service_python(service).resolve()
                and len(args) >= 4 and args[-2:] == ["_serve", service]
                and str(SCRIPT) in args)
    except (KeyError, OSError, psutil.Error):
        return False


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def terminate_owned_tree(record, service):
    """HTTP 준비 전 실패한 실행기와 그 Windows venv 자식만 종료한다."""
    import psutil
    if not owned(record, service):
        return
    root = psutil.Process(record["pid"])
    family = root.children(recursive=True) + [root]
    # 명령·개별 프로세스의 실행 nonce까지 확인한 뒤에만 전체 종료를 시작한다.
    for process in family:
        try:
            args = process.cmdline()
            if (str(SCRIPT) not in args or args[-2:] != ["_serve", service]
                    or process.environ().get("LOCAL_DEMO_INSTANCE") != record["instance"]):
                raise RuntimeError("종료 대상의 소유를 확인하지 못했습니다: " + service)
        except psutil.NoSuchProcess:
            continue
    for process in reversed(family):
        with contextlib.suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(family, timeout=3)
    for process in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()
    _, alive = psutil.wait_procs(alive, timeout=3)
    if alive:
        raise RuntimeError("로컬 프로세스 종료를 확인하지 못했습니다: " + service)


def stop_service(config, state, service):
    record = state.get(service)
    if not record:
        return
    if owned(record, service):
        try:
            info = fetch(config, service, "/local-demo")
        except (OSError, ValueError):
            terminate_owned_tree(record, service)
            state.pop(service, None)
            save_json(STATE, state)
            return
        if info.get("instance") != record["instance"] or info.get("service") != service:
            raise RuntimeError("프로세스 식별이 달라 종료하지 않았습니다: " + service)
        fetch(config, service, "/local-demo/stop", method="POST")
        deadline = time.monotonic() + 40
        while owned(record, service) and time.monotonic() < deadline:
            time.sleep(.2)
        if owned(record, service):
            raise RuntimeError("정상 종료를 기다리고 있습니다. 잠시 뒤 stop을 다시 실행하세요: " + service)
    state.pop(service, None)
    save_json(STATE, state)


def start(config, services=SERVICES):
    import psutil
    if "dialogue" in services and not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ValueError("OPENAI_API_KEY 환경 변수가 필요합니다.")
    required = []
    if "dialogue" in services:
        required.append(MODELS / "silero_vad.onnx")
    if "tts" in services:
        required.extend((MODELS / "VoxCPM2/config.json", VOX_SOURCE / "src/voxcpm/core.py"))
    for path in required:
        if not path.is_file():
            raise ValueError("음성 모델이 없습니다. setup을 먼저 실행하세요.")
    if "web" in services or "worker" in services:
        from local_demo_web import preflight
        preflight()
    if any(not service_python(s).is_file() for s in services):
        raise ValueError("로컬 실행 환경이 없습니다. setup과 setup-web을 먼저 실행하세요.")
    state, started = load_state(), []
    # 전체 포트를 먼저 검사해 충돌 때문에 서비스를 일부만 켜지 않게 한다.
    for service in services:
        if port_open(config[service + "_port"]) and not owned(state.get(service, {}), service):
            raise RuntimeError("다른 프로세스가 로컬 포트를 사용 중입니다: " + service)
    try:
        for service in services:
            if not owned(state.get(service, {}), service):
                log = DATA / "logs" / (service + ".log")
                log.parent.mkdir(parents=True, exist_ok=True)
                env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1",
                           LOCAL_DEMO_INSTANCE=uuid.uuid4().hex)
                if service != "dialogue":
                    env.pop("OPENAI_API_KEY", None)
                env.pop("TRIPO_API_KEY", None)  # 웹·작업자는 로컬 전용 web.env에서 읽는다.
                flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                         if os.name == "nt" else 0)
                with log.open("ab") as output:
                    child = subprocess.Popen([str(service_python(service)), "-X", "utf8", "-u", str(SCRIPT), "_serve", service],
                        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                        creationflags=flags, start_new_session=os.name != "nt")
                state[service] = {"pid": child.pid, "created": psutil.Process(child.pid).create_time(),
                                  "instance": env["LOCAL_DEMO_INSTANCE"]}
                save_json(STATE, state)
                started.append(service)
            print("준비 중: " + service, flush=True)
            deadline = time.monotonic() + (240 if service == "tts" else 60)
            ready = False
            while time.monotonic() < deadline:
                if not owned(state[service], service):
                    raise RuntimeError("서비스 시작에 실패했습니다. 로컬 로그를 확인하세요: " + service)
                try:
                    info = fetch(config, service, "/local-demo", timeout=2)
                    health = fetch(config, service, "/health", timeout=10)
                    ready = info.get("instance") == state[service]["instance"] and health.get("status") == "ready"
                    if ready:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(.5)
            if not ready:
                raise RuntimeError("서비스 준비 시간이 초과됐습니다: " + service)
            print("준비 완료: " + service, flush=True)
    except BaseException:
        # 이번 호출에서 시작한 프로세스만 정리한다. 이미 실행 중이던 서비스는 보존한다.
        for service in reversed(started):
            try:
                stop_service(config, state, service)
            except (OSError, RuntimeError, ValueError):
                print("종료 재확인 필요: " + service, flush=True)
        raise


def status(config):
    state, rows = load_state(), {}
    for service in SERVICES:
        row = {"running": owned(state.get(service, {}), service), "ready": False,
               "port": config[service + "_port"]}
        if row["running"]:
            try:
                health = fetch(config, service, "/health", timeout=10)
                row.update(ready=health.get("status") == "ready")
                for field in ("llm", "stt", "model", "voice_mode", "tts_ready", "streaming"):
                    if field in health:
                        row[field] = health[field]
            except (OSError, ValueError):
                pass
        rows[service] = row
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def serve(config, service):
    import uvicorn
    from fastapi import Header, HTTPException
    from local_demo_apps import dialogue_app, registration_app, tts_app
    from local_demo_web import web_app, worker_app
    app = {"registration": registration_app, "tts": tts_app, "dialogue": dialogue_app,
           "web": web_app, "worker": worker_app}[service](config)
    instance = os.environ.get("LOCAL_DEMO_INSTANCE", "")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config[service + "_port"],
        access_log=False, log_level="warning", timeout_graceful_shutdown=30))

    def auth(value):
        if not hmac.compare_digest(value, config["token"]):
            raise HTTPException(403, "인증 실패")

    @app.get("/local-demo")
    async def identity(x_token: str = Header("")):
        auth(x_token)
        return {"service": service, "instance": instance}

    @app.post("/local-demo/stop")
    async def shutdown(x_token: str = Header("")):
        auth(x_token)
        if hasattr(app.state, "stop_worker"):
            app.state.stop_worker()
        server.should_exit = True
        return {"stopping": True}

    # Web의 '/' mount보다 관리 API가 우선한다.
    management = [route for route in app.router.routes if getattr(route, "path", "").startswith("/local-demo")]
    app.router.routes[:] = management + [route for route in app.router.routes if route not in management]
    server.run()


def setup_models():
    from huggingface_hub import snapshot_download
    from setup_dialogue_models import install_silero
    if not VOX_SOURCE.exists():
        VOX_SOURCE.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(VOX_SOURCE)], check=True)
        subprocess.run(["git", "-C", str(VOX_SOURCE), "fetch", "--depth", "1",
                        "https://github.com/OpenBMB/VoxCPM.git", VOX_COMMIT], check=True)
        subprocess.run(["git", "-C", str(VOX_SOURCE), "checkout", "--detach", "-q", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(VOX_SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if actual != VOX_COMMIT:
        raise ValueError("VoxCPM 소스 버전이 달라 자동 덮어쓰기를 하지 않았습니다.")
    install_silero(DATA / "models/silero_vad.onnx")
    snapshot_download("openbmb/VoxCPM2", revision=VOX_REVISION, local_dir=str(DATA / "models/VoxCPM2"),
                      allow_patterns=["*.json", "*.safetensors", "*.bin", "*.pth", "*.model"], max_workers=3)
    print("VoxCPM2·Silero 준비 완료")


def import_archive(raw):
    """지정된 현재 인물만 안전하게 복사한다. tar 경로를 그대로 추출하지 않는다."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(set(names)) != len(names) or "current.json" not in names or len(names) > 6:
            raise ValueError("잘못된 등록 자료 묶음입니다.")
        current = archive.getmember("current.json")
        if not current.isfile() or current.size > 1024:
            raise ValueError("잘못된 현재 인물 정보입니다.")
        sid = json.loads(archive.extractfile(current).read()).get("session", "")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sid):
            raise ValueError("잘못된 세션 식별자입니다.")
        target = DATA / "sessions" / sid
        staged = DATA / "sessions" / (".incoming-" + uuid.uuid4().hex)
        contents = {}
        for member in members:
            if member.name == "current.json":
                continue
            filename = member.name.removeprefix(sid + "/")
            if (filename not in PERSON_FILES or member.name != sid + "/" + filename
                    or not member.isfile() or not 0 <= member.size <= PERSON_FILES[filename]):
                raise ValueError("등록 자료 경로나 크기가 잘못됐습니다.")
            contents[filename] = archive.extractfile(member).read()
        if not {"persona.md", "voice.wav", "model.glb"}.issubset(contents):
            raise ValueError("현재 인물의 설정·목소리·완성 모델이 모두 필요합니다.")
        # 같은 세션 재가져오기는 변경이 없을 때만 허용한다. 이전 자료를 조용히 덮어쓰지 않는다.
        if target.exists():
            if target.is_symlink() or any(not (target / n).is_file() or (target / n).is_symlink()
                                         or (target / n).read_bytes() != b for n, b in contents.items()):
                raise ValueError("같은 세션의 로컬 자료가 달라 덮어쓰지 않았습니다.")
        else:
            staged.mkdir(parents=True, mode=0o700)
            try:
                for name, data in contents.items():
                    (staged / name).write_bytes(data)
                    os.chmod(staged / name, 0o600)
                staged.rename(target)
            finally:
                if staged.exists():
                    for path in staged.iterdir():
                        path.unlink()
                    staged.rmdir()
        save_json(DATA / "sessions/current.json", {"session": sid})
    print("현재 인물·참조 음성·완성 모델 복사 완료. 원본 서버 자료는 유지했습니다.")


def import_server():
    # 원격에 명령·키·운영 프로필을 저장하지 않는다. 서비스가 꺼져 있어도 SSH로 읽는다.
    remote = '''import io,json,pathlib,re,sys,tarfile
root=pathlib.Path.home()/"server/sessions"
sid=json.loads((root/"current.json").read_text()).get("session","")
if not isinstance(sid,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",sid): raise ValueError("invalid current session")
parent=root/sid
if parent.is_symlink() or parent.resolve().parent!=root.resolve(): raise ValueError("invalid session path")
limits={"persona.md":131072,"knowledge.md":131072,"rules.md":131072,"voice.wav":31457280,"model.glb":268435456}
with tarfile.open(fileobj=sys.stdout.buffer,mode="w|") as out:
 data=json.dumps({"session":sid}).encode();info=tarfile.TarInfo("current.json");info.size=len(data);out.addfile(info,io.BytesIO(data))
 for name,limit in limits.items():
  p=parent/name
  if p.is_symlink(): raise ValueError("symlink refused")
  if p.is_file():
   if p.stat().st_size>limit: raise ValueError("asset too large")
   out.add(p,arcname=sid+"/"+name,recursive=False)
'''
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "raon", "python3 -"],
                            input=remote.encode(), capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("서버 자료 읽기에 실패했습니다. SSH 접속과 현재 인물 등록을 확인하세요.")
    import_archive(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "setup-models", "import-server", "import-web", "prepare-friend", "start", "start-web", "stop",
                                            "status", "open-web", "use-local", "use-server", "_serve"])
    parser.add_argument("service", nargs="?", choices=SERVICES)
    args = parser.parse_args()
    config = initialize() if args.command == "init" else read_config()
    if args.command == "_serve":
        if not args.service:
            parser.error("service is required")
        serve(config, args.service)
        return
    # OS 잠금은 프로세스가 죽어도 풀린다. 설정·PID 파일의 동시 변경을 막는다.
    from filelock import FileLock
    DATA.mkdir(parents=True, exist_ok=True)
    with FileLock(str(DATA / "launcher.lock"), timeout=1):
        if args.command == "init":
            print("로컬 설정 준비 완료. API 키는 환경 변수만 사용합니다.")
        elif args.command == "setup-models":
            setup_models()
        elif args.command == "prepare-friend":
            from local_demo_web_setup import prepare_friend
            prepare_friend()
        elif args.command in ("import-server", "import-web"):
            if any(owned(r, s) for s, r in load_state().items()):
                raise RuntimeError("자료를 가져오기 전에 로컬 서비스를 종료하세요.")
            if args.command == "import-server":
                import_server()
            else:
                from local_demo_web_setup import import_web
                import_web()
        elif args.command == "start":
            start(config)
        elif args.command == "start-web":
            start(config, WEB_SERVICES)
            print("로컬 등록 웹: " + address(config, "web"))
        elif args.command == "open-web":
            if not owned(load_state().get("web", {}), "web"):
                raise ValueError("로컬 웹을 먼저 시작하세요: start-web 또는 start")
            import webbrowser
            webbrowser.open(address(config, "web"))
        elif args.command == "stop":
            state = load_state()
            for service in reversed(SERVICES):
                stop_service(config, state, service)
            print("로컬 서비스 종료 완료. 기존 GPU 서버는 제어하지 않았습니다.")
        elif args.command == "status":
            status(config)
        else:
            local = args.command == "use-local"
            save_json(CONNECTION, connection(config, local))
            print(("로컬 API" if local else "기존 서버") + " 선택 완료. 다음 Unity Play부터 적용됩니다.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # API 응답 본문·인물 내용·SSH stderr·토큰을 출력하지 않는다.
        print("로컬 시연 준비 실패: " + str(exc), file=sys.stderr)
        sys.exit(1)
