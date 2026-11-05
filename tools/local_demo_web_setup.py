"""서버를 켜지 않고 로컬 웹의 생성 자산·비공개 설정만 복사한다."""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import tarfile

from local_demo_config import DATA, ROOT

FACE_FILES = {"1k3d68.onnx", "2d106det.onnx", "det_10g.onnx", "genderage.onnx", "w600k_r50.onnx"}
OPTIONS = {"TRIPO_API_KEY", "SURVEY_ACCESS_CODE", "ADMIN_PASSWORD", "TRIPO_FACE_LIMIT",
           "TRIPO_GEOMETRY_QUALITY", "HEAD_CUTOUT_FIT", "HEAD_BODY_BONE", "HEAD_BODY_FROM",
           "HEAD_BODY_SHARE", "HEAD_BODY_YAW", "HEAD_BODY_OUT_SCALE"}


def blender_path():
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Blender Foundation"
    candidates = [base / "Blender 4.5/blender.exe", *sorted(base.glob("*/blender.exe"))]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError("Blender를 먼저 설치하세요. 로컬 생성 작업에 필요합니다.")


def install_archive(blob, blender):
    """경로·종류·크기를 검사하고 허용된 파일만 쓴다. 현재 등록 인물은 건드리지 않는다."""
    targets = {"body.glb": DATA / "models/body.glb",
               "cutout.tflite": DATA / "models/selfie_multiclass_256x256.tflite"}
    targets.update({"face/" + name: DATA / "models/insightface/models/buffalo_l" / name for name in FACE_FILES})
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        if len(set(names)) != len(names) or set(names) != set(targets) | {"settings.json"}:
            raise ValueError("생성 자산 목록이 예상과 다릅니다.")
        for item in members:
            limit = 16384 if item.name == "settings.json" else 256 * 1024 * 1024
            if not item.isfile() or not 0 < item.size <= limit:
                raise ValueError("생성 자산 종류 또는 크기가 잘못됐습니다.")
        settings = json.loads(archive.extractfile("settings.json").read())
        if (not isinstance(settings, dict) or not set(settings).issubset(OPTIONS)
                or any(not isinstance(v, str) or any(c in v for c in '\r\n\x00') for v in settings.values())
                or not settings.get("TRIPO_API_KEY")):
            raise ValueError("생성 서비스 설정이 잘못됐습니다.")
        # 재실행 때 로컬에서 바꾼 자산·키를 조용히 덮지 않는다.
        for name, target in targets.items():
            if target.exists() and (target.is_symlink() or target.read_bytes() != archive.extractfile(name).read()):
                raise ValueError("로컬 생성 자산이 달라 덮어쓰지 않았습니다.")
        env = DATA / "web.env"
        if env.exists():
            raise ValueError("로컬 웹 설정이 이미 있습니다. 기존 web.env를 보존했습니다.")
        for name, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(archive.extractfile(name).read())
        settings.setdefault("ADMIN_PASSWORD", secrets.token_urlsafe(24))
        settings.setdefault("SURVEY_ACCESS_CODE", "")
        settings.update(HEAD_BODY_GLB=str(targets["body.glb"]),
                        HEAD_CUTOUT_MODEL=str(targets["cutout.tflite"]), BLENDER_BIN=str(blender))
        env.write_text("\n".join(k + "=" + v for k, v in settings.items()) + "\n", encoding="utf-8")
        os.chmod(env, 0o600)


def import_web():
    blender = blender_path()
    remote = '''import io,json,pathlib,sys,tarfile
root=pathlib.Path.home()
values={}
for line in (root/"webapp/Web/.env").read_text().splitlines():
 if "=" in line and not line.lstrip().startswith("#"):
  k,v=line.split("=",1);values[k.strip()]=v.strip().strip(chr(34)).strip(chr(39))
allowed={"TRIPO_API_KEY","SURVEY_ACCESS_CODE","ADMIN_PASSWORD","TRIPO_FACE_LIMIT","TRIPO_GEOMETRY_QUALITY","HEAD_CUTOUT_FIT","HEAD_BODY_BONE","HEAD_BODY_FROM","HEAD_BODY_SHARE","HEAD_BODY_YAW","HEAD_BODY_OUT_SCALE"}
settings={k:v for k,v in values.items() if k in allowed}
paths={"body.glb":pathlib.Path(values["HEAD_BODY_GLB"]),"cutout.tflite":pathlib.Path(values["HEAD_CUTOUT_MODEL"])}
for name in ("1k3d68.onnx","2d106det.onnx","det_10g.onnx","genderage.onnx","w600k_r50.onnx"):
 paths["face/"+name]=root/".insightface/models/buffalo_l"/name
for p in paths.values():
 if not p.is_file() or p.stat().st_size>268435456: raise ValueError("invalid asset")
with tarfile.open(fileobj=sys.stdout.buffer,mode="w|") as out:
 data=json.dumps(settings).encode();info=tarfile.TarInfo("settings.json");info.size=len(data);out.addfile(info,io.BytesIO(data))
 for name,p in paths.items(): out.add(p,arcname=name,recursive=False)
'''
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "raon", "python3 -"],
                            input=remote.encode(), capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError("생성 자산을 읽지 못했습니다. 서버 SSH 연결과 자산 설정을 확인하세요.")
    install_archive(result.stdout, blender)
    print("로컬 생성 자산·비공개 Tripo 설정 준비 완료. 서버 서비스와 등록 인물은 변경하지 않았습니다.")


