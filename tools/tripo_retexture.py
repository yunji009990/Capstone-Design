"""Re-texture an existing Tripo model without paying for the geometry again.

왜 이 실험인가
──────────────
face_similarity.py 로 기존 실험 아홉 개를 재 보니, 원본 → T포즈는 0.56~0.80 으로
동일인성이 남는데 T포즈 → 모델에서 0.13~0.25 로 무너진다. 같은 모델을 형상만
렌더하면 -0.05~0.06, 텍스처만 렌더하면 0.18~0.36 이다. 즉 **동일인성을 들고 있는
것은 텍스처뿐이고, 손실은 텍스처를 굽는 단계에서 난다.**

그래서 형상은 그대로 두고 텍스처만 다시 굽는다. Tripo 의 `texture_model` 은
이미 만든 모델의 task ID 를 받아 새 참조 이미지로 텍스처만 생성한다. 생성을
처음부터 다시 하지 않으므로 형상 비용이 들지 않고, 같은 형상 위에서 참조
이미지만 바꿔가며 A/B 할 수 있다.

쓰는 법
───────
    # 요청과 비용을 먼저 확인한다. 키도 네트워크도 필요 없다
    python tools/tripo_retexture.py --trial tools/_work/tripo_trial_20260918_fullbody --dry-run

    # T포즈 이미지로 다시 굽는다 (기본)
    python tools/tripo_retexture.py --trial tools/_work/tripo_trial_20260918_fullbody

    # 얼굴 크롭을 더 얹어 굽는다. --image 는 여러 번 쓸 수 있다
    python tools/tripo_retexture.py --trial tools/_work/tripo_trial_20260918_fullbody \
        --image tools/_work/crops/face.png --variant facecrop

    # 결과를 재서 원래 것과 비교한다
    python tools/faceshot/server.py     # 다른 창에서 띄우고 브라우저로 촬영
    tools/_work/venv-face/Scripts/python.exe tools/face_similarity.py \
        --trial tools/_work/tripo_trial_20260918_fullbody --shots fb_retex

중단과 재시작
─────────────
유료 제출은 POST 전에 `submitting` 으로 먼저 적고, task ID 를 받은 뒤 다시 적는다.
제출 도중 끊기면 결과를 알 수 없으므로 자동으로 다시 보내지 않고 멈춘다. 이때는
Tripo 작업 이력에서 그 제출이 살았는지 확인한 뒤 판단한다. 같은 --variant 로 다시
실행하면 저장된 task ID 의 폴링부터 이어간다.

비용
────
`texture_quality=detailed` 가 10 크레딧을 더한다는 것만 문서에 적혀 있고 기본
요금은 적혀 있지 않다. 그래서 추정치를 지어내지 않고, 실행 전후의 잔액과 응답의
consumed_credit 을 기록해 실제 사용량을 남긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Survey"))
from core.tripo import API_BASE, TripoClient, file_type  # noqa: E402

from tripo_trial import glb_summary, read_key  # noqa: E402

# texture_model 은 원본 작업이 Turbo-v1.0-20250506 이상이거나 v2.0-20240919 이상이어야
# 한다. 우리 생성은 v3.1-20260211 이라 조건을 넘는다.
TEXTURE_MODEL_VERSION = "v3.0-20250812"


def emit(stage: str, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=True), flush=True)


def save_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def shown(path: Path) -> str:
    """기록에 남길 경로. 저장소 안이면 상대 경로로, 밖이면 그대로 적는다."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def source_images(directory: Path, args) -> list[Path]:
    """텍스처 참조로 보낼 이미지들. 첫 장이 주 참조다."""
    if args.image:
        chosen = [path.resolve() for path in args.image]
    else:
        name = {"tpose": "reference_tpose.png", "original": "reference.png"}[args.source]
        chosen = [directory / name]
    for path in chosen:
        if not path.is_file():
            raise SystemExit(f"참조 이미지가 없습니다: {path}")
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise SystemExit(f"PNG 나 JPG 만 보냅니다: {path}")
    return chosen


def build_request(model_task_id: str, images: list[Path], args) -> dict:
    """texture_prompt 는 text / image / images 가 상호배타다. 여러 장이면 images 를 쓴다."""
    if args.text:
        prompt = {"text": args.text}
    else:
        files = [{"type": file_type(path), "file_token": None} for path in images]
        prompt = {"images": files} if len(files) > 1 else {"image": files[0]}
    body = {
        "type": "texture_model",
        "original_model_task_id": model_task_id,
        "model_version": args.model_version,
        "texture_prompt": prompt,
        "texture": True,
        "pbr": not args.no_pbr,
        "texture_quality": args.texture_quality,
        "texture_alignment": args.texture_alignment,
        "bake": True,
    }
    if args.texture_seed is not None:
        body["texture_seed"] = args.texture_seed
    return body


