"""Build an explicit deployment bundle for only one server domain (no secrets/data).

python tools/service_bundle.py platform
python tools/service_bundle.py dialogue
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def files_for(area):
    if area == "platform":
        paths = [ROOT / "Web/app.py", ROOT / "Web/survey_v2.py", ROOT / "Web/.env.example", ROOT / "Web/requirements.txt",
                 ROOT / "Survey/model_worker.py", ROOT / "Survey/requirements-worker.txt"]
        paths += [ROOT / "Web/static" / name for name in ("index.html", "admin.html", "after.html",
                                                          "style.css", "presets_v2.json")]
        paths += sorted((ROOT / "Survey/core").glob("*.py"))
        paths += sorted((ROOT / "Server/registration").glob("*.py"))
        paths += [ROOT / "Server" / name for name in (
            "session_server.py", "service.sh", "platform.sh", "web_start.sh", "web_stop.sh",
            "requirements-registration.txt", "session.env.example")]
        return [(path, path.relative_to(ROOT).as_posix()) for path in paths]
    names = ("dialogue_server.py", "persona_client.py", "realtime_dialogue.py", "realtime_audio.py",
             "realtime_llm.py", "realtime_tts.py", "interruption_policy.py", "persona_context.py", "dialogue_memory.py",
             "speech_gate.py", "setup_dialogue_models.py", "dialogue_diagnostics.py",
             "voice_reference.py", "dialogue_reactions.py", "dialogue_system_lines.py",
             "tts_server.py", "tts_omni.py", "tts_vox.py", "tts_vox_native.py",
             "tts_streaming.yaml", "setup_tts_streaming.py",
             "service.sh", "dialogue.sh", "platform.sh", "requirements-dialogue.txt",
             "requirements-tts.txt", "requirements-tts-streaming.txt", "dialogue.env.example", "tts.env.example")
    return [(ROOT / "Server" / name, name) for name in names]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("area", choices=("platform", "dialogue"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    target = args.out or ROOT / "tools/_work/service_split_20260912" / (args.area + ".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"area": args.area, "files": []}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in files_for(args.area):
            raw = path.read_bytes()
            archive.writestr(name, raw)
            manifest["files"].append({"path": name, "source": path.relative_to(ROOT).as_posix(),
                                      "sha256": hashlib.sha256(raw).hexdigest()})
        archive.writestr("bundle-manifest.json", json.dumps(manifest, indent=2))
    print(json.dumps({"area": args.area, "files": len(manifest["files"]), "bundle": str(target)}))


if __name__ == "__main__":
    main()
