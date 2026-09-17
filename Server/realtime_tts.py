"""Model-independent phrase segmentation and cancellable TTS HTTP client."""
import base64
import json
import re

import httpx


class PhraseBuffer:
    def __init__(self):
        self.pending = ""

    def push(self, delta, final=False):
        self.pending += delta
        out = []
        while self.pending.strip():
            # Wait for following whitespace so a delta ending in "3." does not
            # split the next delta's "14" into a second spoken number.
            match = re.search(r"[.!?。！？](?=\s)|\n", self.pending)
            cut = match.end() if match else 0
            if (not cut or cut > 100) and len(self.pending) >= 100:
                cut = self.pending.rfind(" ", 20, 100)
                if cut < 0:
                    cut = 100
            if not cut and final:
                cut = len(self.pending)
            if not cut:
                break
            out.append(self.pending[:cut])
            self.pending = self.pending[cut:]
        return out


# 닫는 따옴표·괄호까지 포함해 문장 끝으로 본다. 길이 제한으로만 끊긴
# 구절은 문장 끝으로 취급하지 않는다.
_SENTENCE_END = re.compile(r"[.!?。！？…][\"'”’」』）)\]】]*\s*$")


def ends_sentence(text):
    """구절이 한 문장을 끝맺었는가. 줄바꿈으로 끊긴 구절도 문장 끝으로 본다."""
    if text.endswith("\n"):
        return True
    return bool(_SENTENCE_END.search(text))


class TTSClient:
    def __init__(self, url, token=""):
        self.url = url.rstrip("/")
        self.streaming_mode = None
        self.voice_mode = None
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=5), trust_env=False,
                                      headers={"X-Token": token} if token else {})

    async def available(self):
        self.streaming_mode = None
        self.voice_mode = None
        try:
            r = await self.http.get(self.url + "/health", timeout=3)
            info = r.json()
            # 참조 기반 모드만 허용한다. reference_icl은 Qwen(참조 음성+전사),
            # reference_audio는 VoxCPM2(참조 전용)다. 그 밖의 모드는 거부한다.
            ready = (r.status_code == 200 and info.get("status") == "ready"
                     and info.get("voice_mode") in ("reference_icl", "reference_audio"))
            if ready:
                self.streaming_mode = info.get("streaming")
                self.voice_mode = info.get("voice_mode")
            return ready
        except (httpx.HTTPError, ValueError):
            return False

    async def bind(self, reference, text=None):
        response = await self.http.post(self.url + "/voices", json={
            "pcm": base64.b64encode(reference.pcm).decode(),
            "sample_rate": reference.sample_rate, "text": text or reference.text})
        response.raise_for_status()
        payload = response.json()
        # mode를 주지 않는 기존 Qwen 응답·모의 서버는 reference_icl로 본다.
        return BoundVoice(self, payload["voice_id"],
                          payload.get("voice_mode") or self.voice_mode or "reference_icl")

    async def reaction_identity(self):
        response = await self.http.get(self.url + "/health", timeout=3)
        response.raise_for_status()
        info = response.json()
        return [self.url, info.get("model"), info.get("streaming"), info.get("voice_mode")]

    async def stream(self, text, voice_id):
        complete = False
        async with self.http.stream("POST", self.url + "/synthesize",
                                    json={"text": text, "voice_id": voice_id}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                if len(line) > 20000:
                    raise ValueError("Oversized TTS packet")
                data = json.loads(line)
                if data["type"] == "audio":
                    pcm = base64.b64decode(data["pcm"], validate=True)
                    if data["sample_rate"] != 24000 or not pcm or len(pcm) > 9600 or len(pcm) % 2:
                        raise ValueError("Invalid TTS audio format")
                    yield data["pcm"], len(pcm) // 2
                elif data["type"] == "error":
                    raise RuntimeError(data["message"])
                elif data["type"] == "done":
                    complete = True
        if not complete:
            raise RuntimeError("TTS stream ended before completion")

    async def close(self):
        await self.http.aclose()


class BoundVoice:
    """A connection's reference prompt, shared by all its answer phrases."""
    def __init__(self, client, voice_id, voice_mode="reference_icl"):
        self.client, self.voice_id = client, voice_id
        self.voice_mode = voice_mode

    def stream(self, text):
        return self.client.stream(text, self.voice_id)

    async def release(self):
        response = await self.client.http.delete(self.client.url + "/voices/" + self.voice_id)
        response.raise_for_status()
