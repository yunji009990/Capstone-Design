"""증명사진에서 머리카락·얼굴·목만 남기고 옷과 어깨를 지운다.

왜 필요한가
───────────
머리만 따로 생성해 미리 리깅된 몸에 갈아 끼우려면, Tripo 에 넣는 사진에 어깨와
상의가 없어야 한다. 있으면 흉상(bust)이 만들어져서 몸통과 겹치고, 목에서 자를 자리도
옷 주름에 묻힌다. 실제로 첫 시험(head01)에서 흰 티셔츠가 그대로 조각됐다.

기하학적 크롭으로는 안 된다. 어깨는 목과 같은 높이에서 옆으로 뻗으므로 가로줄 하나로
자르면 목까지 같이 날아간다. 옷만 골라 지우려면 의미 분할이 필요하다.

무엇을 쓰나
───────────
MediaPipe 의 selfie multiclass 분할. 여섯 갈래로 나눈다.

    0 배경   1 머리카락   2 몸피부(=목)   3 얼굴피부   4 옷   5 기타(장신구)

남길 것은 1·2·3, 지울 것은 0·4·5 다. 클래스가 이미 우리가 원하는 경계와 같아서
따로 학습하거나 규칙을 짤 필요가 없다.

모델 파일(selfie_multiclass_256x256.tflite, 약 16MB)은 저장소에 넣지 않는다.
HEAD_CUTOUT_MODEL 로 경로를 주거나 tools/_work/models/ 에 두면 된다. 없으면
CutoutUnavailable 을 올린다 — 호출부가 이 단계를 건너뛰고 원래 사진으로 갈 수 있게.

무엇을 고르게 두나
──────────────────
긴 머리는 어디선가 잘린다. 정사각 프레임을 얼굴에 맞추면 얼굴 픽셀이 커지는 대신
머리끝이 잘리고, 전체에 맞추면 머리는 다 들어오지만 얼굴이 작아진다. 어느 쪽이
나은지는 사진마다 달라서 fit 으로 고르게 하고, 결과에 얼굴 픽셀 수를 적어 돌려준다.
그 숫자가 생성 품질의 상한이다(현재 운영 경로는 약 70x100, head01 은 421x553).
"""
from __future__ import annotations

import os
from pathlib import Path

# MediaPipe selfie multiclass 의 클래스 번호.
BACKGROUND, HAIR, BODY_SKIN, FACE_SKIN, CLOTHES, OTHERS = range(6)
KEEP = (HAIR, BODY_SKIN, FACE_SKIN)

DEFAULT_SIZE = 1024
DEFAULT_TOP = 0.45       # 얼굴 높이 대비 위쪽 여백 (머리카락)
DEFAULT_BOTTOM = 0.55    # 아래쪽 여백 (목)
DEFAULT_SIDE = 0.15      # 좌우 최소 여백
DEFAULT_NECK = 0.40      # 턱 아래로 목을 얼마나 남길지. 얼굴 높이 대비
DEFAULT_MAX_RATIO = 2.0  # fit=portrait 에서 세로가 가로의 몇 배까지 길어져도 되는지


class CutoutUnavailable(Exception):
    """mediapipe 나 모델 파일이 없다. 이 단계를 건너뛰어야 한다."""


class CutoutFailed(Exception):
    """얼굴을 못 찾았거나 남길 영역이 없다."""


_segmenter = None


def model_path() -> Path | None:
    """분할 모델 위치. 환경변수가 우선이고, 없으면 실험용 기본 자리를 본다."""
    raw = os.environ.get("HEAD_CUTOUT_MODEL", "").strip().strip('"').strip("'")
    if raw:
        path = Path(raw)
        return path if path.is_file() else None
    root = Path(__file__).resolve().parents[2]
    guess = root / "tools" / "_work" / "models" / "selfie_multiclass_256x256.tflite"
    return guess if guess.is_file() else None


