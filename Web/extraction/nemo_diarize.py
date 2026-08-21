"""
NeMo MSDD(NeuralDiarizer) 화자분리 러너 — 격리된 nemo_env 에서 실행한다.

  nemo_env/Scripts/python.exe nemo_diarize.py --input audio.mp3 --out-dir DIR [--num-speakers N]

출력: <out-dir>/diar.json  = {"duration":..,"speakers":[...],
       "segments":[{"start","end","spk"}...]}  (RTTM 파싱 결과, overlap 포함)
VAD(MarbleNet) + 다중스케일 TitaNet 임베딩 + 군집 + MSDD 신경 디코더.
"""
import argparse
import json
import subprocess
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
from omegaconf import OmegaConf


def to_wav(src, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                    "-ac", "1", "-ar", "16000", str(dst)], check=True)


def parse_rttm(path):
    segs = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            st, dur = float(p[3]), float(p[4])
            segs.append({"start": round(st, 3), "end": round(st + dur, 3),
                         "spk": p[7]})
    segs.sort(key=lambda s: s["start"])
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--num-speakers", type=int, default=None)
    ap.add_argument("--config", default="nemo_conf/diar_infer_telephonic.yaml")
    args = ap.parse_args()

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    wav = out / "audio.wav"
    print("[1/4] wav 변환"); to_wav(args.input, wav)

    manifest = out / "manifest.json"
    meta = {"audio_filepath": str(wav).replace("\\", "/"), "offset": 0,
            "duration": None, "label": "infer", "text": "-",
            "num_speakers": args.num_speakers, "rttm_filepath": None,
            "uem_filepath": None}
    manifest.write_text(json.dumps(meta) + "\n")

    print("[2/4] config 구성")
    cfg = OmegaConf.load(args.config)
    cfg.num_workers = 0            # Windows 멀티프로세싱 회피
    cfg.batch_size = 8
    cfg.device = "cpu"
    d = cfg.diarizer
    d.manifest_filepath = str(manifest).replace("\\", "/")
    d.out_dir = str(out).replace("\\", "/")
    d.oracle_vad = False
    d.vad.model_path = "vad_multilingual_marblenet"
    d.speaker_embeddings.model_path = "titanet_large"
    d.clustering.parameters.oracle_num_speakers = bool(args.num_speakers)
    if args.num_speakers:
        d.clustering.parameters.max_num_speakers = max(8, args.num_speakers)
    # MSDD(neural) 섹션 추가
    d.msdd_model = OmegaConf.create({
        "model_path": "diar_msdd_telephonic",
        "parameters": {
            "use_speaker_model_from_ckpt": True,
            "infer_batch_size": 8,
            "sigmoid_threshold": [0.7],
            "seq_eval_mode": False,
            "split_infer": True,
            "diar_window_length": 50,
            "overlap_infer_spk_limit": 5,
        },
    })

    print("[3/4] NeuralDiarizer 실행 (모델 최초 1회 다운로드)")
    from nemo.collections.asr.models.msdd_models import NeuralDiarizer
    NeuralDiarizer(cfg=cfg).diarize()

    print("[4/4] RTTM 파싱")
    rttm = out / "pred_rttms" / (wav.stem + ".rttm")
    segs = parse_rttm(rttm)
    spks = sorted({s["spk"] for s in segs})
    result = {"duration": None, "n_speakers": len(spks),
              "speakers": spks, "segments": segs}
    (out / "diar.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"완료: 화자 {len(spks)}명, 구간 {len(segs)}개 -> {out/'diar.json'}")


if __name__ == "__main__":
    main()
