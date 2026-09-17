"""Run one resumable Tripo generation/rig/animation trial outside production sessions.

Outputs (including the reference photo) belong under git-ignored tools/_work/.
The existing Web/.env key is read locally and never copied into the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Survey"))
from core.tripo import (API_BASE, TPOSE_MODEL, TPOSE_PROMPT, TripoClient,  # noqa: E402
                        image_extension, image_url, tpose_credits)


def read_key() -> str:
    key = os.environ.get("TRIPO_API_KEY", "").strip()
    if not key:
        env_path = ROOT / "Web" / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                name, separator, value = line.strip().partition("=")
                if separator and name.strip() == "TRIPO_API_KEY":
                    key = value.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("TRIPO_API_KEY is missing")
    return key


def glb_summary(path: Path) -> dict:
    blob = path.read_bytes()
    magic, version, total = struct.unpack_from("<4sII", blob)
    if magic != b"glTF" or version != 2 or total != len(blob):
        raise ValueError("Not a complete glTF 2.0 binary")
    document, binary = None, b""
    offset = 12
    while offset < total:
        length, kind = struct.unpack_from("<II", blob, offset)
        chunk = blob[offset + 8:offset + 8 + length]
        if kind == 0x4E4F534A:
            document = json.loads(chunk)
        elif kind == 0x004E4942:
            binary = chunk
        offset += 8 + length
    if document is None:
        raise ValueError("Missing GLB JSON")

    accessors = document.get("accessors", [])

    def floats(index: int) -> list[tuple]:
        accessor = accessors[index]
        if accessor["componentType"] != 5126 or "sparse" in accessor:
            raise ValueError("Animation inspection expects nonsparse float accessors")
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
        view = document["bufferViews"][accessor["bufferView"]]
        if view.get("buffer", 0) != 0:
            raise ValueError("External animation buffer is not supported")
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        stride = view.get("byteStride", width * 4)
        return [struct.unpack_from("<" + "f" * width, binary, start + i * stride)
                for i in range(accessor["count"])]

    animations = []
    for animation in document.get("animations", []):
        varying, duration = 0, 0.0
        for sampler in animation.get("samplers", []):
            times = floats(sampler["input"])
            duration = max(duration, max((t[0] for t in times), default=0))
        for channel in animation.get("channels", []):
            sampler = animation["samplers"][channel["sampler"]]
            values = floats(sampler["output"])
            if sampler.get("interpolation") == "CUBICSPLINE":
                values = values[1::3]
            if values and any(max(c) - min(c) > 1e-5 for c in zip(*values)):
                varying += 1
        animations.append({"name": animation.get("name", ""),
                           "duration_sec": round(duration, 4),
                           "channels": len(animation.get("channels", [])),
                           "varying_channels": varying})

    primitives = [p for mesh in document.get("meshes", []) for p in mesh.get("primitives", [])]
    triangles = sum(accessors[p["indices"]]["count"] // 3
                    for p in primitives if "indices" in p and p.get("mode", 4) == 4)
    skins = document.get("skins", [])
    return {"file": path.name, "bytes": len(blob), "triangles": triangles,
            "skins": len(skins), "joints": sum(len(s.get("joints", [])) for s in skins),
            "skinned_mesh_nodes": sum("skin" in n and "mesh" in n for n in document.get("nodes", [])),
            "weighted_primitives": sum("JOINTS_0" in p.get("attributes", {}) and
                                       "WEIGHTS_0" in p.get("attributes", {}) for p in primitives),
            "animations": animations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tpose", action="store_true",
                        help="Tripo generate_image(t_pose)로 T포즈 이미지를 먼저 만들고 그 결과로 생성한다")
    parser.add_argument("--tpose-model", default=TPOSE_MODEL,
                        help="T포즈 이미지 모델. 기본 gemini_2.5_flash_image_preview(5). gemini_3_pro_image_preview 등은 10")
    parser.add_argument("--tpose-only", action="store_true",
                        help="T포즈 이미지까지만 받고 멈춘다. 같은 --out 으로 다시 실행하면 이어서 생성한다")
    parser.add_argument("--tpose-prompt", default=TPOSE_PROMPT,
                        help="T포즈 이미지 프롬프트를 바꿔 스타일을 실험한다. 프롬프트가 다르면 "
                             "다른 시험이므로 새 --out 폴더를 쓴다")
    parser.add_argument("--multiview", action="store_true",
                        help="4뷰를 먼저 만들고(generate_multiview_image, 10) multiview_to_model 로 생성한다. "
                             "한 장에서 뒤·옆을 지어내지 않아 좌우 비대칭이 준다")
    args = parser.parse_args()
    image_path = args.image.resolve(strict=True)
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / "tools" / "_work"):
        parser.error("--out must be inside tools/_work (private, git-ignored trial outputs)")
    image_type = image_path.suffix.lower().lstrip(".").replace("jpeg", "jpg")
    if image_type not in ("jpg", "png"):
        parser.error("Use a JPG or PNG reference")

    config = {"model_version": "v3.1-20260211", "face_limit": 50000,
              "texture": True, "pbr": True, "texture_quality": "detailed",
              "geometry_quality": "standard", "texture_alignment": "original_image",
              "enable_image_autofix": False, "model_seed": 20260909, "texture_seed": 20260909}
    identity = {"image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "generation": config, "rig_version": "v1.0-20240301",
                "rig_spec": "tripo", "animation": "preset:sit"}
    if args.multiview:
        identity["multiview"] = {"image_task": "generate_multiview_image",
                                 "model_task": "multiview_to_model"}
    extra = tpose_credits(args.tpose_model) if args.tpose else 0
    # 4뷰 생성 10 + multiview_to_model 40(=30+detailed 10). image_to_model 은 40.
    base = 85 if args.multiview else 75
    if args.tpose:
        # 기존 폴더의 request 와 그대로 비교되도록 --tpose 일 때만 키를 더한다.
        identity["tpose"] = {"model_version": args.tpose_model, "prompt": args.tpose_prompt, "t_pose": True}
    if args.dry_run:
        print(json.dumps({"request": identity, "estimated_credits": base + extra, "network_calls": 0}, indent=2))
        return 0

    client = TripoClient(read_key())
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "trial.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "request": identity, "tasks": {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if manifest["request"] != identity:
        raise RuntimeError("Output contains a different trial; choose a new --out directory")

    def save():
        temporary = manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(manifest_path)

    def emit(stage, **values):
        print(json.dumps({"stage": stage, **values}, ensure_ascii=True), flush=True)

    def task(stage: str, body: dict, timeout=900) -> dict:
        record = manifest["tasks"].get(stage)
        if not record:
            task_id = client._submit(body)
            record = manifest["tasks"][stage] = {"task_id": task_id, "request": body}
            save()
            emit(stage, task_id=task_id, submitted=True)
        task_id = record["task_id"]
        deadline = time.monotonic() + timeout
        previous = None
        poll_failures = 0
        while time.monotonic() < deadline:
            try:
                response = client.get_task(task_id)
                poll_failures = 0
            except Exception as exc:
                transient = isinstance(exc, OSError) or any(
                    f"[{code}]" in str(exc) for code in (429, 500, 502, 503, 504))
                if not transient or poll_failures >= 3:
                    raise
                poll_failures += 1
                emit(stage, status="poll_retry", attempt=poll_failures)
                time.sleep(5)
                continue
            if response.get("code", 0) != 0:
                raise RuntimeError(f"{stage}: task query returned code {response.get('code')}")
            data = response.get("data") or {}
            record["response"] = data
            save()
            current = (data.get("status"), data.get("progress"))
            if current != previous:
                emit(stage, status=current[0], progress=current[1], credits=data.get("consumed_credit"))
                previous = current
            if current[0] == "success":
                return data
            if current[0] in {"failed", "banned", "expired", "cancelled", "canceled", "unknown"}:
                raise RuntimeError(f"{stage}: {data.get('error_msg') or data.get('error') or current[0]}")
            time.sleep(5)
        raise TimeoutError(f"{stage}: timed out; rerun with the same output to resume polling")

    def save_views(output: dict) -> list:
        """4뷰 이미지를 순서대로 내려받는다. 문서가 output 키를 적어두지 않아
        http 로 시작하는 값을 나온 순서대로 모은다(generate_image 와 같은 방식)."""
        urls = []

        def walk(value):
            if isinstance(value, str) and value.startswith("http"):
                if value not in urls:
                    urls.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(output)
        saved = []
        for index, url in enumerate(urls[:4]):
            label = ("front", "left", "back", "right")[index]
            stem = f"multiview_{index}_{label}"
            existing = [p for p in out.glob(stem + ".*") if p.suffix != ".partial"]
            if existing:
                dest = existing[0]
            else:
                part = out / (stem + ".partial")
                client.download_file(url, part)
                ext = image_extension(part.read_bytes()[:16]) or "png"
                dest = part.replace(out / f"{stem}.{ext}")
            saved.append({"file": dest.name, "bytes": dest.stat().st_size})
        return saved

    def download(stage: str, data: dict, filename: str) -> Path:
        output = data.get("output") or {}
        url = output.get("pbr_model") or output.get("model")
        if isinstance(url, dict):
            url = url.get("url")
        if not url:
            raise RuntimeError(f"{stage}: response has no model URL")
        dest = out / filename
        if not dest.exists():
            part = dest.with_suffix(".partial")
            client.download_glb(url, part)
            part.replace(dest)
        summary = glb_summary(dest)
        manifest.setdefault("assets", {})[stage] = summary
        save()
        emit(stage, asset=summary)
        return dest

    manifest["status"] = "running"
    manifest.pop("error", None)
    save()
    try:
        if "balance_before" not in manifest:
            balance = client._get(API_BASE + "/user/balance").get("data") or {}
            manifest["balance_before"] = balance
            if float(balance.get("balance", 0)) < base + extra:
                raise RuntimeError(f"Trial requires at least {base + extra} credits")
            save()
        reference = out / ("reference." + image_type)
        if not reference.exists():
            shutil.copyfile(image_path, reference)
        source, source_type = image_path, image_type
        if args.tpose:
            # 원본 대신 T포즈 이미지를 생성 입력으로 쓴다. 팔을 내린 사진은 리깅 뒤
            # 팔을 들 때 옷이 손을 따라 늘어나기 때문이다(문서: Tripo_T포즈_전처리_실험).
            if "tpose" not in manifest["tasks"]:
                token = client._upload_image(image_path)
                tpose_body = {"type": "generate_image", "model_version": args.tpose_model,
                              "prompt": args.tpose_prompt, "file": {"type": image_type, "file_token": token},
                              "t_pose": True}
            else:
                tpose_body = manifest["tasks"]["tpose"]["request"]
            posed = task("tpose", tpose_body, 300)
            existing = [p for p in out.glob("reference_tpose.*") if p.suffix != ".partial"]
            if existing:
                source = existing[0]
            else:
                url = image_url(posed.get("output") or {})
                if not url:
                    raise RuntimeError("tpose: response has no image URL: "
                                       + json.dumps(posed.get("output"), ensure_ascii=False)[:500])
                part = out / "reference_tpose.partial"
                client.download_file(url, part)
                ext = image_extension(part.read_bytes()[:16])
                if ext is None:
                    part.unlink()
                    raise RuntimeError("tpose: downloaded file is not a PNG/JPEG/WEBP image")
                source = part.replace(out / ("reference_tpose." + ext))
            source_type = source.suffix.lstrip(".")
            manifest.setdefault("assets", {})["tpose"] = {
                "file": source.name, "bytes": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            save()
            emit("tpose", asset=manifest["assets"]["tpose"])
            if args.tpose_only:
                manifest["status"] = "tpose_ready"
                emit("complete", status=manifest["status"])
                return 0
        multiview_id = None
        if args.multiview:
            if "multiview_image" not in manifest["tasks"]:
                token = client._upload_image(source)
                views_body = {"type": "generate_multiview_image",
                              "file": {"type": source_type, "file_token": token}}
            else:
                views_body = manifest["tasks"]["multiview_image"]["request"]
            views = task("multiview_image", views_body, 300)
            saved = save_views(views.get("output") or {})
            if saved:
                manifest.setdefault("assets", {})["multiview_image"] = saved
                save()
                emit("multiview_image", asset=saved)
            multiview_id = manifest["tasks"]["multiview_image"]["task_id"]
        if "generation" in manifest["tasks"]:
            generation_body = manifest["tasks"]["generation"]["request"]
        elif args.multiview:
            # files 와 original_task_id 는 상호배타다. 앞 작업 결과를 그대로 넘긴다.
            generation_body = {"type": "multiview_to_model", "original_task_id": multiview_id, **config}
        else:
            token = client._upload_image(source)
            generation_body = {"type": "image_to_model", "file": {"type": source_type, "file_token": token}, **config}
        generated = task("generation", generation_body)
        download("generation", generated, "generated.glb")
        model_id = manifest["tasks"]["generation"]["task_id"]
        checked = task("rig_check", {"type": "animate_prerigcheck", "original_model_task_id": model_id}, 300)
        rig_info = checked.get("output") or {}
        if not rig_info.get("riggable"):
            raise RuntimeError("Generated model failed the rig check; static fallback is not a successful animation trial")
        rig_type = rig_info.get("rig_type") or "biped"
        if rig_type != "biped":
            raise RuntimeError(f"Expected biped for the sit preset; got {rig_type}")
        rigged = task("rig", {"type": "animate_rig", "original_model_task_id": model_id,
                             "model_version": "v1.0-20240301", "rig_type": "biped",
                             "spec": "tripo", "out_format": "glb"})
        download("rig", rigged, "rigged.glb")
        rig_id = manifest["tasks"]["rig"]["task_id"]
        animated = task("animation", {"type": "animate_retarget", "original_model_task_id": rig_id,
                                      "animation": "preset:sit", "out_format": "glb", "bake_animation": True})
        download("animation", animated, "animated.glb")
        summary = manifest["assets"]["animation"]
        if not (summary["skins"] and summary["weighted_primitives"] and
                any(a["varying_channels"] for a in summary["animations"])):
            raise RuntimeError("Output has no weighted skeleton or time-varying animation")
        manifest["status"] = "animation_data_verified"
        emit("complete", status=manifest["status"])
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        emit("failed", error=str(exc))
        return 1
    finally:
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        manifest["credits_consumed"] = sum((t.get("response") or {}).get("consumed_credit", 0) or 0
                                            for t in manifest["tasks"].values())
        try:
            manifest["balance_after"] = client._get(API_BASE + "/user/balance").get("data")
        except Exception:
            pass
        save()
        emit("billing", credits_consumed=manifest["credits_consumed"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