def _analyzer():
    """분할기를 한 번만 올린다. 작업마다 다시 올리면 건당 수 초가 더 든다."""
    global _segmenter
    if _segmenter is None:
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mpp
            from mediapipe.tasks.python import vision
        except ImportError as exc:
            raise CutoutUnavailable(str(exc)) from exc
        path = model_path()
        if path is None:
            raise CutoutUnavailable(
                "분할 모델이 없습니다. HEAD_CUTOUT_MODEL 로 경로를 주거나 "
                "tools/_work/models/selfie_multiclass_256x256.tflite 에 두세요")
        _segmenter = vision.ImageSegmenter.create_from_options(
            vision.ImageSegmenterOptions(
                base_options=mpp.BaseOptions(model_asset_path=str(path)),
                output_category_mask=True))
        _segmenter._mp = mp
    return _segmenter


def _categories(image_bgr):
    """BGR 이미지를 넣어 클래스 번호 맵(H×W, uint8)을 받는다."""
    import cv2
    import numpy as np
    segmenter = _analyzer()
    mp = segmenter._mp
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    result = segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    mask = result.category_mask.numpy_view()
    return np.asarray(mask).reshape(mask.shape[0], mask.shape[1]).astype(np.uint8)


def _largest_blob(mask, seed_xy):
    """얼굴이 들어 있는 덩어리만 남긴다. 거울·액자 같은 잡음을 떼기 위한 것."""
    import cv2
    import numpy as np
    count, labels = cv2.connectedComponents((mask > 0).astype(np.uint8))
    if count <= 2:
        return mask
    x, y = seed_xy
    x = int(np.clip(x, 0, mask.shape[1] - 1))
    y = int(np.clip(y, 0, mask.shape[0] - 1))
    target = labels[y, x]
    if target == 0:
        # 얼굴 중심이 배경으로 잡힌 이상한 경우. 가장 큰 덩어리로 대신한다.
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        target = int(sizes.argmax())
    return np.where(labels == target, mask, 0).astype(np.uint8)