def fill_tokens(client: TripoClient, body: dict, images: list[Path]) -> dict:
    """dry-run 에서는 업로드하지 않으므로, 실제 실행 때 토큰만 채워 넣는다."""
    prompt = body["texture_prompt"]
    files = prompt["images"] if "images" in prompt else [prompt["image"]]
    for entry, path in zip(files, images):
        entry["file_token"] = client._upload_image(path)
    return body


def poll(client: TripoClient, record: dict, journal: Path, manifest: dict, timeout=1800) -> dict:
    deadline, failures, previous = time.monotonic() + timeout, 0, None
    while time.monotonic() < deadline:
        try:
            response = client.get_task(record["task_id"])
            failures = 0
        except Exception as exc:
            transient = isinstance(exc, OSError) or any(
                f"[{code}]" in str(exc) for code in (429, 500, 502, 503, 504))
            if not transient or failures >= 3:
                raise
            failures += 1
            emit("texture", status="poll_retry", attempt=failures)
            time.sleep(5)
            continue
        if response.get("code", 0) != 0:
            raise RuntimeError(f"작업 조회가 code {response.get('code')} 로 실패했습니다")
        data = response.get("data") or {}
        record["response"] = data
        save_json(journal, manifest)
        current = (data.get("status"), data.get("progress"))
        if current != previous:
            emit("texture", status=current[0], progress=current[1],
                 credits=data.get("consumed_credit"))
            previous = current
        if current[0] == "success":
            return data
        if current[0] in {"failed", "banned", "expired", "cancelled", "canceled", "unknown"}:
            raise RuntimeError(f"texture_model 이 {current[0]} 로 끝났습니다: "
                               f"{data.get('error_msg') or data.get('error') or ''}")
        time.sleep(5)
    raise TimeoutError("대기 시간 초과 · 같은 --variant 로 다시 실행하면 같은 작업을 이어서 폴링합니다")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", type=Path, required=True, help="생성이 끝난 실험 폴더")
    parser.add_argument("--source", choices=("tpose", "original"), default="tpose",
                        help="참조로 쓸 폴더 안 이미지. 기본은 T포즈")
    parser.add_argument("--image", type=Path, action="append",
                        help="참조 이미지를 직접 지정. 여러 번 쓰면 여러 장으로 보낸다")
    parser.add_argument("--text", help="이미지 대신 글로 지시 (이미지와 함께 못 쓴다)")
    parser.add_argument("--variant", help="결과 이름. 여러 시도를 같은 폴더에 남기려고 쓴다")
    parser.add_argument("--texture-quality", choices=("standard", "detailed"), default="detailed")
    parser.add_argument("--texture-alignment", choices=("original_image", "geometry"),
                        default="original_image")
    parser.add_argument("--texture-seed", type=int, default=20260909,
                        help="고정하면 같은 참조에서 같은 텍스처가 나온다")
    parser.add_argument("--model-version", default=TEXTURE_MODEL_VERSION)
    parser.add_argument("--no-pbr", action="store_true")
    parser.add_argument("--check-rig", action="store_true",
                        help="끝난 뒤 animate_prerigcheck 로 이 결과에서 리깅을 이어갈 수 있는지 본다 (0 크레딧)")
    parser.add_argument("--dry-run", action="store_true", help="요청만 출력. 키도 네트워크도 쓰지 않는다")
    args = parser.parse_args()

    directory = args.trial.resolve()
    if not directory.is_relative_to(ROOT / "tools" / "_work"):
        parser.error("--trial 은 tools/_work 안이어야 합니다 (git 에 올라가지 않는 실험 산출물)")
    trial_path = directory / "trial.json"
    if not trial_path.is_file():
        parser.error(f"trial.json 이 없습니다: {directory}")
    trial = json.loads(trial_path.read_text(encoding="utf-8"))
    generation = (trial.get("tasks") or {}).get("generation") or {}
    model_task_id = generation.get("task_id")
    if not model_task_id:
        parser.error("이 폴더에는 완료된 generation 작업이 없습니다. 먼저 tripo_trial.py 로 생성하세요")

    # 문서상 text 와 참조 이미지는 상호배타다. 둘 다 오면 무엇을 보냈는지 불분명해진다.
    if args.text and args.image:
        parser.error("--text 와 --image 는 함께 쓸 수 없습니다. 하나만 고르세요")
    images = [] if args.text else source_images(directory, args)
    variant = args.variant or ("text" if args.text else "custom" if args.image else args.source)
    body = build_request(model_task_id, images, args)

    if args.dry_run:
        print(json.dumps({
            "trial": directory.name,
            "original_model_task_id": model_task_id,
            "variant": variant,
            "reference_images": [shown(p) for p in images],
            "request": body,
            "credits": {
                "note": "기본 요금은 문서에 없다. detailed 는 +10. 실제 사용량은 실행 후 "
                        "consumed_credit 과 잔액 차이로 기록한다",
                "texture_quality_surcharge": 10 if args.texture_quality == "detailed" else 0,
            },
            "network_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    client = TripoClient(read_key())
    journal = directory / f"retexture_{variant}.json"
    dest = directory / f"retexture_{variant}.glb"
    manifest = json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else {
        "original_model_task_id": model_task_id,
        "variant": variant,
        "reference_images": [{"file": shown(p),
                              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in images],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "task": None,
    }
    if manifest["original_model_task_id"] != model_task_id:
        raise SystemExit("이 기록은 다른 모델의 재텍스처입니다. 다른 --variant 를 쓰세요")

    if dest.is_file() and (manifest.get("asset") or {}).get("sha256"):
        if hashlib.sha256(dest.read_bytes()).hexdigest() != manifest["asset"]["sha256"]:
            raise SystemExit(f"{dest.name} 이 기록과 다릅니다. 확인 후 지우고 다시 실행하세요")
        emit("texture", status="cached", file=dest.name)
        return 0

    try:
        manifest["balance_before"] = client._get(API_BASE + "/user/balance").get("data")
    except Exception:
        pass

    record = manifest.get("task")
    if record is None:
        record = manifest["task"] = {"request": body, "state": "submitting"}
        save_json(journal, manifest)
        # 제출이 중간에 끊기면 결과를 알 수 없다. 재시작해도 다시 보내지 않는다.
        record["task_id"] = client._submit(fill_tokens(client, body, images))
        record["state"] = "submitted"
        save_json(journal, manifest)
        emit("texture", status="submitted", task_id=record["task_id"])
    if not record.get("task_id"):
        raise SystemExit("제출 결과를 알 수 없습니다. Tripo 작업 이력에서 확인한 뒤 판단하세요 "
                         "— 자동으로 다시 보내지 않습니다")

    data = poll(client, record, journal, manifest)

    output = data.get("output") or {}
    url = output.get("pbr_model") or output.get("model")
    if isinstance(url, dict):
        url = url.get("url")
    if not url:
        raise SystemExit("응답에 모델 주소가 없습니다")
    partial = dest.with_suffix(".partial")
    client.download_glb(url, partial)
    summary = glb_summary(partial)
    partial.replace(dest)
    summary["file"] = dest.name
    manifest["asset"] = {**summary, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                         "bytes": dest.stat().st_size}
    record["state"] = "downloaded"
    save_json(journal, manifest)
    emit("texture", status="downloaded", asset=manifest["asset"])

    if args.check_rig:
        # 재텍스처 결과에서 리깅을 이어갈 수 있는지는 문서에 없다. prerigcheck 는 0 크레딧이라
        # 물어보는 비용이 없다. 안 되면 리깅 전에 텍스처를 확정하는 순서로 바꿔야 한다.
        check = manifest.get("rig_check")
        if check is None:
            check = manifest["rig_check"] = {"state": "submitting"}
            save_json(journal, manifest)
            check["task_id"] = client._submit({"type": "animate_prerigcheck",
                                               "original_model_task_id": record["task_id"]})
            check["state"] = "submitted"
            save_json(journal, manifest)
        result = poll(client, check, journal, manifest, timeout=600)
        riggable = bool((result.get("output") or {}).get("riggable"))
        manifest["rig_check"]["riggable"] = riggable
        save_json(journal, manifest)
        emit("rig_check", riggable=riggable, rig_type=(result.get("output") or {}).get("rig_type"))

    try:
        manifest["balance_after"] = client._get(API_BASE + "/user/balance").get("data")
    except Exception:
        pass
    manifest["credits_consumed"] = sum(
        (step.get("response") or {}).get("consumed_credit", 0) or 0
        for step in (manifest.get("task"), manifest.get("rig_check")) if step)
    save_json(journal, manifest)
    emit("billing", credits_consumed=manifest["credits_consumed"],
         balance_before=manifest.get("balance_before"), balance_after=manifest.get("balance_after"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
