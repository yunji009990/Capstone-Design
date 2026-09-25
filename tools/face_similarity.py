"""Measure identity drift across the Tripo pipeline with ArcFace embeddings.

왜 필요한가
───────────
사진 → T포즈 → 생성 → 텍스처로 이어지는 각 단계는 전부 생성 모델이다. 단계마다
얼굴이 조금씩 다른 사람이 되는데, 지금까지는 눈으로만 비교해서 "프롬프트를 바꿨더니
나아진 것 같다" 수준에서 결론이 멈췄다. 같은 사람인지를 숫자로 재면 프롬프트·모델
A/B 의 결과가 쌓이고, 어느 단계에서 얼마나 잃는지도 보인다.

무엇을 재는가
─────────────
1. **동일인 점수** — ArcFace(buffalo_l) 임베딩의 코사인 유사도. 얼굴 인식이
   "같은 사람"을 판정할 때 쓰는 것과 같은 값이다.
2. **얼굴 픽셀 수** — 검출된 얼굴 상자의 크기. 텍스처로 구워질 수 있는 정보량의
   상한이라, 점수가 낮을 때 원인이 "다른 사람을 그렸다"인지 "그릴 픽셀이 없었다"인지
   가른다. 1024 전신 T포즈에서 얼굴은 보통 한 변 100~200px 수준이다.

쓰는 법
───────
    # 아무 이미지들끼리 비교
    python tools/face_similarity.py a.png b.png c.png

    # 실험 폴더 한 개: reference.png ↔ reference_tpose.png (+ 모델 렌더)
    python tools/face_similarity.py --trial tools/_work/tripo_trial_20260918_fullbody \
        --shots fullbody --save

    # 실험 폴더 전체를 한 표로
    python tools/face_similarity.py --all-trials

`--save` 는 결과를 그 폴더의 trial.json 에 `face_identity` 로 남긴다. 유료 작업
기록(tasks/assets)은 건드리지 않는다.

모델 렌더는 tools/faceshot/server.py 로 먼저 찍는다. 네 방위 중 얼굴이 가장 크게
잡힌 것을 `--shots <이름>` 이 알아서 고른다. 직접 찍은 PNG 는 `--render` 로 넣는다.

준비
────
무거운 의존성이라 전용 venv 를 쓴다. 프로젝트 python 은 그대로 둔다.

    python -m venv tools/_work/venv-face
    tools/_work/venv-face/Scripts/python.exe -m pip install \
        numpy onnxruntime opencv-python-headless pillow insightface
    tools/_work/venv-face/Scripts/python.exe tools/face_similarity.py --all-trials

첫 실행에서 insightface 가 buffalo_l 모델 묶음(약 300MB)을 ~/.insightface 에 내려받는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "tools" / "_work"

# 코사인 유사도 해석 기준. buffalo_l 의 1:1 판정에서 흔히 쓰는 구간이다.
# 절대값보다 같은 원본에 대한 후보들 사이의 차이가 중요하다.
SAME = 0.60          # 이 위면 사람이 봐도 같은 사람이라고 한다
MAYBE = 0.40         # 이 사이는 "닮았지만 다른 사람" 이 섞인다


def verdict(score: float | None) -> str:
    if score is None:
        return "얼굴 없음"
    if score >= SAME:
        return "같은 사람"
    if score >= MAYBE:
        return "애매"
    return "다른 사람"


class Analyzer:
    """insightface 를 늦게 불러온다. 도움말과 인자 검사는 의존성 없이 되도록."""

    def __init__(self, det_size: int = 640):
        try:
            import numpy  # noqa: F401
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise SystemExit(
                f"의존성이 없습니다 ({exc.name}). 파일 상단 '준비' 절의 venv 를 만들고 그 python 으로 실행하세요."
            ) from exc
        self.app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=(det_size, det_size))

    def read(self, path: Path):
        import cv2
        import numpy as np
        blob = np.fromfile(str(path), dtype=np.uint8)   # 한글 경로에서 imread 가 실패한다
        image = cv2.imdecode(blob, cv2.IMREAD_COLOR)
        if image is None:
            raise SystemExit(f"이미지를 읽지 못했습니다: {path}")
        return image

    def face(self, path: Path) -> dict | None:
        """가장 큰 얼굴 하나. 임베딩과 상자 크기를 함께 돌려준다."""
        image = self.read(path)
        faces = self.app.get(image)
        if not faces:
            return None
        best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        x1, y1, x2, y2 = (int(v) for v in best.bbox)
        height, width = image.shape[:2]
        return {
            "file": path.name,
            "image_px": [width, height],
            "face_px": [x2 - x1, y2 - y1],
            "face_share": round((x2 - x1) * (y2 - y1) / (width * height), 4),
            "faces_found": len(faces),
            "embedding": best.normed_embedding,
        }


def cosine(a, b) -> float:
    import numpy as np
    return round(float(np.dot(a["embedding"], b["embedding"])), 4)


def describe(face: dict | None, label: str) -> str:
    if face is None:
        return f"  {label:<22s} 얼굴을 찾지 못함"
    w, h = face["face_px"]
    iw, ih = face["image_px"]
    extra = "" if face["faces_found"] == 1 else f"  (얼굴 {face['faces_found']}개 중 가장 큰 것)"
    return f"  {label:<22s} {iw}x{ih} 안에 얼굴 {w}x{h}px ({face['face_share'] * 100:.1f}%){extra}"


def measure(analyzer: Analyzer, items: list[tuple[str, Path]]) -> dict:
    """[(라벨, 경로)] 를 받아 첫 항목을 기준으로 삼은 결과를 만든다."""
    faces = [(label, path, analyzer.face(path)) for label, path in items]
    print("얼굴 크기")
    for label, _, face in faces:
        print(describe(face, label))

    base_label, _, base = faces[0]
    result = {"reference": base_label, "stages": [], "pairs": []}
    for label, path, face in faces:
        result["stages"].append({
            "label": label, "file": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
            "found": face is not None,
            "image_px": face["image_px"] if face else None,
            "face_px": face["face_px"] if face else None,
        })

    print(f"\n동일인 점수 (기준: {base_label})")
    if base is None:
        print("  기준 이미지에서 얼굴을 찾지 못해 비교할 수 없습니다.")
        return result
    for label, _, face in faces[1:]:
        score = cosine(base, face) if face else None
        result["pairs"].append({"from": base_label, "to": label, "cosine": score,
                                "verdict": verdict(score)})
        shown = f"{score:+.3f}" if score is not None else "  —  "
        print(f"  {base_label} → {label:<18s} {shown}  {verdict(score)}")

    # 단계 사이의 낙폭. 어디서 잃는지가 여기서 보인다.
    chain = [(label, face) for label, _, face in faces if face is not None]
    if len(chain) > 2:
        print("\n단계별 낙폭")
        for (la, fa), (lb, fb) in zip(chain, chain[1:]):
            score = cosine(fa, fb)
            result["pairs"].append({"from": la, "to": lb, "cosine": score, "verdict": verdict(score)})
            print(f"  {la} → {lb:<18s} {score:+.3f}  {verdict(score)}")
    return result


SHOTS = WORK / "faceshot_shots"


def best_shot(analyzer: "Analyzer", tag: str) -> Path | None:
    """faceshot 이 찍은 네 방위 중 얼굴이 가장 크게 잡힌 장을 고른다.

    모델마다 정면이 보는 축이 달라서 방위를 미리 알 수 없다. 검출기에게 맡긴다."""
    best, area = None, 0
    for path in sorted(SHOTS.glob(f"{tag}_az*.png")):
        face = analyzer.face(path)
        if face is None:
            continue
        size = face["face_px"][0] * face["face_px"][1]
        if size > area:
            best, area = path, size
    return best


def trial_items(directory: Path, render: Path | None) -> list[tuple[str, Path]]:
    items = []
    for label, name in (("원본 사진", "reference.png"), ("T포즈", "reference_tpose.png")):
        path = directory / name
        if path.is_file():
            items.append((label, path))
    if render is not None:
        if not render.is_file():
            raise SystemExit(f"렌더 이미지가 없습니다: {render}")
        items.append(("모델 렌더", render))
    if len(items) < 2:
        raise SystemExit(f"비교할 이미지가 부족합니다: {directory}")
    return items


def save_into_trial(directory: Path, result: dict) -> None:
    path = directory / "trial.json"
    if not path.is_file():
        print(f"\ntrial.json 이 없어 저장하지 않았습니다: {directory}")
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["face_identity"] = result          # tasks/assets 는 건드리지 않는다
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    print(f"\ntrial.json 에 face_identity 를 기록했습니다: {path}")


def all_trials(analyzer: Analyzer, save: bool) -> None:
    rows = []
    for directory in sorted(WORK.glob("tripo_trial_*")):
        if not directory.is_dir():
            continue
        original, tpose = directory / "reference.png", directory / "reference_tpose.png"
        if not (original.is_file() and tpose.is_file()):
            continue
        a, b = analyzer.face(original), analyzer.face(tpose)
        score = cosine(a, b) if a and b else None
        rows.append((directory.name, a, b, score))
        if save:
            save_into_trial(directory, {"reference": "원본 사진", "stages": [], "pairs": [
                {"from": "원본 사진", "to": "T포즈", "cosine": score, "verdict": verdict(score)}]})
    if not rows:
        print("원본과 T포즈가 모두 있는 실험 폴더가 없습니다.")
        return
    print(f"{'실험 폴더':<40s} {'원본 얼굴':>11s} {'T포즈 얼굴':>11s} {'동일인':>8s}  판정")
    print("-" * 86)
    for name, a, b, score in sorted(rows, key=lambda r: -(r[3] or -1)):
        fa = f"{a['face_px'][0]}x{a['face_px'][1]}" if a else "없음"
        fb = f"{b['face_px'][0]}x{b['face_px'][1]}" if b else "없음"
        shown = f"{score:+.3f}" if score is not None else "  —  "
        print(f"{name:<40s} {fa:>11s} {fb:>11s} {shown:>8s}  {verdict(score)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="*", type=Path, help="비교할 이미지. 첫 번째가 기준")
    parser.add_argument("--trial", type=Path, help="실험 폴더. 원본과 T포즈를 자동으로 찾는다")
    parser.add_argument("--render", type=Path, help="모델을 정면에서 찍은 PNG. --trial 에 더한다")
    parser.add_argument("--shots", help="faceshot 이 찍은 이름. 네 방위 중 얼굴이 가장 큰 장을 쓴다")
    parser.add_argument("--all-trials", action="store_true", help="tools/_work 의 모든 실험을 한 표로")
    parser.add_argument("--save", action="store_true", help="결과를 trial.json 의 face_identity 에 기록")
    parser.add_argument("--det-size", type=int, default=640, help="검출 입력 크기. 작은 얼굴이면 1024")
    parser.add_argument("--json", action="store_true", help="사람이 읽는 표 대신 JSON")
    args = parser.parse_args()

    if not (args.images or args.trial or args.all_trials):
        parser.error("이미지, --trial, --all-trials 중 하나는 필요합니다")

    analyzer = Analyzer(args.det_size)

    if args.all_trials:
        all_trials(analyzer, args.save)
        return 0

    if args.trial:
        render = args.render
        if render is None and args.shots:
            render = best_shot(analyzer, args.shots)
            if render is None:
                raise SystemExit(f"{SHOTS} 에서 {args.shots}_az*.png 의 얼굴을 찾지 못했습니다")
            print(f"모델 렌더로 {render.name} 을 씁니다 (네 방위 중 얼굴이 가장 큰 장)\n")
        items = trial_items(args.trial, render)
    else:
        for path in args.images:
            if not path.is_file():
                raise SystemExit(f"파일이 없습니다: {path}")
        items = [(path.stem, path) for path in args.images]

    result = measure(analyzer, items)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.save and args.trial:
        save_into_trial(args.trial, result)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
