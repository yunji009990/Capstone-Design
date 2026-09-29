"""사진에서 머리만 잘라 T포즈·리깅 없이 image_to_model 로 보낸다.

왜 따로 만드나
───────────────
지금 운영 경로는 사진 → generate_image(t_pose) → image_to_model 이다. 그런데
generate_image 는 1024 전신을 내놓고, 프레임 안 전신 비율이 고정이라 입력 사진의
얼굴이 40x55 든 268x391 든 **출력 얼굴은 언제나 66~78 x 90~106px** 이다
(2026-09-26, 실험 11건 전수). 즉 사진 화질을 올려도 Tripo 가 보는 얼굴은 70px 이고,
T포즈를 거치는 한 우회로가 없다.

게다가 generate_image 는 생성 모델이라 사람을 다시 그린다. 신원 손실이 거기서
시작될 수 있는데, 그 다음 단계는 이미 바뀐 얼굴을 보고 3D 를 만든다.

T포즈가 필요했던 유일한 이유는 자동 리깅이다(팔 내린 사진은 animate_prerigcheck 가
거부한다 — 운영 실패 8건 중 6건). 몸을 미리 리깅해 두고 머리만 갈아 끼운다면 T포즈가
필요 없고, 그러면 원본 사진이 image_to_model 로 직행한다.

이 스크립트가 재는 것
─────────────────────
「머리만 생성하면 얼굴이 실제로 좋아지는가」 하나다. 몸에 붙이는 일(목 이음새·피부톤·
본 연결)은 별개 문제고, 이 답이 아니면 할 필요가 없다. 그래서 여기서는 붙이지 않는다.

    python tools/head_only_trial.py --image <사진> --out tools/_work/head01 --dry-run
    python tools/head_only_trial.py --image <사진> --out tools/_work/head01

크롭은 insightface bbox 에 여백을 붙여 정사각으로 만든다. 머리카락을 살리려면 위쪽
여백이 커야 하고, 나중에 목에서 자르려면 아래쪽에 목이 남아 있어야 한다.

리깅을 하지 않으므로 tripo_trial.py 를 쓸 수 없다. 그쪽은 생성 뒤 반드시
animate_prerigcheck 를 부르고 riggable 이 아니면 예외를 낸다 — 머리만 있는 모델은
biped 가 될 수 없으니 매번 거기서 죽는다.
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
sys.path.insert(0, str(ROOT / "tools"))
from core import head_cutout  # noqa: E402
from core.tripo import API_BASE, TripoClient  # noqa: E402
from tripo_trial import glb_summary, read_key  # noqa: E402


def crop_head(image_path: Path, dest: Path, *, top: float, bottom: float,
              side: float, size: int) -> dict:
    """얼굴 bbox 에 여백을 붙인 정사각 크롭. 모자라는 곳은 가장자리 색으로 채운다."""
    import cv2
    from core import face_paste

    image = face_paste._read(image_path)
    found = face_paste._biggest(face_paste._analyzer(), image)
    if found is None:
        raise SystemExit("사진에서 얼굴을 찾지 못했습니다")
    x1, y1, x2, y2 = found.bbox
    width, height = x2 - x1, y2 - y1
    # 정사각 한 변은 필요한 세로 길이로 정한다. 머리는 세로로 길다.
    span = height * (1 + top + bottom)
    cx = (x1 + x2) / 2
    cy = (y1 - height * top) + span / 2
    half = max(span / 2, width * (0.5 + side))
    left, right = int(cx - half), int(cx + half)
    upper, lower = int(cy - half), int(cy + half)

    pad = [max(0, -upper), max(0, lower - image.shape[0]),
           max(0, -left), max(0, right - image.shape[1])]
    if any(pad):
        image = cv2.copyMakeBorder(image, *pad, cv2.BORDER_REPLICATE)
        upper, lower = upper + pad[0], lower + pad[0]
        left, right = left + pad[2], right + pad[2]
    patch = image[upper:lower, left:right]
    scaled = cv2.resize(patch, (size, size), interpolation=cv2.INTER_LANCZOS4)

    ok, buffer = cv2.imencode(".png", scaled)
    if not ok:
        raise SystemExit("크롭을 인코딩하지 못했습니다")
    dest.parent.mkdir(parents=True, exist_ok=True)
    buffer.tofile(str(dest))
    # 잘라 키운 뒤 얼굴이 몇 px 이 되는지가 이 실험의 핵심 수치다.
    ratio = size / (right - left)
    return {"file": dest.name, "source_face_px": [int(width), int(height)],
            "crop_box": [left, upper, right, lower], "output": [size, size],
            "face_px_after": [int(width * ratio), int(height * ratio)],
            "bytes": dest.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="크롭만 만들고 멈춘다. 크레딧 0 — 먼저 눈으로 확인하세요")
    # 전처리가 두 갈래다. box 는 얼굴 상자에 여백을 붙여 네모로 자르기만 하는 것이고,
    # cutout 은 의미 분할로 옷과 어깨를 지운다. box 로 만든 첫 시험(head01)은 흰 티셔츠가
    # 그대로 조각돼서, 미리 리깅된 몸에 얹으면 흉상이 겹친다. 기본은 cutout 이다.
    parser.add_argument("--prep", choices=("cutout", "box"), default="cutout",
                        help="cutout: 옷·어깨를 지운다 / box: 네모로 자르기만 한다")
    parser.add_argument("--top", type=float, default=0.40, help="얼굴 높이 대비 위쪽 여백 (머리카락)")
    parser.add_argument("--bottom", type=float, default=0.45, help="아래쪽 여백 (턱·목)")
    parser.add_argument("--side", type=float, default=0.15, help="좌우 최소 여백")
    parser.add_argument("--size", type=int, default=1024, help="크롭을 키울 한 변")
    parser.add_argument("--neck", type=float, default=head_cutout.DEFAULT_NECK,
                        help="prep=cutout: 턱 아래로 목을 얼마나 남길지. 얼굴 높이 대비")
    parser.add_argument("--fit", choices=("face", "all", "portrait"), default="portrait",
                        help="prep=cutout: face/all/portrait. portrait 는 가로를 얼굴 기준으로 "
                             "두고 세로만 머리끝까지 늘려 얼굴 픽셀과 머리 길이를 둘 다 지킨다")
    parser.add_argument("--cut-hair", action="store_true",
                        help="prep=cutout: 머리카락도 목 자르는 높이에서 함께 자른다")
    parser.add_argument("--face-limit", default="50000", help="삼각형 상한. auto 면 Tripo 가 정한다")
    parser.add_argument("--geometry-quality", choices=("standard", "detailed"), default="detailed")
    args = parser.parse_args()

    image_path = args.image.resolve(strict=True)
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / "tools" / "_work"):
        parser.error("--out 은 tools/_work 안이어야 합니다 (git 에 올라가지 않는 자리)")
    out.mkdir(parents=True, exist_ok=True)

    head = out / "head.png"
    if args.prep == "cutout":
        try:
            crop = head_cutout.cutout(image_path, head, size=args.size, fit=args.fit,
                                      top=args.top, bottom=args.bottom, side=args.side,
                                      neck=args.neck, keep_hair=not args.cut_hair)
        except head_cutout.CutoutUnavailable as exc:
            raise SystemExit(f"컷아웃 준비물이 없습니다: {exc}\n"
                             "--prep box 로 돌리면 자르기만 합니다(옷은 남습니다).")
        except head_cutout.CutoutFailed as exc:
            raise SystemExit(str(exc))
    else:
        crop = crop_head(image_path, head, top=args.top, bottom=args.bottom,
                         side=args.side, size=args.size)
    print(json.dumps({"stage": "prep", "prep": args.prep, **crop},
                     ensure_ascii=False, indent=2))
    print("\n얼굴 픽셀  원본 {}x{}  ->  전처리 후 {}x{}   (현재 운영 경로는 약 70x100)\n".format(
        crop["source_face_px"][0], crop["source_face_px"][1],
        crop["face_px_after"][0], crop["face_px_after"][1]))

    config = {"model_version": "v3.1-20260211", "texture": True, "pbr": True,
              "texture_quality": "detailed", "geometry_quality": args.geometry_quality,
              "texture_alignment": "original_image", "enable_image_autofix": False,
              "model_seed": 20260909, "texture_seed": 20260909}
    if args.face_limit.strip().lower() != "auto":
        config["face_limit"] = int(args.face_limit)
    # prep 을 identity 에 넣어야 한다. 안 넣으면 전처리를 바꿔 같은 폴더로 다시 돌렸을 때
    # 앞 회차의 task_id 를 그대로 물고 가서, 새 입력으로 만든 줄 알았던 결과가 실은
    # 옛 결과인 일이 생긴다.
    identity = {"mode": "head_only", "prep": args.prep,
                "crop": {k: crop[k] for k in ("crop_box", "output")},
                "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "generation": config}
    if args.prep == "cutout":
        identity["cutout"] = {"fit": args.fit, "neck": args.neck,
                              "keep_hair": not args.cut_hair}
    if args.dry_run:
        print(json.dumps({"request": identity, "estimated_credits": 50,
                          "network_calls": 0, "crop_written": str(head)},
                         ensure_ascii=False, indent=2))
        return 0

    client = TripoClient(read_key())
    manifest_path = out / "trial.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "request": identity, "tasks": {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if manifest["request"] != identity:
        raise SystemExit("이 폴더에 다른 조건의 시험이 있습니다. 새 --out 을 쓰세요")

    def save():
        temporary = manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(manifest_path)

    manifest["status"] = "running"
    manifest.pop("error", None)
    save()
    data = {}
    try:
        record = manifest["tasks"].get("generation")
        if record is None:
            # 유료 제출은 먼저 기록한다. 이 줄 다음에 끊기면 복구는 submitting 을 보고
            # 다시 POST 하지 않는다 — 같은 작업을 두 번 사는 것이 가장 나쁜 실패다.
            record = manifest["tasks"]["generation"] = {"state": "submitting"}
            save()
            token = client._upload_image(head)
            task_id = client._submit({"type": "image_to_model",
                                      "file": {"type": "png", "file_token": token}, **config})
            record.update(state="submitted", task_id=task_id)
            save()
            print(json.dumps({"stage": "generation", "task_id": task_id, "submitted": True}))
        if not record.get("task_id"):
            raise SystemExit("앞선 제출의 결과를 알 수 없습니다. Tripo 대시보드를 확인하세요")

        deadline, previous = time.monotonic() + 900, None
        while time.monotonic() < deadline:
            data = (client.get_task(record["task_id"]).get("data")) or {}
            record["response"] = data
            save()
            current = (data.get("status"), data.get("progress"))
            if current != previous:
                print(json.dumps({"stage": "generation", "status": current[0],
                                  "progress": current[1],
                                  "credits": data.get("consumed_credit")}), flush=True)
                previous = current
            if current[0] == "success":
                break
            if current[0] in {"failed", "banned", "expired", "cancelled", "canceled", "unknown"}:
                raise SystemExit("생성 실패: " + str(data.get("error_msg") or current[0]))
            time.sleep(5)
        else:
            raise SystemExit("대기 시간 초과 · 같은 명령으로 다시 실행하면 이어서 기다립니다")

        output = data.get("output") or {}
        url = output.get("pbr_model") or output.get("model")
        if isinstance(url, dict):
            url = url.get("url")
        if not url:
            raise SystemExit("응답에 모델 주소가 없습니다")
        dest = out / "head.glb"
        if not dest.exists():
            part = dest.with_suffix(".partial")
            client.download_glb(url, part)
            part.replace(dest)
        summary = glb_summary(dest)
        manifest.setdefault("assets", {})["generation"] = summary
        manifest["status"] = "ready"
        save()
        print(json.dumps({"stage": "complete", "asset": summary}, ensure_ascii=False, indent=2))
        return 0
    except SystemExit:
        manifest["status"] = "failed"
        save()
        raise
    finally:
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        manifest["credits_consumed"] = sum(
            (t.get("response") or {}).get("consumed_credit", 0) or 0
            for t in manifest["tasks"].values())
        try:
            manifest["balance_after"] = client._get(API_BASE + "/user/balance").get("data")
        except Exception:
            pass
        save()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
