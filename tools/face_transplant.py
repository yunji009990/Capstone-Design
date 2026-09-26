"""T포즈 이미지를 키우고 그 얼굴 자리에 원본 사진의 얼굴을 옮겨 붙인다.

왜 필요한가
───────────
Tripo 의 generate_image(t_pose) 는 1024×1024 전신을 내놓는다. 프레임 안에서 얼굴이
차지하는 비율이 고정이라, 원본 얼굴이 40×55 든 268×391 이든 결과는 언제나
66~78 × 90~106px 다(2026-09-26, 실험 11건 전수). image_to_model 은 그 작은 얼굴을
보고 4096 아틀라스의 860×860 영역에 텍스처를 굽는다. 9배 업스케일이라 뭉갠다.

T포즈가 필요한 이유는 리깅이다 — 팔을 내린 사진으로 만든 모델은 자동 리깅이
거부하거나(운영 실패 6건) 손에 옷이 딸려 올라간다. 그래서 자세는 T포즈에서 가져오되,
얼굴 픽셀만 원본에서 끌어온다.

    T포즈 1024² (얼굴 66×94)
      → scale 배로 확대
      → 머리 자리에 원본 얼굴을 맞춰 붙임
      → 얼굴이 훨씬 큰 한 장

Tripo 는 JPEG/PNG 를 20MB 까지 받고 해상도 상한을 문서에 적어 두지 않았다.

어떻게 맞추나
─────────────
두 사진에서 얼굴 5점(양눈·코·입꼬리)을 찾아, 원본 → T포즈 로 가는 닮음변환
(크기·회전·이동)을 구해 원본을 얹는다. 경계가 보이지 않게 타원 마스크를 흐리고,
붙이기 전에 원본의 색을 T포즈 얼굴의 밝기·색조에 맞춘다. 맞추지 않으면 목과 얼굴의
피부색이 갈라져 Tripo 가 그 경계를 그대로 텍스처에 굽는다.

쓰는 법
───────
    tools/_work/venv-face/Scripts/python.exe tools/face_transplant.py \
        --body  tools/_work/<세션>/tpose.png \
        --face  tools/_work/<세션>/front.jpg \
        --out   tools/_work/<세션>/tpose_hires.png

의존성은 face_similarity.py 와 같은 venv 를 쓴다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    import cv2
    import numpy as np
    blob = np.fromfile(str(path), dtype=np.uint8)   # 한글 경로에서 imread 가 실패한다
    image = cv2.imdecode(blob, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"이미지를 읽지 못했습니다: {path}")
    return image


def biggest_face(app, image):
    faces = app.get(image)
    if not faces:
        return None
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def match_colour(source, target, mask):
    """source 의 색 분포를 target 에 맞춘다. LAB 각 채널의 평균·표준편차를 옮긴다."""
    import cv2
    import numpy as np
    src = cv2.cvtColor(source, cv2.COLOR_BGR2LAB).astype(np.float32)
    dst = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
    area = mask > 0
    if area.sum() < 100:
        return source
    for c in range(3):
        s_mean, s_std = src[..., c][area].mean(), src[..., c][area].std() + 1e-6
        d_mean, d_std = dst[..., c][area].mean(), dst[..., c][area].std() + 1e-6
        src[..., c] = (src[..., c] - s_mean) * (d_std / s_std) + d_mean
    return cv2.cvtColor(np.clip(src, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--body", type=Path, required=True, help="T포즈 이미지 (자세를 가져올 쪽)")
    parser.add_argument("--face", type=Path, required=True, help="원본 사진 (얼굴을 가져올 쪽)")
    parser.add_argument("--out", type=Path, required=True, help="결과 PNG")
    parser.add_argument("--scale", type=float, default=3.0, help="T포즈를 몇 배로 키울지")
    parser.add_argument("--feather", type=float, default=0.22,
                        help="경계를 흐리는 폭. 얼굴 높이 대비 비율")
    parser.add_argument("--no-colour-match", action="store_true",
                        help="색 맞추기를 끈다. 원본 피부색을 그대로 쓰고 싶을 때")
    args = parser.parse_args()

    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
        from skimage.transform import SimilarityTransform
    except ImportError as exc:
        raise SystemExit(f"의존성이 없습니다 ({exc.name}). face_similarity.py 상단의 venv 를 쓰세요.")

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))

    body = load(args.body)
    face_img = load(args.face)

    big = cv2.resize(body, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_LANCZOS4)
    body_face = biggest_face(app, big)
    if body_face is None:
        raise SystemExit("T포즈 이미지에서 얼굴을 찾지 못했습니다")
    src_face = biggest_face(app, face_img)
    if src_face is None:
        raise SystemExit("원본 사진에서 얼굴을 찾지 못했습니다")

    # 5점으로 닮음변환을 구한다. 표정·각도 차이는 못 고치므로 정면 사진일수록 잘 맞는다.
    transform = SimilarityTransform()
    if not transform.estimate(src_face.kps, body_face.kps):
        raise SystemExit("두 얼굴을 맞출 변환을 구하지 못했습니다")
    matrix = transform.params[:2]
    height, width = big.shape[:2]
    warped = cv2.warpAffine(face_img, matrix, (width, height),
                            flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)

    # 이목구비만 덮는 타원. 머리카락·귀·턱선 바깥은 T포즈 것을 남겨야 자세가 안 흐트러진다.
    x1, y1, x2, y2 = body_face.bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    rx, ry = (x2 - x1) * 0.62, (y2 - y1) * 0.72
    mask = np.zeros((height, width), np.uint8)
    cv2.ellipse(mask, (int(cx), int(cy)), (int(rx), int(ry)), 0, 0, 360, 255, -1)
    blur = int(max(9, (y2 - y1) * args.feather)) | 1
    soft = cv2.GaussianBlur(mask, (blur, blur), 0)

    if not args.no_colour_match:
        warped = match_colour(warped, big, mask)

    alpha = (soft.astype(np.float32) / 255.0)[..., None]
    merged = (warped.astype(np.float32) * alpha + big.astype(np.float32) * (1 - alpha))
    result = np.clip(merged, 0, 255).astype(np.uint8)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(args.out.suffix or ".png", result)
    if not ok:
        raise SystemExit("결과를 인코딩하지 못했습니다")
    buffer.tofile(str(args.out))

    after = biggest_face(app, result)
    def size(face):
        return f"{int(face.bbox[2] - face.bbox[0])}x{int(face.bbox[3] - face.bbox[1])}"
    print(f"원본 사진   {face_img.shape[1]}x{face_img.shape[0]}  얼굴 {size(src_face)}")
    print(f"T포즈 확대  {width}x{height}  얼굴 {size(body_face)}")
    print(f"결과        {args.out}  ({args.out.stat().st_size // 1024} KB)"
          + (f"  얼굴 {size(after)}" if after else "  ← 얼굴 검출 실패, 확인 필요"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
