"""한국어 STT 모델 하나를 실제 음성으로 측정한다. 한 번 실행에 한 백엔드만 쓴다.

SenseVoiceSmall(CPU INT8)·Whisper large-v3(GPU)·Qwen3-ASR(GPU)를 같은 입력으로 비교하기 위한
독립 실행기다. 대화 서버를 호출하지 않고, 모델을 내려받지 않으며, 로컬 경로만 사용한다.
VAD·잡음 제거·대화 프롬프트를 적용하지 않고 WAV를 그대로 넣어 STT 단독 차이만 본다.
GPU 백엔드는 프로세스를 하나씩 끝내 메모리를 돌려준 뒤 다음 백엔드를 실행한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = "stt_compare_v1"
NORMALIZATION = "nfkc_casefold_letters_digits_v1"
SAMPLE_RATE = 16000
MAX_SECONDS = 30.0          # 현재 대화 입력의 발화 상한과 같은 범위
MAX_SAMPLES = 500           # 자료 실수로 긴 실행이 되는 것을 막는 안전 상한
MAX_REPEATS = 10
MAX_CONDITION_CHARS = 32
DEFAULT_CONDITION = "unspecified"
STAGE_CODES = {"model_load": "model_load_failed", "warmup": "warmup_failed",
               "inference": "inference_failed", "close": "close_failed"}
MODEL_HASH_SUFFIXES = (".onnx", ".bin", ".safetensors", ".pt", ".pth", ".txt", ".json", ".model", ".yaml")
ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "Server"


# --- 문자열 정규화와 편집 거리 -------------------------------------------------

def normalize_text(text):
    """NFKC → casefold → 글자·숫자만 남긴다. 공백과 문장부호는 버린다."""
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    # casefold 뒤에 결합 문자가 생길 수 있어 한 번 더 합성한다.
    folded = unicodedata.normalize("NFKC", folded)
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] in ("L", "N"))


def edit_counts(reference, hypothesis):
    """문자 단위 Levenshtein 거리와 대치·삭제·삽입 개수를 함께 돌려준다."""
    ref, hyp = list(reference), list(hypothesis)
    rows, cols = len(ref) + 1, len(hyp) + 1
    dist = [[0] * cols for _ in range(rows)]
    for i in range(rows): dist[i][0] = i
    for j in range(cols): dist[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            same = ref[i-1] == hyp[j-1]
            dist[i][j] = min(dist[i-1][j-1] + (0 if same else 1), dist[i-1][j] + 1, dist[i][j-1] + 1)
    i, j, sub, dele, ins = len(ref), len(hyp), 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i-1] == hyp[j-1] and dist[i][j] == dist[i-1][j-1]:
            i, j = i-1, j-1
        elif i > 0 and j > 0 and dist[i][j] == dist[i-1][j-1] + 1:
            sub += 1; i, j = i-1, j-1
        elif i > 0 and dist[i][j] == dist[i-1][j] + 1:
            dele += 1; i -= 1
        else:
            ins += 1; j -= 1
    return {"substitutions": sub, "deletions": dele, "insertions": ins, "distance": dist[-1][-1]}


def score_pair(reference, hypothesis):
    """정규화한 정답·전사 한 쌍의 편집 통계를 만든다. 빈 정답도 그대로 표시한다."""
    ref, hyp = normalize_text(reference), normalize_text(hypothesis)
    row = edit_counts(ref, hyp)
    row.update(reference_chars=len(ref), hypothesis_chars=len(hyp),
               reference_empty=not ref, hypothesis_empty=not hyp, exact=ref == hyp)
    # 빈 정답(잡음 전용 표본)은 CER 분모에서 빼고 환청 비율로 따로 센다.
    row["cer"] = round(row["distance"] / len(ref), 4) if ref else None
    row["hallucinated"] = bool(not ref and hyp)
    return row


def aggregate_accuracy(rows):
    """성공한 인식 결과만 모아 micro/macro CER과 실패·환청 수를 계산한다."""
    scored = [row for row in rows if not row["failed"]]
    counted = [row for row in scored if not row["reference_empty"]]
    empty_ref = [row for row in scored if row["reference_empty"]]
    total_ref = sum(row["reference_chars"] for row in counted)
    total_distance = sum(row["distance"] for row in counted)
    per_sentence = [row["cer"] for row in counted]
    return {
        "scored_samples": len(counted), "empty_reference_samples": len(empty_ref),
        "failed_samples": sum(row["failed"] for row in rows),
        "micro_cer": round(total_distance / total_ref, 4) if total_ref else None,
        "macro_cer": round(statistics.fmean(per_sentence), 4) if per_sentence else None,
        "sentence_exact": sum(row["exact"] for row in counted),
        "sentence_exact_ratio": round(sum(row["exact"] for row in counted) / len(counted), 4) if counted else None,
        "empty_hypothesis_on_nonempty_reference": sum(row["hypothesis_empty"] for row in counted),
        "hallucination_on_empty_reference": sum(row["hallucinated"] for row in empty_ref),
        "reference_chars": total_ref, "edit_distance": total_distance,
        "substitutions": sum(row["substitutions"] for row in counted),
        "deletions": sum(row["deletions"] for row in counted),
        "insertions": sum(row["insertions"] for row in counted)}


def group_by_condition(rows):
    """조건별 정확도·지연을 따로 낸다. clean/noisy/negative를 합친 하나의 CER로 비교하지 않는다."""
    groups = []
    for condition in sorted({row["condition"] for row in rows}):
        subset = [row for row in rows if row["condition"] == condition]
        first = [row for row in subset if row["round"] == 1]
        groups.append({"condition": condition, "input_files": len(first),
                       "accuracy": aggregate_accuracy(first), "latency": latency_stats(subset)})
    return groups


def percentile(values, percent):
    ordered = sorted(values)
    if not ordered: return None
    return round(ordered[max(0, min(len(ordered)-1, int(round((len(ordered)-1)*percent))))], 3)


def latency_stats(rows):
    """실패한 호출을 뺀 지연 통계와 실시간 배속을 만든다."""
    done = [row for row in rows if not row["failed"]]
    seconds = [row["seconds"] for row in done]
    audio = sum(row["duration_seconds"] for row in done)
    return {"inferences": len(done),
            "average": round(statistics.fmean(seconds), 3) if seconds else None,
            "p50": percentile(seconds, .50), "p95": percentile(seconds, .95),
            "max": round(max(seconds), 3) if seconds else None,
            "audio_seconds": round(audio, 3),
            "rtf": round(sum(seconds) / audio, 4) if audio else None}


# --- 입력 자료 ---------------------------------------------------------------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""): digest.update(block)
    return digest.hexdigest()


def read_wav(path):
    """16kHz mono PCM16만 받는다. 형식이 다르면 거부하고 자동 변환하지 않는다.

    헤더를 먼저 검사해 상한을 넘는 파일을 읽지 않고, 읽은 뒤에는 실제 바이트 수가
    헤더의 프레임 수와 맞는지 확인한다. 잘린 WAV를 성공으로 처리하지 않는다.
    """
    path = Path(path)
    with wave.open(str(path), "rb") as handle:
        channels, width, rate, frames = (handle.getnchannels(), handle.getsampwidth(),
                                         handle.getframerate(), handle.getnframes())
        if (channels, width, rate) != (1, 2, SAMPLE_RATE):
            raise ValueError(f"WAV must be 16kHz mono PCM16: {path.name} has {rate}Hz {channels}ch {width*8}bit")
        duration = frames / float(SAMPLE_RATE)
        if frames <= 0: raise ValueError(f"Empty WAV: {path.name}")
        if duration > MAX_SECONDS: raise ValueError(f"WAV longer than {MAX_SECONDS}s: {path.name}")
        pcm = handle.readframes(frames)
    if len(pcm) != frames * 2:
        raise ValueError(f"Truncated WAV: {path.name} declares {frames} frames but holds {len(pcm)} bytes")
    array = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return {"pcm": pcm, "array": array, "duration_seconds": round(duration, 3),
            "sha256": sha256_file(path), "bytes": len(pcm)}


def read_condition(entry, sample_id):
    """조건 이름(clean/noisy/negative 등)은 선택이지만 형식은 강제한다."""
    if "condition" not in entry: return DEFAULT_CONDITION
    condition = entry["condition"]
    if not isinstance(condition, str): raise ValueError(f"Sample condition must be a string: {sample_id}")
    condition = condition.strip()
    if not condition or len(condition) > MAX_CONDITION_CHARS or condition != " ".join(condition.split()):
        raise ValueError(f"Sample condition must be a short single-line label: {sample_id}")
    return condition


def load_manifest(path, limit=None):
    """UTF-8 JSON object {samples:[{id,audio,reference,condition?,...}]}를 읽고 검증한다."""
    source = Path(path).read_bytes()
    data = json.loads(source.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
        raise ValueError("Manifest must be a JSON object with a 'samples' list")
    samples, seen, base = [], set(), Path(path).resolve().parent
    if not data["samples"]: raise ValueError("Manifest has no samples")
    if len(data["samples"]) > MAX_SAMPLES: raise ValueError(f"Manifest exceeds {MAX_SAMPLES} samples")
    recordings = data.get("source_recordings")
    if recordings is not None and (not isinstance(recordings, int) or isinstance(recordings, bool) or recordings < 1):
        raise ValueError("Manifest 'source_recordings' must be a positive integer when present")
    for entry in data["samples"]:
        if not isinstance(entry, dict): raise ValueError("Each sample must be an object")
        sample_id, audio = entry.get("id"), entry.get("audio")
        if not isinstance(sample_id, str) or not sample_id.strip(): raise ValueError("Sample id must be a non-empty string")
        if sample_id in seen: raise ValueError(f"Duplicate sample id: {sample_id}")
        if not isinstance(audio, str) or not audio.strip(): raise ValueError(f"Sample audio missing: {sample_id}")
        # 누락된 정답을 빈 문자열(잡음 전용 표본)로 바꾸지 않는다. 정확도 보고가 조용히 틀어진다.
        if "reference" not in entry: raise ValueError(f"Sample reference is required: {sample_id}")
        reference = entry["reference"]
        if not isinstance(reference, str): raise ValueError(f"Sample reference must be a string: {sample_id}")
        seen.add(sample_id)
        resolved = (base / audio).resolve()          # 상대 경로는 manifest 위치 기준이다.
        if not resolved.is_file(): raise FileNotFoundError(f"Missing audio for sample: {sample_id}")
        samples.append({"id": sample_id, "path": resolved, "reference": reference,
                        "condition": read_condition(entry, sample_id),
                        "source": entry.get("source"), "notes": entry.get("notes")})
    if limit is not None: samples = samples[:limit]
    return {"sha256": hashlib.sha256(source).hexdigest(), "declared": len(data["samples"]),
            "selected": len(samples), "samples": samples, "declared_source_recordings": recordings,
            "manifest_name": Path(path).name, "notes": data.get("notes")}


def describe_model(path):
    """모델 경로의 파일 해시와 HF 스냅샷 revision을 기록해 재현 가능하게 한다."""
    path = Path(path)
    if not path.exists(): raise FileNotFoundError("Model path does not exist")
    started = time.monotonic()
    targets = [path] if path.is_file() else sorted(
        p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in MODEL_HASH_SUFFIXES)
    files = [{"name": (p.name if path.is_file() else str(p.relative_to(path)).replace("\\", "/")),
              "bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in targets]
    parts = path.resolve().parts
    revision = parts[parts.index("snapshots")+1] if "snapshots" in parts[:-1] else None
    return {"path": str(path.resolve()), "revision": revision, "file_count": len(files),
            "files": files, "hash_seconds": round(time.monotonic()-started, 2)}


def package_versions(names):
    versions = {}
    for name in names:
        try:
            from importlib import metadata
            versions[name] = metadata.version(name)
        except Exception:
            versions[name] = None
    return versions


# --- 백엔드 ------------------------------------------------------------------
# 실제 모델 패키지는 여기서만 import한다. 모듈 최상단에 torch를 올리지 않아
# 모델이 없는 CPU 환경에서도 계산·검증 검사를 실행할 수 있다.

OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


def enforce_offline():
    """모델 적재 전에 이 평가 프로세스만 오프라인으로 고정한다.

    경로가 있다는 이유만으로 라이브러리가 허브를 조회하지 않는다고 보장할 수 없다.
    os.environ 변경은 이 프로세스와 자식에만 적용되며 시스템 설정을 바꾸지 않는다.
    """
    os.environ.update(OFFLINE_ENV)
    return dict(OFFLINE_ENV)


def apply_memory_budget(torch, budget_mib):
    """PyTorch allocator 상한만 설정한다. 프로세스 전체 VRAM 상한이 아니다."""
    if budget_mib is None: return {"requested_mib": None, "applied": False}
    total = torch.cuda.get_device_properties(0).total_memory
    fraction = min(1.0, (budget_mib * 1024 * 1024) / total)
    torch.cuda.set_per_process_memory_fraction(fraction, 0)
    return {"requested_mib": budget_mib, "applied": True, "fraction": round(fraction, 4),
            "device_total_mib": total // (1024 * 1024),
            "scope": "pytorch_caching_allocator_only; not a whole-process VRAM cap"}


def call_with_optional(factory, required, optional):
    """지원하지 않는 인수로 적재가 막히지 않게 하고, 실제로 적용한 인수를 함께 돌려준다."""
    try:
        return factory(**required, **optional), sorted(optional)
    except TypeError:
        return factory(**required), []


class SenseVoiceBackend:
    """운영과 같은 Server/realtime_audio.SenseVoiceFrontend를 그대로 쓴다(CPU 2스레드, ko, ITN)."""
    packages = ("sherpa-onnx", "numpy")

    def __init__(self, model_path, options):
        import asyncio
        if str(SERVER) not in sys.path: sys.path.insert(0, str(SERVER))
        from realtime_audio import SenseVoiceFrontend
        self.asyncio = asyncio
        self.loop = asyncio.new_event_loop()
        self.frontend = SenseVoiceFrontend(str(model_path))
        self.settings = {"device": "cpu", "num_threads": 2, "language": "ko", "use_itn": True,
                         "source": "Server/realtime_audio.SenseVoiceFrontend",
                         "local_files_only": "sherpa-onnx는 지정한 로컬 파일만 연다"}

    def transcribe(self, audio):
        # 운영과 같은 공용 계약(await transcribe(pcm))을 그대로 호출한다.
        observation = self.loop.run_until_complete(self.frontend.transcribe(audio["pcm"]))
        return observation.text

    def close(self):
        self.frontend.close()
        self.loop.close()


class WhisperBackend:
    """faster-whisper large-v3. generator를 끝까지 소비한 시점까지 시간에 포함한다."""
    packages = ("faster-whisper", "ctranslate2", "numpy")

    def __init__(self, model_path, options):
        # CTranslate2는 PyTorch allocator를 쓰지 않는다. 메모리 상한을 지원하는 척하지 않는다.
        if options.get("gpu_memory_budget_mib") is not None:
            raise ValueError("--gpu-memory-budget-mib is not supported by the whisper backend")
        from faster_whisper import WhisperModel
        self.model = WhisperModel(str(model_path), device="cuda",
                                  compute_type=options["compute_type"], local_files_only=True)
        self.settings = {"device": "cuda", "compute_type": options["compute_type"], "language": "ko",
                         "beam_size": 5, "temperature": 0, "condition_on_previous_text": False,
                         "vad_filter": False, "without_timestamps": True, "concurrency": 1,
                         "local_files_only": True,
                         "gpu_memory_budget": {"requested_mib": None, "applied": False,
                                               "reason": "unsupported_by_backend"}}

    def transcribe(self, audio):
        segments, _info = self.model.transcribe(
            audio["array"], language="ko", beam_size=5, temperature=0,
            condition_on_previous_text=False, vad_filter=False, without_timestamps=True)
        return "".join(segment.text for segment in segments).strip()

    def close(self):
        self.model = None


class QwenBackend:
    """qwen_asr 공식 사용법 그대로. FlashAttention을 요구하지 않고 transformers 기본 주의 구현을 쓴다."""
    packages = ("qwen-asr", "transformers", "torch", "numpy")

    def __init__(self, model_path, options):
        import torch
        from qwen_asr import Qwen3ASRModel
        self.torch = torch
        budget = apply_memory_budget(torch, options.get("gpu_memory_budget_mib"))
        self.model, applied = call_with_optional(
            lambda **kwargs: Qwen3ASRModel.from_pretrained(str(model_path), **kwargs),
            {"dtype": torch.bfloat16, "device_map": "cuda:0",
             "max_inference_batch_size": 1, "max_new_tokens": 512},
            {"local_files_only": True, "trust_remote_code": False})
        self.settings = {"device": "cuda:0", "dtype": "bfloat16", "language": "Korean",
                         "max_new_tokens": 512, "max_inference_batch_size": 1,
                         "return_time_stamps": False, "forced_aligner": False, "concurrency": 1,
                         "optional_kwargs_applied": applied, "gpu_memory_budget": budget}

    def transcribe(self, audio):
        self.torch.cuda.synchronize()
        result = self.model.transcribe(audio=(audio["array"], SAMPLE_RATE), language="Korean",
                                       return_time_stamps=False)
        self.torch.cuda.synchronize()
        if isinstance(result, (list, tuple)): result = result[0]
        return (result.text or "").strip()

    def close(self):
        self.model = None
        self.torch.cuda.empty_cache()


BACKENDS = {"sensevoice": SenseVoiceBackend, "whisper": WhisperBackend, "qwen": QwenBackend}


# --- GPU 메모리 표본 ----------------------------------------------------------

class GpuSampler:
    """nvidia-smi의 compute-apps에서 이 프로세스 PID 사용량만 주기적으로 읽어 최대값을 남긴다.

    다른 프로세스의 사용량·명령줄·세션 정보는 수집하지 않는다. 읽지 못하면 null로 남긴다.
    """
    def __init__(self, interval=0.3):
        self.interval, self.peak_mib, self.samples = interval, None, 0
        self.stop = threading.Event()
        self.thread = None
        self.reason = None

    def _read(self):
        output = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if output.returncode != 0: raise RuntimeError("nvidia-smi query failed")
        mine = str(os.getpid())
        for line in output.stdout.splitlines():
            pid, _, used = line.partition(",")
            if pid.strip() == mine: return int(used.strip())
        return None

    def _run(self):
        while not self.stop.wait(self.interval):
            try:
                used = self._read()
            except Exception as error:
                self.reason = type(error).__name__
                return
            self.samples += 1
            if used is not None: self.peak_mib = max(self.peak_mib or 0, used)

    def start(self):
        try:
            self._read()
        except Exception as error:
            self.reason = type(error).__name__
            return self
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def result(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=2)
        if self.peak_mib is None:
            return {"peak_mib": None, "method": "nvidia-smi_own_pid", "samples": self.samples,
                    "unavailable_reason": self.reason or "own_pid_not_listed"}
        return {"peak_mib": self.peak_mib, "method": "nvidia-smi_own_pid", "samples": self.samples,
                "interval_seconds": self.interval}


# --- 실행 --------------------------------------------------------------------

def build_report(args, manifest, model_info, warmup_sha):
    return {
        "schema": SCHEMA, "backend": args.backend,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": {"executable": Path(sys.executable).name, "script": Path(__file__).name,
                    "arguments": {"backend": args.backend, "model": str(args.model),
                                  "manifest": str(args.manifest), "repeats": args.repeats,
                                  "limit": args.limit, "compute_type": args.compute_type,
                                  "gpu_memory_budget_mib": args.gpu_memory_budget_mib,
                                  "include_transcripts": bool(args.include_transcripts)}},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "packages": package_versions(BACKENDS[args.backend].packages),
                        "offline_env": dict(OFFLINE_ENV)},
        "model": model_info,
        "manifest": {k: manifest[k] for k in ("manifest_name", "sha256", "declared", "selected",
                                              "declared_source_recordings", "notes")},
        "audio_policy": {"sample_rate": SAMPLE_RATE, "format": "mono PCM16 WAV",
                         "vad_applied": False, "denoise_applied": False, "dialogue_prompt_applied": False,
                         "passthrough": "동일한 원본 WAV를 모든 백엔드에 그대로 전달한다",
                         "scope": "STT 단독 측정이다. 운영 VAD 게이트 동작은 이 실행기에서 검증하지 않는다"},
        "normalization": {"name": NORMALIZATION,
                          "rule": "NFKC → casefold → 글자·숫자만 유지(공백·문장부호 제외)",
                          "known_limit": "한국어 숫자 발음과 숫자 표기를 자동으로 맞추지 않아 표기 차이가 오류로 잡힌다"},
        "warmup": {"audio_sha256": warmup_sha, "seconds": None, "counted_in_latency": False},
        "model_load_seconds": None, "transcripts_included": bool(args.include_transcripts),
        "samples": [], "rounds": [], "by_condition": [], "latency": {}, "accuracy": {}, "gpu": None,
        "failures": [], "failure_count": 0, "failed_stages": [], "complete": False}


def record_failure(report, stage, error, sample_id=None, round_index=None):
    """실패를 고정 코드와 예외 타입으로만 남긴다. 예외 문구·경로·전사를 쓰지 않는다."""
    report["failures"].append({"stage": stage, "code": STAGE_CODES[stage],
                               "type": type(error).__name__, "id": sample_id, "round": round_index})
    if stage not in report["failed_stages"]: report["failed_stages"].append(stage)
    report["failure_count"] = len(report["failures"])


def write_report(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check_arguments(args):
    if args.output.exists(): raise FileExistsError("Output file already exists; choose a new path")
    if not 1 <= args.repeats <= MAX_REPEATS: raise ValueError(f"--repeats must be 1..{MAX_REPEATS}")
    if args.limit is not None and args.limit < 1: raise ValueError("--limit must be at least 1")
    if args.gpu_memory_budget_mib is not None:
        if args.gpu_memory_budget_mib < 1: raise ValueError("--gpu-memory-budget-mib must be a positive integer")
        if args.backend != "qwen":
            raise ValueError(f"--gpu-memory-budget-mib is only supported by the qwen backend, not {args.backend}")


def run_samples(args, report, backend, clips, rows):
    """표본별 반복 추론. 한 표본의 실패가 나머지 실행을 멈추지 않는다."""
    for index, clip in enumerate(clips, start=1):
        entry = {"id": clip["id"], "condition": clip["condition"], "source": clip["source"],
                 "audio_sha256": clip["audio"]["sha256"], "audio_bytes": clip["audio"]["bytes"],
                 "duration_seconds": clip["audio"]["duration_seconds"],
                 "reference_chars": len(normalize_text(clip["reference"])), "rounds": []}
        texts = []
        for round_index in range(1, args.repeats + 1):
            row = {"id": clip["id"], "condition": clip["condition"], "round": round_index,
                   "failed": False, "duration_seconds": clip["audio"]["duration_seconds"],
                   "error_code": None, "error_type": None}
            started = time.monotonic()
            try:
                text = backend.transcribe(clip["audio"])
            except Exception as error:
                # 전사·경로·개인정보가 로그에 남지 않도록 예외 문구를 쓰지 않는다.
                row.update(failed=True, error_code=STAGE_CODES["inference"], error_type=type(error).__name__)
                record_failure(report, "inference", error, clip["id"], round_index)
                text = ""
            row["seconds"] = round(time.monotonic()-started, 3)
            row["rtf"] = round(row["seconds"]/row["duration_seconds"], 4) if row["duration_seconds"] else None
            row.update(score_pair(clip["reference"], text))
            texts.append(text)
            if args.include_transcripts: row["transcript"] = text
            rows.append(row)
            entry["rounds"].append({k: v for k, v in row.items() if k != "id"})
            print("sample", clip["id"], "round", round_index, "seconds", row["seconds"],
                  "cer", row["cer"], "failed", row["failed"], flush=True)
        entry["stable_across_rounds"] = len({normalize_text(t) for t in texts}) == 1
        if args.include_transcripts: entry["reference"] = clip["reference"]
        report["samples"].append(entry)
        write_report(args.output, report)            # 중간에 끊겨도 쓸 수 있는 보고서를 남긴다.
        print("completed", index, "/", len(clips), flush=True)


def summarize(report, args, clips, rows):
    by_round = []
    for round_index in range(1, args.repeats + 1):
        subset = [row for row in rows if row["round"] == round_index]
        if subset:
            by_round.append({"round": round_index, "latency": latency_stats(subset),
                             "accuracy": aggregate_accuracy(subset)})
    report["rounds"] = by_round
    report["by_condition"] = group_by_condition(rows)
    report["latency"] = {"overall": latency_stats(rows), "by_round": [r["latency"] for r in by_round]}
    # 정확도 기준은 1회차다. 반복은 지연 분포와 출력 안정성을 보기 위한 것이다.
    # 회차가 하나도 없어도 같은 모양을 유지해 빈 보고서가 만점처럼 읽히지 않게 한다.
    report["accuracy"] = dict(by_round[0]["accuracy"] if by_round else aggregate_accuracy([]),
                              basis="round_1", unstable_samples=sum(
                                  not s["stable_across_rounds"] for s in report["samples"]))
    scored = {row["id"] for row in rows if row["round"] == 1 and not row["failed"]}
    report["counts"] = {
        "unique_input_files": len(clips), "repeats": args.repeats,
        "repeat_inferences": len(rows), "expected_inferences": len(clips) * args.repeats,
        "scored_input_files": len(scored),
        # 점수가 없는 입력(미실행·전 회차 실패)을 조용히 빼지 않는다.
        "unscored_input_files": len(clips) - len(scored),
        "declared_source_recordings": report["manifest"]["declared_source_recordings"],
        "note": "입력 파일 수는 원본 녹음 수가 아니다. 같은 녹음의 변형이 여러 입력일 수 있다"}
    report["complete"] = (not report["failures"] and len(rows) == len(clips) * args.repeats
                          and report["counts"]["unscored_input_files"] == 0)
    return report


def run(args):
    check_arguments(args)
    manifest = load_manifest(args.manifest, args.limit)
    warmup = read_wav(args.warmup_audio)                  # 예열용은 평가 표본과 분리한다.
    clips = [dict(sample, audio=read_wav(sample["path"])) for sample in manifest["samples"]]
    if any(clip["audio"]["sha256"] == warmup["sha256"] for clip in clips):
        raise ValueError("--warmup-audio must not be one of the evaluation samples")
    model_info = describe_model(args.model)
    report = build_report(args, manifest, model_info, warmup["sha256"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, report)

    sampler = GpuSampler().start()
    backend, rows, stage = None, [], None
    try:
        stage = "model_load"
        enforce_offline()                                 # 모델 적재 전에 오프라인으로 고정한다.
        started = time.monotonic()
        backend = BACKENDS[args.backend](args.model, {"compute_type": args.compute_type,
                                                      "gpu_memory_budget_mib": args.gpu_memory_budget_mib})
        report["model_load_seconds"] = round(time.monotonic()-started, 3)   # 적재는 예열·추론과 구분한다.
        report["backend_settings"] = backend.settings
        stage = "warmup"
        started = time.monotonic()
        backend.transcribe(warmup)
        report["warmup"]["seconds"] = round(time.monotonic()-started, 3)
        print("loaded", report["model_load_seconds"], "warmup", report["warmup"]["seconds"], flush=True)
        stage = "inference"
        run_samples(args, report, backend, clips, rows)
    except Exception as error:
        # 적재·예열·중단 실패도 보고서를 남긴다. 결과 없는 조용한 종료를 만들지 않는다.
        record_failure(report, stage, error)
        print("stage_failed", stage, type(error).__name__, flush=True)
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception as error:
                # 정리 실패가 앞선 실패를 덮지 않도록 따로 기록한다.
                record_failure(report, "close", error)
                print("stage_failed close", type(error).__name__, flush=True)
        report["gpu"] = sampler.result()
        summarize(report, args, clips, rows)
        write_report(args.output, report)

    print(json.dumps({k: report[k] for k in ("backend", "counts", "accuracy", "by_condition", "gpu",
                                             "latency", "failure_count", "failed_stages", "complete")},
                     ensure_ascii=False), flush=True)
    # 실패가 있으면 CER에서 조용히 빼고 통과로 보고하지 않는다.
    return 0 if report["complete"] else 1


def positive_int(value):
    number = int(value)
    if number < 1: raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=sorted(BACKENDS), required=True)
    parser.add_argument("--model", type=Path, required=True, help="로컬 모델 경로. 자동 내려받기는 하지 않는다")
    parser.add_argument("--manifest", type=Path, required=True, help="UTF-8 JSON {samples:[{id,audio,reference}]}")
    parser.add_argument("--output", type=Path, required=True, help="새 보고서 경로. 기존 파일을 덮어쓰지 않는다")
    parser.add_argument("--warmup-audio", type=Path, required=True, help="평가와 분리한 16kHz mono PCM16 예열 WAV")
    parser.add_argument("--repeats", type=int, default=3, help="같은 표본의 반복 추론 횟수")
    parser.add_argument("--limit", type=int, help="앞에서부터 사용할 표본 수")
    parser.add_argument("--compute-type", default="float16", help="whisper 전용 연산 정밀도")
    parser.add_argument("--gpu-memory-budget-mib", type=positive_int,
                        help="qwen 전용. PyTorch allocator 상한만 설정한다(프로세스 전체 상한이 아니다)")
    parser.add_argument("--include-transcripts", action="store_true",
                        help="공개 자료일 때만 원문·전사를 보고서에 포함한다")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(run(args))
    except SystemExit:
        raise
    except Exception as error:
        print("setup_failed", type(error).__name__, file=sys.stderr, flush=True)
        raise SystemExit(2)


if __name__ == "__main__": main()