def cutout(photo: Path, dest: Path, *, size: int = DEFAULT_SIZE, fit: str = "face",
           top: float = DEFAULT_TOP, bottom: float = DEFAULT_BOTTOM,
           side: float = DEFAULT_SIDE, neck: float = DEFAULT_NECK,
           keep_hair: bool = True, rgba: bool = False, feather: int = 3,
           max_ratio: float = DEFAULT_MAX_RATIO) -> dict:
    """사진에서 머리카락·얼굴·목만 오려 정사각 이미지로 dest 에 저장한다.

    fit="face" 는 얼굴에 프레임을 맞춰 얼굴 픽셀을 키운다(머리끝이 잘릴 수 있다).
    fit="all" 은 남긴 영역 전체를 담는다(머리는 다 들어오고 얼굴은 작아진다).

    돌려주는 dict 는 기록용이다. 특히 face_px_after 가 생성 품질의 상한이라,
    나중에 결과를 해석하려면 남겨 두어야 한다.
    """
    import cv2
    import numpy as np
    from . import face_paste

    image = face_paste._read(photo)
    found = face_paste._biggest(face_paste._analyzer(), image)
    if found is None:
        raise CutoutFailed("사진에서 얼굴을 찾지 못했습니다")
    x1, y1, x2, y2 = found.bbox
    face_w, face_h = float(x2 - x1), float(y2 - y1)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0

    categories = _categories(image)
    keep = np.isin(categories, KEEP)

    # 턱 아래로 neck 만큼만 목을 남긴다. 그 아래 피부는 어깨·팔이라 지운다.
    # 가로줄 하나로 자르는 게 아니라 피부 클래스에만 적용하므로, 같은 높이의
    # 머리카락은 살아남는다 — 어깨 위로 흘러내린 머리를 지키려는 것이다.
    cut_y = int(y2 + neck * face_h)
    if 0 <= cut_y < keep.shape[0]:
        skin_below = np.zeros_like(keep)
        skin_below[cut_y:, :] = np.isin(categories[cut_y:, :], (BODY_SKIN, FACE_SKIN))
        keep &= ~skin_below
    if not keep_hair:
        hair_below = np.zeros_like(keep)
        hair_below[cut_y:, :] = categories[cut_y:, :] == HAIR
        keep &= ~hair_below

    mask = (keep.astype(np.uint8)) * 255
    # 옷을 지우면 그 자리에 있던 목·머리에 구멍이 남는다. 닫고 매끈하게 만든다.
    radius = max(3, int(face_w * 0.02)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius, radius))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = _largest_blob(mask, (cx, cy))
    if int((mask > 0).sum()) < 100:
        raise CutoutFailed("남길 영역을 찾지 못했습니다")

    blur = max(1, feather) | 1
    soft = cv2.GaussianBlur(mask, (blur, blur), 0).astype(np.float32) / 255.0

    # 프레임을 정한다. 세 갈래인 이유는 얼굴 픽셀과 머리 길이가 맞바꿈이기 때문이다.
    #   face      얼굴 기준 정사각. 얼굴이 가장 크지만 긴 머리는 프레임 밖으로 나간다
    #   all       남긴 영역 전체를 담는 정사각. 머리는 다 들어오고 얼굴이 작아진다
    #   portrait  가로는 얼굴 기준으로 좁게, 세로만 머리 끝까지 늘린다 — 둘 다 지킨다
    ys, xs = np.nonzero(mask)
    span = face_h * (1.0 + top + bottom)
    if fit == "all":
        left, right = int(xs.min()), int(xs.max())
        upper, lower = int(ys.min()), int(ys.max())
        half = max(right - left, lower - upper) / 2.0
        fx, fy = (left + right) / 2.0, (upper + lower) / 2.0
        left, right = int(fx - half), int(fx + half)
        upper, lower = int(fy - half), int(fy + half)
        out_w = out_h = size
    elif fit == "portrait":
        half = max(span / 2.0, face_w * (0.5 + side))
        left, right = int(cx - half), int(cx + half)
        upper = int(y1 - face_h * top)
        lower = int(ys.max()) + int(face_h * 0.05)   # 머리끝이 잘리지 않게 조금 더
        if lower <= upper + 1:
            lower = upper + int(span)
        # 너무 길쭉하면 생성이 흔들릴 수 있어 상한을 둔다. 그 위로는 머리끝을 포기한다.
        tallest = int((right - left) * max_ratio)
        if lower - upper > tallest:
            lower = upper + tallest
        out_w = size
        out_h = int(round(size * (lower - upper) / float(right - left)))
    else:
        half = max(span / 2.0, face_w * (0.5 + side))
        fx, fy = cx, (y1 - face_h * top) + span / 2.0
        left, right = int(fx - half), int(fx + half)
        upper, lower = int(fy - half), int(fy + half)
        out_w = out_h = size

    if rgba:
        canvas = np.dstack([image, (soft * 255).astype(np.uint8)])
        border = (0, 0, 0, 0)
    else:
        white = np.full_like(image, 255)
        canvas = (image.astype(np.float32) * soft[..., None]
                  + white.astype(np.float32) * (1.0 - soft[..., None]))
        canvas = np.clip(canvas, 0, 255).astype(np.uint8)
        border = (255, 255, 255)

    pad = [max(0, -upper), max(0, lower - canvas.shape[0]),
           max(0, -left), max(0, right - canvas.shape[1])]
    if any(pad):
        canvas = cv2.copyMakeBorder(canvas, *pad, cv2.BORDER_CONSTANT, value=border)
        upper, lower = upper + pad[0], lower + pad[0]
        left, right = left + pad[2], right + pad[2]
    patch = canvas[upper:lower, left:right]
    if patch.size == 0:
        raise CutoutFailed("프레임이 이미지 밖으로 나갔습니다")
    scaled = cv2.resize(patch, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = ".png"          # 알파를 쓰든 안 쓰든 무손실로 남긴다
    partial = dest.with_suffix(dest.suffix + ".part")
    ok, buffer = cv2.imencode(suffix, scaled)
    if not ok:
        raise CutoutFailed("결과를 인코딩하지 못했습니다")
    buffer.tofile(str(partial))
    partial.replace(dest)

    ratio = out_w / float(right - left)
    shares = {name: round(float((categories == value).mean()), 4) for name, value in
              (("background", BACKGROUND), ("hair", HAIR), ("neck", BODY_SKIN),
               ("face", FACE_SKIN), ("clothes", CLOTHES), ("others", OTHERS))}
    return {"file": dest.name, "fit": fit, "rgba": rgba,
            "source_face_px": [int(face_w), int(face_h)],
            "face_px_after": [int(face_w * ratio), int(face_h * ratio)],
            "crop_box": [left, upper, right, lower], "output": [out_w, out_h],
            "neck_cut_y": cut_y, "kept_pixels": int((mask > 0).sum()),
            "class_share": shares, "bytes": dest.stat().st_size}
