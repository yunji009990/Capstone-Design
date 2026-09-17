"""CPU dialogue orchestration; LLM and TTS run in independent services."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import anyio
import httpx
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from realtime_audio import TurnDetector, create_frontend, frontend_name
from realtime_dialogue import (PAUSE_GRACE_DEFAULT_MS, Dialogue, build_persona,
                               clamp_pause_grace, read_persona)
from persona_client import PersonaClient
from persona_context import PROMPT_VERSION
from dialogue_memory import MEMORY_VERSION, SessionMemory
from dialogue_reactions import REACTION_VERSION, ReactionCache
from dialogue_system_lines import SYSTEM_LINE_VERSION, SystemLineCache, fallback_lines
from realtime_llm import LLMClient, ModelEndpoint
from realtime_tts import TTSClient
from dialogue_diagnostics import (DIAGNOSTICS_VERSION, ConnectionDiagnostics,
                                  active as diagnostics_active,
                                  configure as configure_diagnostics)
from speech_gate import GATE_VERSION, SileroSpeechGate
from voice_reference import MAX_UPLOAD, ReferenceStore, decode_reference, session_recording

log = logging.getLogger(__name__)


def json_env(name, default):
    value = json.loads(os.environ.get(name, "") or json.dumps(default))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


@dataclass
class Settings:
    sessions_dir: str
    model_dir: str
    token: str = ""
    normal: ModelEndpoint = field(default_factory=lambda: ModelEndpoint(
        "http://127.0.0.1:8001/v1/chat/completions", "exaone"))
    reasoning: ModelEndpoint | None = None
    max_connections: int = 1
    silence_ms: int = 700
    vad_model: str = ""
    vad_threshold: float = .7
    min_speech_ms: int = 200
    diagnostics_log: str = ""
    allow_test_mode: bool = False
    tts_url: str = ""
    tts_token: str = ""
    persona_url: str = ""
    persona_token: str = ""
    memory_enabled: bool = True
    context_tokens: int = 8192
    reactions_enabled: bool = True
    reaction_cache_dir: str = ""
    # 기존 위치 인자 호환을 위해 뒤에 추가한다. 운영 기본은 Whisper GPU 다.
    stt_backend: str = "whisper"
    stt_device: str = "cuda"
    stt_compute_type: str = "float16"
    # 안내 문장 사전 준비. 끄면 짧은 기본 문장을 쓴다.
    system_lines_enabled: bool = True
    # 끼어들기로 답변을 바꿀 때 클라이언트가 구절 경계를 찾을 여유(ms). 0..1500 으로 자른다.
    pause_grace_ms: int = PAUSE_GRACE_DEFAULT_MS

    @classmethod
    def from_env(cls):
        base = Path.home()
        backend = os.environ.get("DIALOGUE_STT_BACKEND", "whisper").strip().lower()
        # 되돌리기로 sensevoice 를 명시했을 때만 기존 모델 폴더를 기본값으로 쓴다.
        default_model = base / "dialogue-models" / (
            "sensevoice" if backend == "sensevoice" else "whisper-large-v3")
        url = os.environ.get("DIALOGUE_LLM_URL", "http://127.0.0.1:8001/v1/chat/completions")
        model = os.environ.get("DIALOGUE_LLM_MODEL", "exaone")
        normal = ModelEndpoint(url, model, json_env("DIALOGUE_LLM_EXTRA", {
            "chat_template_kwargs": {"enable_thinking": False},
            "skip_special_tokens": False}),
            os.environ.get("DIALOGUE_LLM_KEY", ""),
            int(os.environ.get("DIALOGUE_ANSWER_TOKENS", "256")))
        reasoning = None
        reason_model = os.environ.get("DIALOGUE_REASON_MODEL", "")
        if reason_model:
            reasoning = ModelEndpoint(os.environ.get("DIALOGUE_REASON_URL", url), reason_model,
                json_env("DIALOGUE_REASON_EXTRA", {
                    "chat_template_kwargs": {"enable_thinking": True},
                    "skip_special_tokens": False}),
                os.environ.get("DIALOGUE_REASON_KEY", normal.key),
                int(os.environ.get("DIALOGUE_REASON_TOKENS", "2048")))
        return cls(
            os.environ.get("DIALOGUE_SESSIONS_DIR", str(base / "server" / "sessions")),
            os.environ.get("DIALOGUE_MODEL_DIR", str(default_model)),
            os.environ.get("DIALOGUE_TOKEN", ""),
            normal, reasoning, int(os.environ.get("DIALOGUE_MAX_CONNECTIONS", "1")),
            int(os.environ.get("DIALOGUE_SILENCE_MS", "700")),
            os.environ.get("DIALOGUE_VAD_MODEL",
                           str(base / "dialogue-models" / "silero" / "silero_vad.onnx")),
            float(os.environ.get("DIALOGUE_VAD_THRESHOLD", "0.7")),
            int(os.environ.get("DIALOGUE_MIN_SPEECH_MS", "200")),
            os.environ.get("DIALOGUE_DIAGNOSTICS_LOG", ""),
            os.environ.get("DIALOGUE_ALLOW_TEST_MODE", "0") == "1",
            os.environ.get("DIALOGUE_TTS_URL", ""), os.environ.get("DIALOGUE_TTS_TOKEN", ""),
            os.environ.get("DIALOGUE_PERSONA_URL", ""), os.environ.get("DIALOGUE_PERSONA_TOKEN", ""),
            os.environ.get("DIALOGUE_MEMORY_ENABLED", "1") == "1",
            int(os.environ.get("DIALOGUE_CONTEXT_TOKENS", "8192")),
            os.environ.get("DIALOGUE_REACTIONS_ENABLED", "1") == "1",
            os.environ.get("DIALOGUE_REACTION_CACHE_DIR", str(base / "capstone-server" / "reaction-cache")),
            backend, os.environ.get("DIALOGUE_STT_DEVICE", "cuda").strip().lower(),
            os.environ.get("DIALOGUE_STT_COMPUTE_TYPE", "float16").strip().lower(),
            os.environ.get("DIALOGUE_SYSTEM_LINES_ENABLED", "1") == "1",
            clamp_pause_grace(os.environ.get("DIALOGUE_PAUSE_GRACE_MS", PAUSE_GRACE_DEFAULT_MS)))


def create_app(settings=None, frontend=None, llm=None, detector_factory=None, tts=None,
               personas=None, speech_gate=None):
    settings = settings or Settings.from_env()
    owns_frontend, owns_llm = frontend is None, llm is None
    owns_gate = speech_gate is None
    active = set()
    references = ReferenceStore()
    reference_gate = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal reference_gate
        reference_gate = asyncio.Lock()
        # 전역 basicConfig 를 건드리지 않고 전용 로거에만 회전 핸들러를 붙인다.
        configure_diagnostics(settings.diagnostics_log)
        # 뒤의 준비가 실패해도 이미 만든 자원을 정리할 수 있게 먼저 비워 둔다.
        app.state.frontend = app.state.speech_gate = app.state.llm = None
        app.state.tts = app.state.personas = app.state.reactions = None
        app.state.system_lines = None
        try:
            # 모델 적재와 예열을 여기서 끝낸다. 실패하면 health 가 ready 를 말하기 전에
            # 시작이 중단된다. 다른 백엔드로 자동 대체하지 않는다.
            app.state.frontend = frontend or await asyncio.to_thread(
                create_frontend, settings.stt_backend, settings.model_dir,
                device=settings.stt_device, compute_type=settings.stt_compute_type)
            # 음성 검증기는 필수다. 모델이 없으면 예전의 민감한 동작으로 돌아가지 않고
            # 준비 오류로 시작을 거절한다.
            app.state.speech_gate = speech_gate or await asyncio.to_thread(
                SileroSpeechGate, settings.vad_model, threshold=settings.vad_threshold,
                min_speech_ms=settings.min_speech_ms)
            app.state.llm = llm or LLMClient(settings.normal, settings.reasoning, context_limit=settings.context_tokens)
            app.state.tts = tts or (TTSClient(settings.tts_url, settings.tts_token) if settings.tts_url else None)
            app.state.personas = personas or (PersonaClient(settings.persona_url, settings.persona_token)
                                            if settings.persona_url else None)
            app.state.reactions = ReactionCache(settings.reaction_cache_dir) if settings.reactions_enabled else None
            app.state.system_lines = SystemLineCache() if settings.system_lines_enabled else None
            yield
        finally:
            # 정상 종료와 시작 실패가 같은 경로로 한 번만 정리한다. 주입받은 자원은
            # 이 서버가 소유하지 않으므로 닫지 않는다.
            for connection in list(active):
                await connection.close("server_shutdown", 1001)
            if owns_frontend and app.state.frontend is not None:
                await asyncio.to_thread(app.state.frontend.close)
            if owns_gate and app.state.speech_gate is not None:
                await asyncio.to_thread(app.state.speech_gate.close)
            if owns_llm and app.state.llm is not None:
                await app.state.llm.close()
            if app.state.tts and tts is None:
                await app.state.tts.close()
            if app.state.personas and personas is None:
                await app.state.personas.close()

    app = FastAPI(lifespan=lifespan)

    def auth(value):
        if settings.token and not hmac.compare_digest(value, settings.token):
            raise HTTPException(403, "인증 실패")

    async def registered_voice(session=None, bundle=None):
        if app.state.personas:
            bundle = bundle or await app.state.personas.get(session)
            return await app.state.personas.voice(bundle)
        return await asyncio.to_thread(session_recording, settings.sessions_dir, session)

    async def prepare_reference(raw, source):
        async with reference_gate:
            reference = await asyncio.to_thread(decode_reference, raw, source)
            observation = await app.state.frontend.transcribe(reference.pcm, reference.sample_rate)
            reference.text = observation.text.strip()
            if not reference.text:
                raise ValueError("참조 음성을 인식하지 못했습니다. 말소리가 선명한 WAV를 선택하세요.")
            return reference

    @app.post("/references")
    async def upload_reference(voice: UploadFile = File(None), x_token: str = Header("")):
        auth(x_token)
        if not settings.allow_test_mode:
            raise HTTPException(403, "참조 음성 선택은 테스트 모드에서 사용할 수 있습니다.")
        try:
            if voice is not None and voice.filename:
                raw = await voice.read(MAX_UPLOAD + 1)
                source = "업로드 · " + Path(voice.filename.replace("\\", "/")).name[:120]
            else:
                raw, source = await registered_voice()
            if len(raw) > MAX_UPLOAD:
                raise HTTPException(413, "참조 WAV는 30 MB 이하여야 합니다.")
            reference = await prepare_reference(raw, source)
            return references.put(reference).details()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/references/{reference_id}/audio.wav")
    async def reference_audio(reference_id: str, x_token: str = Header("")):
        auth(x_token)
        try:
            return Response(references.get(reference_id).wav(), media_type="audio/wav",
                            headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/references/{reference_id}")
    async def delete_reference(reference_id: str, x_token: str = Header("")):
        auth(x_token)
        references.delete(reference_id)
        return {"released": True}

    @app.get("/health")
    async def health():
        llm_ready = await app.state.llm.available()
        reason_ready = (await app.state.llm.available(settings.reasoning)
                        if settings.reasoning else False)
        tts_ready = await app.state.tts.available() if app.state.tts else False
        return {"status": "ready" if llm_ready and (not app.state.tts or tts_ready) else "degraded",
                "mode": "streaming_voice" if app.state.tts else "streaming_text",
                "stt": frontend_name(app.state.frontend), "llm": settings.normal.model,
                "llm_ready": llm_ready, "reasoning_configured": bool(settings.reasoning),
                "reasoning_ready": reason_ready, "connections": len(active),
                "tts": bool(app.state.tts), "tts_ready": tts_ready,
                "tts_streaming": getattr(app.state.tts, "streaming_mode", None),
                "tts_voice_mode": getattr(app.state.tts, "voice_mode", "reference_icl") if app.state.tts else None,
                "interruption_policy": "semantic_v1",
                # 낱말 한가운데 절단을 피하는 경계 정렬 계약. 실제 정렬은 클라이언트가 한다.
                "pause_boundary": {"grace_ms": settings.pause_grace_ms,
                                   "ack_event": "playback.paused",
                                   "modes": ["phrase", "immediate"]},
                "diagnostics": {"version": DIAGNOSTICS_VERSION,
                                # 설정 문자열이 있다는 뜻이 아니라 실제로 파일을 열었는지다.
                                "file": diagnostics_active()},
                "input_gate": app.state.speech_gate.settings()
                if hasattr(app.state.speech_gate, "settings")
                else {"version": GATE_VERSION, "acoustic": None},
                "prompt_version": PROMPT_VERSION,
                "memory": {"enabled": settings.memory_enabled and callable(getattr(app.state.llm, "extract_memory", None)),
                           "version": MEMORY_VERSION, "scope": "connection"},
                "reactions": {"enabled": bool(app.state.reactions and app.state.tts
                              and callable(getattr(app.state.llm, "generate_reactions", None))),
                              "version": REACTION_VERSION},
                "system_lines": {"enabled": bool(app.state.system_lines and callable(
                                 getattr(app.state.llm, "generate_system_lines", None))),
                                 "version": SYSTEM_LINE_VERSION},
                "persona_source": "http" if settings.persona_url else "legacy_files",
                "test_mode_available": settings.allow_test_mode}

    @app.websocket("/dialogue")
    async def dialogue_socket(ws: WebSocket):
        if settings.token and not hmac.compare_digest(ws.headers.get("x-token", ""), settings.token):
            await ws.close(code=1008)
            return
        await ws.accept()
        connection = None
        bound_voice = None
        send_lock = asyncio.Lock()
        # 닫힘 사유와 WebSocket close code. 경로마다 채워 두고 finally 에서 한 번만 남긴다.
        closing = {"reason": "close", "code": 0}

        async def emit(event):
            async with send_lock:
                await asyncio.wait_for(ws.send_json(event), timeout=3)

        try:
            first = await asyncio.wait_for(ws.receive(), timeout=10)
            if first["type"] == "websocket.disconnect":
                return
            raw_hello = first.get("text")
            if not isinstance(raw_hello, str) or len(raw_hello) > 8192:
                raise ValueError("expected a JSON start message")
            hello = json.loads(raw_hello)
            if (not isinstance(hello, dict) or hello.get("type") != "start"
                    or hello.get("protocol") != 1 or hello.get("sample_rate") != 16000
                    or hello.get("channels") != 1 or hello.get("format") != "pcm_s16le"):
                raise ValueError("expected protocol 1 start: mono PCM16 at 16000 Hz")
            test_mode = hello.get("test_mode", False)
            if app.state.tts and hello.get("interruption_policy") != "semantic_v1":
                raise ValueError("재생 일시정지·재개를 지원하는 최신 Unity 클라이언트로 업데이트해 주세요.")
            if not isinstance(test_mode, bool):
                raise ValueError("test_mode must be a boolean")
            persona_bundle = None
            if test_mode:
                if not settings.allow_test_mode:
                    raise ValueError("서버에서 테스트 모드를 켜야 합니다: DIALOGUE_ALLOW_TEST_MODE=1")
                persona = hello.get("test_persona")
                if not isinstance(persona, str) or not persona.strip() or len(persona) > 3000:
                    raise ValueError("test_persona must contain 1 to 3000 characters")
                # Authenticated, connection-local profile: no registration or disk writes.
                persona_text = persona.strip()
                system, examples = build_persona(persona_text)
            else:
                if hello.get("reference_id") or hello.get("reference_text"):
                    raise ValueError("등록 체험은 해당 세션의 참조 음성을 사용합니다.")
                if app.state.personas:
                    persona_bundle = await app.state.personas.get(
                        PersonaClient.valid_session(hello.get("session")))
                    persona_text = persona_bundle["persona"]
                    # 등록 체험은 고인을 기억으로 다시 만나는 자리다. 이미 등록된 인물도
                    # 재접속만으로 같은 전제를 갖도록 여기서 넣는다(인물 파일은 고치지 않는다).
                    system, examples = build_persona(persona_text,
                        persona_bundle["knowledge"], persona_bundle["rules"], memorial=True)
                else:
                    texts = read_persona(settings.sessions_dir, hello.get("session"))
                    persona_text = texts[0]
                    system, examples = build_persona(*texts, memorial=True)
            if len(active) >= settings.max_connections:
                await emit({"type": "error", "code": "server_busy",
                            "message": "다른 체험이 진행 중입니다. 잠시 후 다시 시작해주세요."})
                await ws.close(code=1013)
                return
            detector = (detector_factory() if detector_factory else
                        TurnDetector(silence_ms=settings.silence_ms))
            memory = (SessionMemory(app.state.llm.extract_memory, getattr(app.state.llm, "resolve_memory_forget", None))
                      if settings.memory_enabled and callable(getattr(app.state.llm, "extract_memory", None)) else None)
            # 클라이언트가 보낸 가명 추적 ID 는 선택 항목이다. 형식이 어긋나면 서버가 새로 만든다.
            diagnostics = ConnectionDiagnostics(hello.get("trace_id", ""))
            diagnostics.event("connection.open", "test" if test_mode else "registered")
            connection = Dialogue(system, examples, app.state.frontend, app.state.llm,
                                  emit, detector=detector, speech_gate=app.state.speech_gate,
                                  diagnostics=diagnostics,
                                  pause_ack=hello.get("pause_ack") is True,
                                  pause_grace_ms=settings.pause_grace_ms,
                                  semantic_interruptions=hello.get("interruption_policy") == "semantic_v1", memory=memory)
            active.add(connection)
            reference_info = None
            reaction_info = {"version": REACTION_VERSION, "ready": False, "count": 0}
            # 안내 문장은 성격·말투만으로 준비하고 음성·텍스트 대화에 함께 쓴다.
            # TTS 유무와 무관하므로 아래 음성 준비 블록 밖에서 먼저 정한다.
            # 생성 입력은 리액션과 같은 경계(persona 만)이고, 폴백의 존대/반말 판정은
            # 공통 규칙 문장이 섞이지 않은 raw persona 원문에서 읽는다.
            # memorial 을 켜지 않으므로 [재회] 규칙과 사망 경위가 대기 리액션·안내 문장
            # 생성 입력으로 새지 않는다.
            profile = build_persona(persona_text)[0]
            system_line_info = {"version": SYSTEM_LINE_VERSION, "ready": False, "source": "fallback"}
            connection.system_lines = fallback_lines(persona_text)
            make_lines = getattr(app.state.llm, "generate_system_lines", None)
            if app.state.system_lines is not None and callable(make_lines):
                try:
                    connection.system_lines, system_line_info = await asyncio.wait_for(
                        app.state.system_lines.prepare(profile, make_lines, generator_identity=[
                            settings.normal.url, settings.normal.model, settings.normal.extra]),
                        timeout=20)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # 준비 실패가 체험 시작을 막지 않는다. 이 연결은 폴백 문장을 쓴다.
                    log.warning("system line preparation skipped: %s", type(error).__name__)
                    system_line_info["reason"] = "preparation_failed"
            if app.state.tts:
                await emit({"type": "voice.preparing", "message": "참조 목소리와 말투를 준비하고 있습니다."})
                if test_mode and hello.get("reference_id"):
                    reference = references.get(hello["reference_id"])
                else:
                    raw, source = await registered_voice(
                        None if test_mode else hello["session"], persona_bundle)
                    reference = await prepare_reference(raw, source)
                reference_text = hello.get("reference_text") if test_mode else None
                # Unity serializes an unset string as "". In that case use the
                # transcription of the actual selected/cropped recording.
                if reference_text == "":
                    reference_text = None
                if reference_text is not None and (not isinstance(reference_text, str)
                        or not reference_text.strip() or len(reference_text) > 2000):
                    raise ValueError("참조 음성의 전사를 1–2000자로 입력하세요.")
                bound_voice = await app.state.tts.bind(reference, reference_text)
                connection.tts = bound_voice
                reference_info = reference.details()
                reference_info["text"] = reference_text or reference.text
                generate = getattr(app.state.llm, "generate_reactions", None)
                if app.state.reactions is not None and callable(generate):
                    # 리액션은 성격·말투만으로 준비한다. 등록 지식·새 근황·언급 조건이 담기는
                    # knowledge·rules와 사용자 대화는 생성 입력에 넣지 않는다.
                    # 본 대화의 시스템 프롬프트(system)는 그대로 셋을 모두 쓴다.
                    scope = ("test" if test_mode else
                             [settings.persona_url or settings.sessions_dir, hello["session"]])
                    await emit({"type": "voice.preparing", "message": "캐릭터의 짧은 리액션을 준비하고 있습니다."})
                    try:
                        identity = getattr(app.state.tts, "reaction_identity", None)
                        synthesis = await identity() if callable(identity) else [settings.tts_url]
                        connection.reactions, reaction_info = await asyncio.wait_for(
                            app.state.reactions.prepare(profile, scope, reference, reference_info["text"],
                                bound_voice, generate, persistent=not test_mode,
                                generator_identity=[settings.normal.url, settings.normal.model, settings.normal.extra],
                                synthesis_identity=synthesis), timeout=35)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        # 리액션 준비 실패는 본 대화의 시작을 막지 않는다. 다음 연결에서 재시도한다.
                        log.warning("reaction preparation skipped: %s", type(error).__name__)
                        reaction_info["reason"] = "preparation_failed"
            await emit({"type": "ready", "protocol": 1, "sample_rate": 16000,
                        "session": "test" if test_mode else hello["session"],
                        "persona_revision": persona_bundle["revision"] if persona_bundle else None,
                        "prompt_version": PROMPT_VERSION,
                        "memory": {"enabled": memory is not None, "version": MEMORY_VERSION, "scope": "connection"},
                        "reactions": reaction_info,
                        "system_lines": system_line_info,
                        "test_mode": test_mode, "tts": bool(app.state.tts),
                        "voice_mode": (getattr(bound_voice, "voice_mode", None)
                          or getattr(app.state.tts, "voice_mode", None)
                          or "reference_icl") if bound_voice else None,
                        "interruption_policy": "semantic_v1" if connection.semantic_interruptions else "cancel",
                        # 멈춘 지점을 보고하겠다고 선언한 클라이언트에만 경계 정렬을 쓴다.
                        "pause_boundary": {"enabled": connection.pause_ack,
                                           "grace_ms": connection.pause_grace_ms,
                                           "ack_event": "playback.paused",
                                           "modes": ["phrase", "immediate"]},
                        "reference": reference_info,
                        "reasoning_available": bool(settings.reasoning)})
            while True:
                packet = await asyncio.wait_for(ws.receive(), timeout=20)
                if packet["type"] == "websocket.disconnect":
                    # 정상 끊김도 예외로 오지 않는다. 여기서 사유와 코드를 남긴다.
                    closing["reason"] = "disconnect"
                    code = packet.get("code")
                    closing["code"] = code if isinstance(code, int) else 1005
                    break
                if packet.get("bytes") is not None:
                    await connection.audio(packet["bytes"])
                    continue
                text = packet.get("text", "")
                if len(text) > 8192:
                    raise ValueError("control message too large")
                control = json.loads(text)
                if not isinstance(control, dict):
                    raise ValueError("control message must be an object")
                kind = control.get("type")
                if kind == "context":
                    connection.context.update(control)
                elif kind == "text" and test_mode:
                    await connection.text(control.get("text"))
                elif kind == "memory.inspect" and test_mode:
                    if connection.memory is None:
                        raise ValueError("memory is disabled")
                    await emit({"type": "memory.snapshot", "status": connection.memory.status(),
                                "facts": connection.memory.fact_rows(max_chars=12000),
                                "summary": connection.memory.summary()})
                elif kind == "memory.flush" and test_mode:
                    if connection.memory is None:
                        raise ValueError("memory is disabled")
                    idle = connection.active is None and connection.held is None
                    if idle:
                        connection.memory.kick(force=True)
                    await emit({"type": "memory.scheduled", "idle": idle})
                elif kind == "cancel":
                    await connection.interrupt("client_cancel")
                elif kind in ("playback.progress", "playback.done", "playback.paused"):
                    connection.playback(control)
                elif kind == "reset":
                    await connection.reset()
                elif kind == "stop":
                    closing["reason"], closing["code"] = "client_stop", 1000
                    await connection.close("client_stop", 1000)
                    await emit({"type": "stopped"})
                    break
                elif kind == "ping":
                    # 마이크가 죽어도 ping 은 계속 온다. 살아 있는지 여기서 확인한다.
                    if connection.diagnostics is not None:
                        connection.diagnostics.heartbeat()
                    await emit({"type": "pong"})
                else:
                    raise ValueError("unknown control message type")
        except WebSocketDisconnect:
            closing["reason"] = "disconnect"
            if closing["code"] == 0:
                closing["code"] = 1005
        except (ValueError, asyncio.TimeoutError) as exc:
            # 클라이언트로 가는 코드와 close code 는 그대로 두고 로그 사유만 구분한다.
            closing["reason"] = "receive_timeout" if isinstance(exc, asyncio.TimeoutError) else "protocol_error"
            closing["code"] = 1008
            try:
                await emit({"type": "error", "code": "protocol_error", "message": str(exc)})
                await ws.close(code=1008)
            except (WebSocketDisconnect, RuntimeError):
                pass
        except httpx.HTTPError:
            closing["reason"], closing["code"] = "voice_error", 1011
            log.exception("TTS reference preparation failed")
            with suppress(WebSocketDisconnect, RuntimeError):
                await emit({"type": "error", "code": "voice_error",
                            "message": "참조 음성을 준비하지 못했습니다. TTS 서버 상태를 확인해 주세요."})
                await ws.close(code=1011)
        finally:
            if connection is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await connection.close(closing["reason"], closing["code"])
                    finally:
                        active.discard(connection)
                        if bound_voice is not None:
                            with suppress(httpx.HTTPError, asyncio.TimeoutError):
                                await asyncio.wait_for(bound_voice.release(), timeout=10)

    return app


app = create_app()
