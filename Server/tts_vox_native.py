"""VoxCPM2 참조 음성 복제와 생성 중 오디오 출력.

2026-09-17 실제 모델·취소 검사에서 사용한 어댑터를 승격했다.
모델 가중치나 생성 알고리즘은 수정하지 않는다. reference 모드는 전사를 사용하지 않는다.
48kHz 원음을 내보내며 HTTP 어댑터가 24kHz 변환과 마지막 잔여 배출을 담당한다.
"""
from __future__ import annotations

import re
import threading
from typing import Generator, Optional

import numpy as np
import torch

_MODES = ("reference", "ultimate")


class Candidate:
    """VoxCPM2 실시간 스트리밍 음성 복제 후보."""

    name = "voxcpm2"

    def __init__(
        self,
        model_path: str,
        *,
        mode: str = "reference",
        device: Optional[str] = None,
        load_denoiser: bool = False,
        optimize: bool = False,
        cfg: float = 2.0,
        timesteps: int = 10,
        min_len: int = 2,
        max_len: int = 2000,
        ratio_threshold: float = 6.0,
        streaming_prefix_len: int = 4,
        **options,
    ):
        """
        Args:
            model_path: 로컬 스냅샷 디렉터리 또는 HF repo id.
            mode: "reference"(전사 불필요) 또는 "ultimate"(같은 파일 prompt+ref, 정확한 전사).
            load_denoiser: False 가 기본. True 는 modelscope/zipenhancer 설치가 필요하다.
            optimize: torch.compile. 첫 검증은 False, 가속 실험 때만 True.
            cfg, timesteps: 기본 2.0 / 10.
            min_len: 원본 기본값 유지. EOS·정지 판정은 건드리지 않는다.
            max_len, ratio_threshold: 문장 길이 상한. 긴 문장이 잘리지 않을 만큼 넉넉히 둔다.
        """
        if options:
            raise TypeError(f"알 수 없는 옵션: {sorted(options)}")
        if mode not in _MODES:
            raise ValueError(f"mode 는 {_MODES} 중 하나여야 한다: {mode!r}")

        from voxcpm.core import VoxCPM
        from voxcpm.model.voxcpm2 import VoxCPM2Model

        self.mode = mode
        self.cfg = float(cfg)
        self.timesteps = int(timesteps)
        self.min_len = int(min_len)
        self.max_len = int(max_len)
        self.ratio_threshold = float(ratio_threshold)
        self.streaming_prefix_len = int(streaming_prefix_len)

        self._wrapper = VoxCPM.from_pretrained(
            hf_model_id=model_path,
            load_denoiser=load_denoiser,
            optimize=optimize,
            device=device,
        )
        model = self._wrapper.tts_model
        if not isinstance(model, VoxCPM2Model):
            raise ValueError(
                f"VoxCPM2 가중치가 아니다(config.architecture 확인): {type(model).__name__}"
            )
        self._model = model
        self._busy = threading.Lock()  # KV 캐시가 모델 공유 상태라 동시 스트림을 막는다

    # ------------------------------------------------------------------ #
    @property
    def rate(self) -> int:
        """모델 출력 샘플레이트. 여기서 리샘플하지 않는다."""
        return int(self._model.sample_rate)

    def prepare(self, wav_path: str, transcript: Optional[str] = None) -> dict:
        """참조 음성으로 prompt cache 를 한 번만 만든다. 이후 문장마다 재사용한다."""
        if self.mode == "reference":
            cache = self._model.build_prompt_cache(
                reference_wav_path=wav_path,
                trim_silence_vad=False,
            )
        else:  # ultimate
            if not transcript or not transcript.strip():
                raise ValueError("ultimate 모드는 참조 음성의 정확한 전사가 필요하다")
            cache = self._model.build_prompt_cache(
                prompt_text=transcript,
                prompt_wav_path=wav_path,
                reference_wav_path=wav_path,
                trim_silence_vad=False,
            )
        return cache

    def stream(
        self,
        text: str,
        prompt: dict,
        seed: Optional[int] = None,
    ) -> Generator[np.ndarray, None, None]:
        """float32 mono numpy 청크를 동기 generator 로 내보낸다.

        생성은 첫 next() 때 시작한다. 소비자가 generator.close() 를 부르면 yield 지점에
        GeneratorExit 이 들어가고, 내부 `with self.audio_vae.streaming_decode()` 의 __exit__ 과
        _inference 제너레이터 close 가 실행된다. 내부 _inference 는 상위 스트리밍 분기에서
        명시적으로 닫히지 않으므로 여기서 직접 close() 를 보장한다.
        """
        target = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
        if not target:
            raise ValueError("빈 문장은 합성하지 않는다")

        def _iter():
            if not self._busy.acquire(blocking=False):
                raise RuntimeError("이전 스트림이 아직 살아 있다. close() 후 다시 호출한다")
            gen = None
            try:
                gen = self._model.generate_with_prompt_cache_streaming(
                    target_text=target,
                    prompt_cache=prompt,
                    min_len=self.min_len,
                    max_len=self.max_len,
                    inference_timesteps=self.timesteps,
                    cfg_value=self.cfg,
                    retry_badcase=False,
                    retry_badcase_ratio_threshold=self.ratio_threshold,
                    streaming_prefix_len=self.streaming_prefix_len,
                    seed=seed,
                )
                for wav, _tokens, _feat in gen:
                    yield _to_mono_f32(wav)
            finally:
                try:
                    if gen is not None:
                        gen.close()
                finally:
                    self._busy.release()

        return _iter()

    def generate_full(self, text: str, prompt: dict, seed: Optional[int] = None) -> np.ndarray:
        """비스트리밍 1회 생성. 스트리밍 청크 이어붙인 길이와 꼬리 flush 를 비교할 때만 쓴다."""
        target = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
        with self._busy:
            wav, _tokens, _feat = self._model.generate_with_prompt_cache(
                target_text=target,
                prompt_cache=prompt,
                min_len=self.min_len,
                max_len=self.max_len,
                inference_timesteps=self.timesteps,
                cfg_value=self.cfg,
                retry_badcase=False,
                retry_badcase_ratio_threshold=self.ratio_threshold,
                seed=seed,
            )
        return _to_mono_f32(wav)

    def limits(self, text: str) -> dict:
        """이 문장에 실제로 적용되는 길이 상한. 긴 문장 잘림 여부를 미리 확인한다."""
        target = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
        n_tokens = len(self._model.text_tokenizer(target))
        patches = min(int(n_tokens * self.ratio_threshold + 10), self.max_len)
        chunk = getattr(self._model, "_decode_chunk_size", self._model.chunk_size)
        per_patch = self._model.patch_size * chunk / float(self.rate)
        return {
            "text_tokens": n_tokens,
            "max_patches": patches,
            "patch_seconds": per_patch,
            "max_seconds": patches * per_patch,
        }

    def close_prompt(self, prompt: dict) -> None:
        """prompt cache 해제. 캐시는 CPU 텐서라 GPU 반환은 없다."""
        if isinstance(prompt, dict):
            prompt.clear()

    def close(self) -> None:
        self._model = None
        self._wrapper = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _to_mono_f32(wav: torch.Tensor) -> np.ndarray:
    """(1, N) 또는 (N,) 텐서를 float32 mono numpy 로. 자르기·리샘플 없음."""
    t = wav.detach()
    if t.ndim == 2:
        if t.shape[0] != 1:
            raise ValueError(f"배치 1만 지원한다: {tuple(t.shape)}")
        t = t[0]
    elif t.ndim != 1:
        raise ValueError(f"예상 밖의 출력 shape: {tuple(t.shape)}")
    return np.ascontiguousarray(t.to(torch.float32).cpu().numpy())
