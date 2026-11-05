"""로컬 시연 전용 설정. 운영 env·서비스·인물 폴더를 사용하지 않는다."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = (ROOT / "bundle.json").is_file()
DATA = (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DasiBom" / "LocalApiDemo"
        if PACKAGED else ROOT / ".dialogue-work" / "local-demo")
CONFIG = DATA / "settings.json" if PACKAGED else ROOT / "UserSettings" / "LocalDemo.json"
CONNECTION = DATA / "connection.json" if PACKAGED else ROOT / "UserSettings" / "LocalDemoConnection.json"
MODELS = ROOT / "models" if PACKAGED else DATA / "models"
RUNTIME_PYTHON = ROOT.parent / "runtime" / "python.exe"
VENV = ROOT / "tools" / "_work" / "local_demo" / "venv"
WEB_VENV = ROOT / "tools" / "_work" / "local_demo" / "web-venv"
VOX_SOURCE = ROOT / "voxcpm-source" if PACKAGED else ROOT / "tools" / "_work" / "local_demo" / "voxcpm-source"
VOX_COMMIT = "f772e498a45fbb5fb8e13fbf9b9c48be9fe33e69"
VOX_REVISION = "32279effe8c19989596f05d353d1447f51d9e915"
DEFAULTS = {
    "version": 1,
    "registration_port": 18000,
    "dialogue_port": 18002,
    "tts_port": 18003,
    "worker_port": 18004,
    "web_port": 18500,
    "llm_model": "gpt-5.4-mini",
    "stt_model": "gpt-transcribe",
    "normal_effort": "none",
    "reasoning_effort": "low",
    "normal_tokens": 384,
    "reasoning_tokens": 2048,
    "context_tokens": 24000,
    "silence_ms": 700,
}
if PACKAGED:
    # 개발 웹과 배포본을 같은 PC에서 켜도 서로의 자료·프로세스를 사용하지 않는다.
    DEFAULTS.update(registration_port=19000, dialogue_port=19002, tts_port=19003,
                    worker_port=19004, web_port=19500)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


def read_config(path=CONFIG):
    value = {**DEFAULTS, **json.loads(path.read_text(encoding="utf-8"))}
    if value.get("version") != 1 or not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        raise ValueError("로컬 설정이 없거나 잘못됐습니다. local_demo.py init을 실행하세요.")
    ports = [value.get(name + "_port") for name in ("registration", "dialogue", "tts", "worker", "web")]
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != len(ports):
        raise ValueError("로컬 서비스 포트는 서로 다른 1024~65535 값이어야 합니다.")
    for name in ("normal_tokens", "reasoning_tokens", "context_tokens", "silence_ms"):
        if type(value.get(name)) is not int or value[name] <= 0:
            raise ValueError("로컬 수치 설정이 잘못됐습니다: " + name)
    return value


def initialize():
    if not CONFIG.exists():
        save_json(CONFIG, {**DEFAULTS, "token": secrets.token_hex(32)})
    config = read_config()
    save_json(CONFIG, config)
    if PACKAGED or not CONNECTION.exists():
        save_json(CONNECTION, connection(config, PACKAGED))
    return config


def connection(config, enabled):
    return {"enabled": enabled, "serverUrl": address(config, "registration"),
            "dialogueServerUrl": address(config, "dialogue"), "token": config["token"]}


def address(config, service):
    # 외부 공개 호스트를 받지 않는다. 모든 서비스는 이 PC에서만 접근한다.
    return "http://127.0.0.1:" + str(config[service + "_port"])
