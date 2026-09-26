"""T포즈 이미지를 키우고 그 얼굴 자리에 원본 사진의 얼굴을 옮겨 붙인다.

왜 필요한가
───────────
generate_image(t_pose) 는 1024×1024 전신을 내놓는다. 프레임 안에서 얼굴이 차지하는
비율이 고정이라, 원본 얼굴이 40×55 든 268×391 든 결과는 언제나 66~78 × 90~106px 다
(2026-09-26, 실험 11건 전수). image_to_model 은 그 작은 얼굴을 보고 4096 아틀라스의
860×860 영역에 텍스처를 굽는다. 9배 업스케일이라 뭉갠다.

T포즈가 필요한 이유는 리깅이다 — 팔을 내린 사진으로 만든 모델은 자동 리깅이 거부한다
(운영 실패 6건이 전부 그것이었다). 그래서 자세는 T포즈에서 가져오되 얼굴 픽셀만
원본에서 끌어온다. 이목구비만 바꾸고 머리카락·귀·턱선 바깥은 건드리지 않는다.

효과의 한계 (2026-09-26 측정)
─────────────────────────────
사진 3장에서 1장만 뚜렷하게 좋아졌다.

    사진 A  0.198 → 0.351   (+0.153)
    사진 B  0.106 → 0.063   (-0.042)
    사진 C  0.269 → 0.293   (+0.023)   노이즈 바닥 0.045

B 는 T포즈에 이마를 가로지르는 머리카락이 있어 그것까지 입체로 조각됐다. 즉 이 단계는
**보장이 아니라 확률을 올리는 시도**다. 실패해도 파이프라인이 멈추면 안 되므로,
호출부는 예외를 잡아 원래 T포즈로 계속 가야 한다.

Scene_2 의 환경광을 올리는 편이 이보다 크게 듣는다(+0.084, 크레딧 0). 생성 쪽만
손대서는 한계가 있다는 뜻이다.
"""
from __future__ import annotations

from pathlib import Path

# 얼굴 5점(양눈·코·입꼬리)으로 닮음변환을 구한다. 표정·각도 차이는 못 고치므로
# 정면 사진일수록 잘 맞는다.
DEFAULT_SCALE = 3.0
DEFAULT_FEATHER = 0.22


class TransplantUnavailable(Exception):
    """얼굴 검출 라이브러리가 없다. 설치 전에는 이 단계를 건너뛰어야 한다."""


class TransplantFailed(Exception):
    """얼굴을 못 찾았거나 맞추지 못했다. 원래 T포즈로 계속 간다."""


_app = None


def _analyzer(det_size: int = 640):
    """모델을 한 번만 올린다. 작업마다 다시 올리면 건당 수 초가 더 든다."""
    global _app
    if _app is None:
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise TransplantUnavailable(str(exc)) from exc
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(det_size, det_size))
        _app = app
    return _app


def _read(path: Path):
    import cv2
    import numpy as np
    blob = np.fromfile(str(path), dtype=np.uint8)   # 한글 경로에서 imread 가 실패한다
    image = cv2.imdecode(blob, cv2.IMREAD_COLOR)
    if image is None:
        raise TransplantFailed(f"이미지를 읽지 못했습니다: {path.name}")
    return image


def _biggest(app, image):
    faces = app.get(image)
    if not faces:
        return None
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def _match_colour(source, target, mask):
    """source 의 색 분포를 target 에 맞춘다. LAB 각 채널의 평균·표준편차를 옮긴다.

    맞추지 않으면 목과 얼굴의 피부색이 갈라지고 Tripo 가 그 경계를 텍스처에 그대로 굽는다."""
    import cv2
    import numpy as np
    src = cv2.cvtColor(source, cv2.COLOR_BGR2LAB).astype(np.float32)
    dst = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
    area = mask > 0
    if area.sum() < 100:
        return source
    for channel in range(3):
        s_mean, s_std = src[..., channel][area].mean(), src[..., channel][area].std() + 1e-6
        d_mean, d_std = dst[..., channel][area].mean(), dst[..., channel][area].std() + 1e-6
        src[..., channel] = (src[..., channel] - s_mean) * (d_std / s_std) + d_mean
    return cv2.cvtColor(np.clip(src, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def transplant(body: Path, face: Path, dest: Path, *, scale: float = DEFAULT_SCALE,
               feather: float = DEFAULT_FEATHER, colour_match: bool = True) -> dict:
    """T포즈(body)를 scale 배로 키우고 원본 사진(face)의 얼굴을 얹어 dest 로 저장한다.

    돌려주는 dict 는 기록용이다 — 어떤 크기로 무엇을 했는지 남겨야 나중에 결과를 해석한다.
    실패하면 TransplantFailed 를 올린다. 호출부는 그것을 잡아 원래 T포즈로 계속 간다.
    """
    import cv2
    import numpy as np
    from skimage.transform import SimilarityTransform

    app = _analyzer()
    body_image, face_image = _read(body), _read(face)

    big = cv2.resize(body_image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    body_face = _biggest(app, big)
    if body_face is None:
        raise TransplantFailed("T포즈 이미지에서 얼굴을 찾지 못했습니다")
    source_face = _biggest(app, face_image)
    if source_face is None:
        raise TransplantFailed("원본 사진에서 얼굴을 찾지 못했습니다")

    transform = SimilarityTransform()
    if not transform.estimate(source_face.kps, body_face.kps):
        raise TransplantFailed("두 얼굴을 맞출 변환을 구하지 못했습니다")
    height, width = big.shape[:2]
    warped = cv2.warpAffine(face_image, transform.params[:2], (width, height),
                            flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)

    # 이목구비만 덮는 타원. 머리카락·귀·턱선 바깥은 T포즈 것을 남겨야 자세가 안 흐트러진다.
    x1, y1, x2, y2 = body_face.bbox
    centre = (int((x1 + x2) / 2), int((y1 + y2) / 2))
    radii = (int((x2 - x1) * 0.62), int((y2 - y1) * 0.72))
    mask = np.zeros((height, width), np.uint8)
    cv2.ellipse(mask, centre, radii, 0, 0, 360, 255, -1)
    blur = int(max(9, (y2 - y1) * feather)) | 1
    soft = cv2.GaussianBlur(mask, (blur, blur), 0)

    if colour_match:
        warped = _match_colour(warped, big, mask)

    alpha = (soft.astype(np.float32) / 255.0)[..., None]
    merged = warped.astype(np.float32) * alpha + big.astype(np.float32) * (1 - alpha)
    result = np.clip(merged, 0, 255).astype(np.uint8)

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    ok, buffer = cv2.imencode(dest.suffix or ".png", result)
    if not ok:
        raise TransplantFailed("결과를 인코딩하지 못했습니다")
    buffer.tofile(str(partial))
    partial.replace(dest)

    def size(found):
        return [int(found.bbox[2] - found.bbox[0]), int(found.bbox[3] - found.bbox[1])]

    return {"file": dest.name, "scale": scale, "canvas": [width, height],
            "source_face_px": size(source_face), "tpose_face_px": size(body_face),
            "bytes": dest.stat().st_size}
