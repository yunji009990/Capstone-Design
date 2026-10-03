#!/usr/bin/env python3
"""현재 Linux 서버 실행 구성을 비공개로 보관하고 같은 구성으로 재시작한다.

capture는 실행 중인 6개 서비스의 명령·환경을 서버에만 저장한다. 토큰이 포함될 수
있으므로 보관 폴더는 700, 파일은 600이며 내용은 출력하거나 Git에 넣지 않는다.
공용 GPU 서버 전체를 종료하지 않고 확인한 프로젝트 프로세스만 제어한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SERVICES = {
    "registration": (8000, "registration", "registration.app:app", "webapp/Server"),
    "llm": (8001, "vllm", None, None),
    "tts": (8003, "qwentts", "tts_server:app", "capstone-server"),
    "dialogue": (8002, "dialogue", "dialogue_server:app", "capstone-server"),
    "tripo": (None, "tripo", None, None),
    "web": (8500, "web", "app:app", "webapp/Web"),
}
STOP_ORDER = ("web", "tripo", "dialogue", "tts", "llm", "registration")
CONFIGS = ("capstone-server/dialogue.env", "capstone-server/tts.env",
           "capstone-server/service-paths.env", "webapp/Server/session.env",
           "webapp/Server/service-paths.env", "webapp/Web/.env")
PIDFILES = {"registration": "webapp/Server/session.pid", "llm": "capstone-runtime/llm.pid",
            "tts": "capstone-server/tts.pid", "dialogue": "capstone-server/dialogue.pid",
            "tripo": "webapp/Server/tripo.pid", "web": "webapp/Web/web.pid"}


@contextmanager
def operation_lock(folder):
    import fcntl  # Linux 운영 서버에서만 호출한다. Windows 모의 검사는 import하지 않는다.
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    fd = os.open(folder / "operation.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another start, stop or capture is already running") from None
        yield
    finally:
        os.close(fd)


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def read_process(pid):
    base = Path("/proc") / str(pid)
    try:
        if base.stat().st_uid != os.getuid():
            return None
        stat = (base / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {"pid": int(pid), "ppid": int(stat[1]), "start": int(stat[19]),
                "argv": [a for a in (base / "cmdline").read_bytes().decode().split("\0") if a],
                "cwd": os.readlink(base / "cwd")}
    except (OSError, UnicodeError, ValueError):
        return None


def processes():
    return {int(p.name): item for p in Path("/proc").iterdir() if p.name.isdigit()
            if (item := read_process(int(p.name))) is not None}


def option(args, key):
    try:
        return args[args.index(key) + 1]
    except (ValueError, IndexError):
        return None


def matches(name, item, home):
    port, environment, module, appdir = SERVICES[name]
    args = item["argv"]
    if not args or Path(args[0]).parent != home / "venv" / environment / "bin":
        return False
    if name == "tripo":
        return str(home / "webapp/Survey/model_worker.py") in args
    if option(args, "--port") != str(port):
        return False
    if name == "llm":
        return "serve" in args and str(home / "models/gemma-4-31B-qat") in args
    return module in args and option(args, "--app-dir") == str(home / appdir)


def find(name, table, home):
    found = [p for p in table.values() if matches(name, p, home)]
    if len(found) > 1:
        raise RuntimeError(name + ": multiple matching processes; no action taken")
    return found[0] if found else None


def listening(port):
    if port is None:
        return False
    with socket.socket() as client:
        client.settimeout(1)
        return client.connect_ex(("127.0.0.1", port)) == 0


def get_json(port, route):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{route}", timeout=4) as response:
        return json.load(response)


def idle_guard(table, home):
    if find("web", table, home):
        worker = get_json(8500, "/status").get("model_worker", {})
        if worker.get("active_jobs") != 0 or (worker.get("jobs") or {}).get("queued", 0):
            raise RuntimeError("model jobs active or unknown; stop postponed")
    if find("dialogue", table, home) and get_json(8002, "/health").get("connections") != 0:
        raise RuntimeError("dialogue connections active or unknown; stop postponed")
    if find("tts", table, home) and get_json(8003, "/health").get("busy") is not False:
        raise RuntimeError("TTS busy or unknown; stop postponed")
    if find("llm", table, home):
        with urllib.request.urlopen("http://127.0.0.1:8001/metrics", timeout=4) as response:
            lines = response.read().decode().splitlines()
        for metric in ("num_requests_running", "num_requests_waiting"):
            values = [float(line.rsplit(" ", 1)[1]) for line in lines
                      if line.startswith("vllm:" + metric)]
            if not values or any(value != 0 for value in values):
                raise RuntimeError("LLM requests active or unknown; stop postponed")


def capture(home, folder):
    profile_path = folder / "profile.json"
    if profile_path.exists():
        raise RuntimeError("profile already exists; use a new --state-dir")
    table = processes()
    records = {}
    for name in SERVICES:
        item = find(name, table, home)
        if item is None:
            raise RuntimeError(name + ": running process required for capture")
        raw = (Path("/proc") / str(item["pid"]) / "environ").read_bytes().decode()
        environment = dict(part.split("=", 1) for part in raw.split("\0") if "=" in part)
        # 재사용할 수 없는 SSH 세션·셸 상태는 제외하고 모델·GPU·인증 환경은 보존한다.
        item["env"] = {key: value for key, value in environment.items()
                       if not key.startswith("SSH_") and key not in ("PWD", "OLDPWD", "SHLVL", "_")}
        records[name] = item
    idle_guard(table, home)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    configs = {}
    for name in CONFIGS:
        path = home / name
        if not path.is_file():
            continue
        blob = path.read_bytes()
        backup = folder / "config" / name
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
        configs[name] = hashlib.sha256(blob).hexdigest()
    private_json(profile_path, {"version": 1, "home": str(home),
                 "captured_utc": datetime.now(timezone.utc).isoformat(),
                 "services": records, "configs": configs})
    print("Captured 6 services and private configuration backups", flush=True)


def load_profile(home, folder):
    path = folder / "profile.json"
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise RuntimeError("profile must be owned by this user and mode 600")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("version") != 1 or profile.get("home") != str(home):
        raise RuntimeError("profile version or server home mismatch")
    if set(profile["services"]) != set(SERVICES):
        raise RuntimeError("incomplete service profile")
    for name, item in profile["services"].items():
        if not matches(name, item, home):
            raise RuntimeError(name + ": profile command identity mismatch")
    return profile


def validate_start(profile, home):
    for name, expected in profile["configs"].items():
        path = home / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("configuration changed: " + name)
    for name, item in profile["services"].items():
        if not os.access(item["argv"][0], os.X_OK) or not Path(item["cwd"]).is_dir():
            raise RuntimeError(name + ": saved Python or working directory unavailable")


def same_process(item):
    current = read_process(item["pid"])
    # Python 실행 직후 argv가 확정되거나 프로세스 제목이 바뀌어도 같은 실행이다.
    return current is not None and current["start"] == item["start"]


def send_term(item):
    # PID가 재사용되는 순간에도 다른 프로세스에 신호가 전달되지 않도록 pidfd를 쓴다.
    try:
        fd = os.pidfd_open(item["pid"])
    except ProcessLookupError:
        return False
    try:
        if not same_process(item):
            return False
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except ProcessLookupError:
            return False
        return True
    finally:
        os.close(fd)


def update_pidfile(name, item, home, remove=False):
    if name == "llm":
        return  # 기존 LLM 실행기는 PID 파일을 사용하지 않는다.
    path = home / PIDFILES[name]
    if remove:
        try:
            if path.read_text().strip() == str(item["pid"]):
                path.unlink()
        except FileNotFoundError:
            pass
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(str(item["pid"]) + "\n")


def descendants(item, table):
    selected = {item["pid"]}
    while True:
        more = {p["pid"] for p in table.values() if p["ppid"] in selected}
        if more <= selected:
            return [table[pid] for pid in selected if pid in table]
        selected.update(more)


def terminate(item, timeout=60):
    if not same_process(item):
        return
    family = descendants(item, processes())
    if not send_term(item):
        return
    deadline = time.monotonic() + timeout
    child_deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        alive = [p for p in family if same_process(p)]
        if not alive:
            return
        if time.monotonic() >= child_deadline:
            for child in alive:
                if child["pid"] != item["pid"] and same_process(child):
                    send_term(child)
            child_deadline = deadline
        time.sleep(.5)
    raise RuntimeError("process did not stop gracefully; no SIGKILL sent")


def ready(name):
    port = SERVICES[name][0]
    if name == "tripo":
        return True
    if name == "llm":
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4) as response:
            return response.status == 200
    data = get_json(port, "/status" if name == "web" else "/health")
    if name == "web":
        worker = data.get("model_worker") or {}
        return worker.get("online") is True and worker.get("configured") is True
    if name == "dialogue":
        return data.get("status") == "ready" and data.get("llm_ready") is True and data.get("tts_ready") is True
    return data.get("status") == "ready"


def launched_process(process, saved, timeout=5):
    deadline = time.monotonic() + timeout
    initial = None
    while time.monotonic() < deadline:
        item = read_process(process.pid)
        if item is not None:
            if initial is None:
                initial = item
            if item["start"] != initial["start"]:
                raise RuntimeError("launched process identity changed")
            if item["argv"] == saved["argv"]:
                return item
        if process.poll() is not None:
            break
        time.sleep(.05)
    if initial is not None:
        terminate(initial)
    raise RuntimeError("launched command unavailable; inspect private log")


def start(profile, home, folder):
    validate_start(profile, home)
    # 먼저 모든 충돌을 확인해 일부 서비스만 시작하고서 충돌을 발견하지 않게 한다.
    table = processes()
    for name, saved in profile["services"].items():
        current = find(name, table, home)
        if current and current["argv"] != saved["argv"]:
            raise RuntimeError(name + ": another configuration is already running")
        if current is None and listening(SERVICES[name][0]):
            raise RuntimeError(name + ": port is occupied by another process")
    started = []
    (folder / "logs").mkdir(exist_ok=True, mode=0o700)
    try:
        for name in SERVICES:
            saved = profile["services"][name]
            current = find(name, processes(), home)
            if current is None:
                print("Starting " + name, flush=True)
                logpath = folder / "logs" / (name + ".log")
                fd = os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "ab") as log:
                    process = subprocess.Popen(saved["argv"], cwd=saved["cwd"], env=saved["env"],
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                               start_new_session=True)
                current = launched_process(process, saved)
                started.append((name, current))
                if current["argv"] != saved["argv"] or not matches(name, current, home):
                    raise RuntimeError(name + ": launched command identity mismatch")
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                if not same_process(current):
                    raise RuntimeError(name + ": process exited; inspect private log")
                try:
                    if ready(name):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(2)
            else:
                raise RuntimeError(name + ": readiness timeout; inspect private log")
            update_pidfile(name, current, home)
            print(name + " ready", flush=True)
    except Exception:
        for name, item in reversed(started):
            try:
                terminate(item)
                update_pidfile(name, item, home, remove=True)
            except Exception:
                print(name + ": rollback incomplete; inspect status", flush=True)
        raise


def stop(home):
    idle_guard(processes(), home)
    for name in STOP_ORDER:
        item = find(name, processes(), home)
        if item:
            print("Stopping " + name, flush=True)
            terminate(item)
            update_pidfile(name, item, home, remove=True)
        print(name + " stopped", flush=True)


def status(home):
    table = processes()
    return {name: {"pid": item["pid"] if (item := find(name, table, home)) else None,
                   "port": spec[0], "listening": listening(spec[0])}
            for name, spec in SERVICES.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "check", "start", "stop", "status"))
    parser.add_argument("--state-dir", type=Path, default=Path.home() / "capstone-runtime")
    args = parser.parse_args()
    if os.name != "posix" or not Path("/proc").is_dir():
        parser.error("Run on the Linux project server through SSH")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        parser.error("Python 3.9+ with Linux pidfd support is required")
    os.umask(0o077)
    home, folder = Path.home(), args.state_dir.expanduser().resolve()
    try:
        if args.action == "capture":
            with operation_lock(folder):
                capture(home, folder)
        elif args.action in ("check", "start"):
            profile = load_profile(home, folder)
            validate_start(profile, home)
            if args.action == "start":
                with operation_lock(folder):
                    start(profile, home, folder)
            else:
                print("Saved commands, environments and configuration files are ready")
        elif args.action == "stop":
            with operation_lock(folder):
                stop(home)
        print(json.dumps(status(home), ensure_ascii=True))
    except Exception as error:
        # 예외 객체에 명령 인수·토큰이 실릴 수 있으므로 제어 오류만 원문을 출력한다.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print("ERROR: " + message, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
