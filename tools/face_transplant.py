"""T포즈 이미지에 원본 사진의 얼굴을 옮겨 붙인다 (명령줄).

구현은 Survey/core/face_paste.py 에 있다. 운영 워커가 같은 코드를 쓰므로 여기에
따로 두지 않는다 — 실험에서 쓰던 것과 서버가 쓰는 것이 갈리면 결과를 비교할 수 없다.

왜 필요한지, 효과가 어디까지인지는 그 파일의 머리말에 적어 두었다. 요약하면
generate_image(t_pose) 출력이 1024 전신 고정이라 얼굴이 언제나 70×100px 언저리로
나오고, Tripo 는 그 작은 얼굴을 4096 아틀라스에 9배로 늘려 굽는다. 자세는 T포즈에서
가져오되 얼굴 픽셀만 원본에서 끌어온다.

    tools/_work/venv-face/Scripts/python.exe tools/face_transplant.py \
        --body tools/_work/<세션>/tpose.png \
        --face tools/_work/<세션>/front.jpg \
        --out  tools/_work/<세션>/tpose_hires.png

의존성은 face_similarity.py 와 같은 venv 를 쓴다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Survey"))
from core import face_paste  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--body", type=Path, required=True, help="T포즈 이미지 (자세를 가져올 쪽)")
    parser.add_argument("--face", type=Path, required=True, help="원본 사진 (얼굴을 가져올 쪽)")
    parser.add_argument("--out", type=Path, required=True, help="결과 PNG")
    parser.add_argument("--scale", type=float, default=face_paste.DEFAULT_SCALE,
                        help="T포즈를 몇 배로 키울지")
    parser.add_argument("--feather", type=float, default=face_paste.DEFAULT_FEATHER,
                        help="경계를 흐리는 폭. 얼굴 높이 대비 비율")
    parser.add_argument("--no-colour-match", action="store_true",
                        help="색 맞추기를 끈다. 원본 피부색을 그대로 쓰고 싶을 때")
    args = parser.parse_args()

    try:
        info = face_paste.transplant(args.body, args.face, args.out, scale=args.scale,
                                     feather=args.feather,
                                     colour_match=not args.no_colour_match)
    except face_paste.TransplantUnavailable as exc:
        raise SystemExit(f"의존성이 없습니다 ({exc}). face_similarity.py 상단의 venv 를 쓰세요.")
    except face_paste.TransplantFailed as exc:
        raise SystemExit(str(exc))

    print(f"원본 얼굴   {info['source_face_px'][0]}x{info['source_face_px'][1]}px")
    print(f"T포즈 얼굴  {info['tpose_face_px'][0]}x{info['tpose_face_px'][1]}px "
          f"(캔버스 {info['canvas'][0]}x{info['canvas'][1]})")
    print(f"결과        {args.out}  ({info['bytes'] // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
