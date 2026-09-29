"""증명사진에서 머리카락·얼굴·목만 오려낸다 (명령줄).

구현은 Survey/core/head_cutout.py 에 있다. 운영 워커가 같은 코드를 쓰게 될 자리라
여기에 따로 두지 않는다 — 실험에서 쓰던 것과 서버가 쓰는 것이 갈리면 결과를 비교할 수 없다.

왜 필요한지는 그 파일의 머리말에 적어 두었다. 요약하면, 머리만 생성해 미리 리깅된
몸에 갈아 끼우려면 입력 사진에 어깨와 상의가 없어야 한다. 어깨는 목과 같은 높이에서
옆으로 뻗으므로 가로줄 하나로 자를 수 없고, 옷만 골라 지우려면 의미 분할이 필요하다.

    tools/_work/venv-face/Scripts/python.exe tools/head_cutout.py \
        --image tools/_work/head01/source.jpg \
        --out   tools/_work/head01/cutout.png

fit 을 바꿔 두 장을 만들어 비교하는 것을 권한다. 얼굴 픽셀과 머리끝 사이의
맞바꿈이라 사진마다 답이 다르다.

    --fit face   얼굴에 프레임을 맞춘다. 얼굴이 커지고 머리끝이 잘린다 (기본)
    --fit all    남긴 영역 전체를 담는다. 머리는 다 들어오고 얼굴이 작아진다

준비물은 mediapipe 와 분할 모델이다. face_similarity.py 와 같은 venv 를 쓴다.

    tools/_work/venv-face/Scripts/python.exe -m pip install mediapipe
    curl -sSL -o tools/_work/models/selfie_multiclass_256x256.tflite \
      https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Survey"))
from core import head_cutout  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, required=True, help="증명사진")
    parser.add_argument("--out", type=Path, required=True, help="결과 PNG")
    parser.add_argument("--fit", choices=("face", "all", "portrait"), default="portrait",
                        help="face: 얼굴 기준 정사각 / all: 전체 정사각 / "
                             "portrait: 가로는 얼굴 기준, 세로만 머리끝까지 (기본)")
    parser.add_argument("--size", type=int, default=head_cutout.DEFAULT_SIZE,
                        help="결과 한 변")
    parser.add_argument("--neck", type=float, default=head_cutout.DEFAULT_NECK,
                        help="턱 아래로 목을 얼마나 남길지. 얼굴 높이 대비")
    parser.add_argument("--top", type=float, default=head_cutout.DEFAULT_TOP,
                        help="위쪽 여백 (fit=face 일 때만)")
    parser.add_argument("--bottom", type=float, default=head_cutout.DEFAULT_BOTTOM,
                        help="아래쪽 여백 (fit=face 일 때만)")
    parser.add_argument("--side", type=float, default=head_cutout.DEFAULT_SIDE,
                        help="좌우 최소 여백 (fit=face 일 때만)")
    parser.add_argument("--cut-hair", action="store_true",
                        help="머리카락도 목 자르는 높이에서 함께 자른다")
    parser.add_argument("--rgba", action="store_true",
                        help="배경을 흰색 대신 투명으로 남긴다")
    parser.add_argument("--feather", type=int, default=3, help="가장자리를 흐리는 폭(px)")
    args = parser.parse_args()

    try:
        info = head_cutout.cutout(args.image, args.out, size=args.size, fit=args.fit,
                                  top=args.top, bottom=args.bottom, side=args.side,
                                  neck=args.neck, keep_hair=not args.cut_hair,
                                  rgba=args.rgba, feather=args.feather)
    except head_cutout.CutoutUnavailable as exc:
        raise SystemExit(f"준비물이 없습니다: {exc}")
    except head_cutout.CutoutFailed as exc:
        raise SystemExit(str(exc))

    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("\n얼굴 픽셀  원본 {}x{}  ->  결과 {}x{}   (현재 운영 경로는 약 70x100)".format(
        info["source_face_px"][0], info["source_face_px"][1],
        info["face_px_after"][0], info["face_px_after"][1]))
    share = info["class_share"]
    print("지운 비율  옷 {:.1%} · 배경 {:.1%}   남긴 비율  머리 {:.1%} · 얼굴 {:.1%} · 목 {:.1%}"
          .format(share["clothes"], share["background"],
                  share["hair"], share["face"], share["neck"]))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
