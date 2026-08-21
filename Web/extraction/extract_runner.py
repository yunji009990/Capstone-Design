"""Extraction runner — 화자 분리 · reference 추출을 격리 실행한다.

'Audio extraction' 폴더의 기존 모듈을 그대로 import 해서 실행한다. 반드시
cwd = 'Audio extraction' 로 실행해야 한다 (NeMo 하위 프로세스가 config 를
상대경로 nemo_conf/…yaml 로 열기 때문 — cwd 를 상속받아 config 를 찾는다).

CLI:
  python extract_runner.py --extraction-dir <dir> --input <audio>
      --out-dir <dir> --engine nemo|ecapa [--speakers N]

결과(result.json + full.mp3 + talkerN/*)는 기존 run_extraction* 이 out-dir 에 직접 쓴다.
"""
import argparse
import sys
from pathlib import Path


def log(msg):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction-dir", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--engine", default="nemo")
    ap.add_argument("--speakers", type=int, default=0)
    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.extraction_dir).resolve()))
    from extract_nemo import run_extraction_nemo            # noqa: E402

    # 엔진은 NeMo MSDD 만 지원한다(ECAPA 는 제거됨). --engine 인자는 호환을 위해 무시.
    n = args.speakers or None
    run_extraction_nemo(args.input, args.out_dir, n_speakers=n,
                        viz=True, progress=log)
    log("EXTRACT_DONE")


if __name__ == "__main__":
    main()