def prepare_friend():
    """현재 친구의 자료를 별도 보관한다. 생성 API·실행 중인 세션은 변경하지 않는다."""
    sys.path.insert(0, str(ROOT / "Web"))
    import survey_v2
    from prepared_friend import load_bundle
    target = DATA / "prepared-friend"
    if target.exists():
        load_bundle(target)
        print("준비된 친구 자료가 이미 있습니다. 기존 자료를 유지합니다.")
        return
    sid = json.loads((DATA / "sessions/current.json").read_text(encoding="utf-8"))["session"]
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sid):
        raise ValueError("현재 등록 인물을 확인하지 못했습니다.")
    source = DATA / "sessions" / sid
    if source.is_symlink():
        raise ValueError("등록 자료 경로를 확인하지 못했습니다.")
    profiles = json.loads((ROOT / "Web/static/presets_v2.json").read_text(encoding="utf-8"))["profiles"]
    answers = next(p["answers"] for p in profiles if p["key"] == "friend")
    compiled = survey_v2.compile_survey(survey_v2.validate(answers))
    if any((source / (name + ".md")).read_text(encoding="utf-8") != compiled[name]
           for name in ("persona", "knowledge", "rules")):
        raise ValueError("현재 인물과 친구 프리셋이 다릅니다. 자료를 자동으로 섞지 않습니다.")
    assets = {name: (source / name).read_bytes() for name in ("voice.wav", "model.glb")}
    local_photo = next((DATA / "survey/sessions" / sid / ("front" + ext)
                        for ext in (".png", ".jpg", ".jpeg")
                        if (DATA / "survey/sessions" / sid / ("front" + ext)).is_file()), None)
    if local_photo:
        suffix, photo = local_photo.suffix, local_photo.read_bytes()
    else:
        # 원본 사진만 읽는다. 서비스 시작·생성 요청은 보내지 않는다.
        remote = '''import json,pathlib,sys
sid=SID_LITERAL
root=pathlib.Path.home()/"webapp"
values={}
for line in (root/"Web/.env").read_text().splitlines():
 if "=" in line and not line.lstrip().startswith("#"):
  k,v=line.split("=",1);values[k.strip()]=v.strip().strip(chr(34)).strip(chr(39))
base=pathlib.Path(values.get("SURVEY_DATA_DIR",str(root/"Survey/data"))).expanduser()
if not base.is_absolute(): base=root/"Web"/base
parent=base/"sessions"/sid
if parent.is_symlink(): raise ValueError("invalid path")
p=next(p for ext in (".png",".jpg",".jpeg") if (p:=parent/("front"+ext)).is_file())
if p.is_symlink() or not 0<p.stat().st_size<=20971520: raise ValueError("invalid photo")
sys.stdout.buffer.write(json.dumps({"suffix":p.suffix}).encode()+b"\\n"+p.read_bytes())
'''.replace("SID_LITERAL", repr(sid))
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "raon", "python3 -"],
                                input=remote.encode(), capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("등록된 친구의 원본 사진을 읽지 못했습니다. SSH 연결과 등록 사진을 확인하세요.")
        header, photo = result.stdout.split(b"\n", 1)
        suffix = json.loads(header)["suffix"]
    photo_name = "photo.png" if suffix == ".png" else "photo.jpg"
    assets[photo_name] = photo
    assets["survey.json"] = json.dumps(answers, ensure_ascii=False).encode("utf-8")
    manifest = {"version": 1, "photo": photo_name, "survey_revision": compiled["survey_revision"],
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()}}
    staged = DATA / (".prepared-friend-" + secrets.token_hex(6))
    staged.mkdir(mode=0o700)
    try:
        for name, data in assets.items():
            (staged / name).write_bytes(data)
            os.chmod(staged / name, 0o600)
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        load_bundle(staged)
        staged.rename(target)
    finally:
        if staged.exists():
            for item in staged.iterdir():
                item.unlink()
            staged.rmdir()
    print("친구의 기존 사진·참조 음성·완성 모델 준비 완료. Tripo 호출과 세션 변경은 없습니다.")
