"""얼굴 이식 → 고밀도 생성 → 감축 을 한 번에 돌린다. 시험 회차를 빠르게 하려는 것.

지금까지 손으로 하던 순서다.

    1. 서버에서 세션 폴더를 받는다            (scp, 크레딧 0)
    2. T포즈를 키우고 원본 얼굴을 합성한다     (face_transplant.py, 크레딧 0)
    3. 생성·리깅·앉기                        (tripo_trial.py, 약 95 크레딧)
    4. Quest 예산으로 감축                    (gltf-transform, 크레딧 0)

각 단계는 결과 파일이 있으면 건너뛴다. 중간에 끊겨도 같은 명령으로 이어진다.
유료 단계는 tripo_trial.py 의 기록·복구 규칙을 그대로 쓴다.

    python tools/face_pipeline_trial.py --session 6ed44712 --fetch
    python tools/face_pipeline_trial.py --session 6ed44712 --face-limit 250000

마지막에 렌더·측정 명령을 찍어 준다. 그 둘은 브라우저가 필요해 여기서 하지 않는다.

왜 이렇게 나눠 두나
───────────────────
생성 설정이 양날의 검이라 회차마다 값을 바꿔 가며 비교해야 한다. face_limit 을
무제한으로 두면 콧방울·눈꺼풀이 살지만(사진 A, 0.198→0.351), T포즈에 이마를
가로지르는 머리카락이 있으면 그것까지 입체로 조각해 얼굴을 망친다(사진 B, 효과 없음).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "tools" / "_work"
FACE_PY = WORK / "venv-face" / "Scripts" / "python.exe"
REMOTE = "crc_unity@220.69.208.201"


def run(args, **kwargs):
    print("  $ " + " ".join(str(a) for a in args), flush=True)
    return subprocess.run(args, **kwargs)


def find_session(prefix: str) -> Path | None:
    hits = [p for p in WORK.glob(prefix + "*") if p.is_dir() and (p / "tpose.png").is_file()]
    return hits[0] if len(hits) == 1 else None


def fetch(prefix: str) -> Path:
    print(f"— 서버에서 {prefix} 받기")
    done = run(["scp", "-q", "-r", f"{REMOTE}:webapp/Survey/data/sessions/{prefix}*", str(WORK)])
    if done.returncode != 0:
        raise SystemExit("scp 실패 — ssh 키 인증과 세션 앞자리를 확인하세요")
    session = find_session(prefix)
    if session is None:
        raise SystemExit(f"{prefix} 로 시작하는 세션이 하나로 좁혀지지 않았습니다")
    return session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True, help="세션 ID 앞자리")
    parser.add_argument("--fetch", action="store_true", help="로컬에 없으면 서버에서 받는다")
    parser.add_argument("--tag", help="결과 폴더 이름. 기본은 세션 앞자리")
    parser.add_argument("--scale", type=float, default=3.0, help="T포즈 확대 배율")
    parser.add_argument("--no-transplant", action="store_true",
                        help="합성 없이 원래 T포즈로 생성한다 (대조군)")
    parser.add_argument("--face-limit", default="auto", help="삼각형 상한. auto 면 Tripo 가 정한다")
    parser.add_argument("--geometry-quality", choices=("standard", "detailed"), default="detailed")
    parser.add_argument("--ratio", default="0.03", help="감축 비율. 0.03 이면 약 6만 면")
    parser.add_argument("--dry-run", action="store_true", help="생성 요청만 확인하고 멈춘다")
    args = parser.parse_args()

    session = find_session(args.session)
    if session is None:
        if not args.fetch:
            raise SystemExit(f"{args.session} 세션이 로컬에 없습니다. --fetch 를 붙이세요")
        session = fetch(args.session)
    print(f"세션 폴더: {session}")

    source = session / "tpose.png"
    if not args.no_transplant:
        source = session / f"tpose_hires_x{args.scale:g}.png"
        if source.is_file():
            print(f"— 합성본이 이미 있습니다: {source.name}")
        else:
            if not FACE_PY.is_file():
                raise SystemExit(f"얼굴 검출 venv 가 없습니다: {FACE_PY}")
            face = next((session / f"front{e}" for e in (".jpg", ".png", ".jpeg")
                         if (session / f"front{e}").is_file()), None)
            if face is None:
                raise SystemExit("원본 사진(front.*)이 없습니다")
            print("— 얼굴 이식")
            if run([str(FACE_PY), str(ROOT / "tools/face_transplant.py"),
                    "--body", str(session / "tpose.png"), "--face", str(face),
                    "--out", str(source), "--scale", str(args.scale)]).returncode != 0:
                raise SystemExit("합성 실패")

    tag = args.tag or args.session
    out = WORK / f"tripo_trial_{tag}"
    print("— 생성·리깅·앉기")
    command = [sys.executable, str(ROOT / "tools/tripo_trial.py"), "--image", str(source),
               "--out", str(out), "--face-limit", args.face_limit,
               "--geometry-quality", args.geometry_quality]
    if args.dry_run:
        command.append("--dry-run")
    if run(command).returncode != 0:
        raise SystemExit("생성 실패 — 같은 명령으로 다시 실행하면 이어집니다")
    if args.dry_run:
        return 0

    animated = out / "animated.glb"
    reduced = out / f"animated_r{args.ratio}.glb"
    if not reduced.is_file():
        print("— 감축")
        # Windows 에서 npx 는 npx.cmd 라 이름만으로는 CreateProcess 가 못 찾는다.
        # which() 가 돌려주는 실제 경로를 그대로 쓴다.
        npx = shutil.which("npx")
        if npx is None:
            print("  npx 가 없어 감축을 건너뜁니다. Node 를 설치하면 됩니다.")
        else:
            run([npx, "--yes", "@gltf-transform/cli@latest", "simplify",
                 str(animated), str(reduced), "--ratio", args.ratio, "--error", "0.0005"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n완료. 남은 두 단계는 브라우저가 필요합니다.\n")
    target = reduced if reduced.is_file() else animated
    print("  1) tools/faceshot/server.py 를 띄우고 아래 주소를 연다")
    print(f"     http://127.0.0.1:8778/faceshot/index.html"
          f"?glb=../_work/{target.relative_to(WORK).as_posix()}&out={tag}")
    print("\n  2) 점수를 잰다")
    print(f"     {FACE_PY} tools/face_similarity.py \\")
    print(f"         --trial {session.relative_to(ROOT).as_posix()} --shots {tag} --save")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
