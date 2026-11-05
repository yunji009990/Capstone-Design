"""별도 loopback 서비스 팩토리. 운영 서버의 기본 설정을 변경하지 않는다."""
from __future__ import annotations

import io
import os
import wave
from contextlib import asynccontextmanager

from local_demo_config import DATA, MODELS, VOX_SOURCE, address


def configure_local_vox(model):
    # 합성 API는 문장 300자·참조 12초, 생성은 최대 2000패치다.
    # UTF-8 바이트 기준 텍스트 상한 1200 + 참조 75 + 특수 토큰 + 생성 2000은
    # 4096 안에 들어간다. 가중치·생성 길이는 유지하고 빈 KV 공간만 줄인다.
    import torch
    model.base_lm.kv_cache = None
    model.residual_lm.kv_cache = None
    torch.cuda.empty_cache()
    for lm in (model.base_lm, model.residual_lm):
        lm.setup_cache(1, 4096, model.device, model._dtype())
    model.config.max_length = 4096
    release_cuda_cache()


def release_cuda_cache():
    import torch
    torch.cuda.empty_cache()


def dialogue_app(config):
    from dialogue_server import Settings, create_app
    from local_demo_providers import OpenAIFrontend, OpenAILLMClient
    from realtime_llm import ModelEndpoint

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("OPENAI_API_KEY 환경 변수를 설정하세요.")
    url = "https://api.openai.com/v1/chat/completions"
    normal = ModelEndpoint(url, config["llm_model"], {"reasoning_effort": config["normal_effort"]},
                           key, config["normal_tokens"])
    reasoning = ModelEndpoint(url, config["llm_model"], {"reasoning_effort": config["reasoning_effort"]},
                              key, config["reasoning_tokens"])
    settings = Settings(
        sessions_dir=str(DATA / "dialogue-sessions"), model_dir="",
        token=config["token"], normal=normal, reasoning=reasoning,
        silence_ms=config["silence_ms"], vad_model=str(MODELS / "silero_vad.onnx"),
        tts_url=address(config, "tts"), tts_token=config["token"],
        persona_url=address(config, "registration"), persona_token=config["token"],
        context_tokens=config["context_tokens"], allow_test_mode=True,
        reaction_cache_dir=str(DATA / "reactions"),
        diagnostics_log=str(DATA / "logs" / "dialogue-diagnostics.jsonl"))
    frontend = OpenAIFrontend(key, config["stt_model"])
    llm = OpenAILLMClient(normal, reasoning, context_limit=settings.context_tokens)
    app = create_app(settings=settings, frontend=frontend, llm=llm)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        try:
            async with original(instance):
                yield
        finally:
            # create_app은 주입받은 자원의 소유권을 가지지 않는다.
            await frontend.aclose()
            await llm.close()

    app.router.lifespan_context = lifespan
    return app


def registration_app(config):
    from registration.app import create_app
    return create_app(root=DATA / "sessions", token=config["token"], persona_token=config["token"])


def tts_app(config):
    from tts_server import create_app
    from tts_vox import VoxVoice

    class LocalVoxVoice(VoxVoice):
        async def start(self):
            # 작은 텐서 연산이 많은 생성 루프가 Unity의 CPU 작업과 경쟁하지 않게 한다.
            import torch
            torch.set_num_threads(1)
            await super().start()
            # Unity와 GPU를 공유한다. 적재 중의 임시 버퍼는 계속 예약하지 않는다.
            configure_local_vox(self._vox._model)

        def _prepare_sync(self, pcm, rate, transcript):
            # 고정한 VoxCPM2 소스는 librosa.load(file-like)를 지원한다.
            # Linux /dev/shm 대신 RAM WAV를 건네므로 Windows에도 임시 음성을 쓰지 않는다.
            with io.BytesIO() as buffer:
                with wave.open(buffer, "wb") as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(rate)
                    out.writeframes(pcm)
                buffer.seek(0)
                prompt = self._vox.prepare(buffer, transcript)
            # 참조 음성 인코딩의 작업 공간도 재생 전 GPU에 남겨두지 않는다.
            release_cuda_cache()
            return prompt

    # 이 엔트리포인트의 자식 프로세스에만 적용한다. 기존 서버 env 파일은 읽지 않는다.
    os.environ["TTS_VOX_MODEL"] = str(MODELS / "VoxCPM2")
    os.environ["TTS_VOX_SOURCE"] = str(VOX_SOURCE / "src")
    return create_app(voice=LocalVoxVoice(), token=config["token"])
