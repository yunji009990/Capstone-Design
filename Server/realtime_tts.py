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


class TTSClient:
    def __init__(self, url, token=""):
        self.url = url.rstrip("/")
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=5), trust_env=False,
                                      headers={"X-Token": token} if token else {})

    async def available(self):
        try:
            r = await self.http.get(self.url + "/health", timeout=3)
            return (r.status_code == 200 and r.json().get("status") == "ready"
                    and r.json().get("voice_mode") == "reference_icl")
        except (httpx.HTTPError, ValueError):
            return False

    async def bind(self, reference, text=None):
        response = await self.http.post(self.url + "/voices", json={
            "pcm": base64.b64encode(reference.pcm).decode(),
            "sample_rate": reference.sample_rate, "text": text or reference.text})
        response.raise_for_status()
        return BoundVoice(self, response.json()["voice_id"])

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
    def __init__(self, client, voice_id):
        self.client, self.voice_id = client, voice_id

    def stream(self, text):
        return self.client.stream(text, self.voice_id)

    async def release(self):
        response = await self.client.http.delete(self.client.url + "/voices/" + self.voice_id)
        response.raise_for_status()
