# -*- coding: utf-8 -*-
"""프롬프트 변형을 서버에 등록한다. **서버에서 실행한다.**

목소리는 지금 세션의 것을 그대로 쓴다 - 실존 인물의 음성이라 내려받지 않는다.
페르소나·사전지식만 갈아끼운다.

    python3 register.py <persona.md> <knowledge.md> [세션ID]
"""
import sys, json, uuid, urllib.request

URL = "http://127.0.0.1:8000"
TOK = "23605a891e448b5aa46f82c8640b554c"
VOICE = "/home/crc_unity/server/sessions/test_0906_150338/voice.wav"


def form(fields, files):
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                   + v.encode() + b"\r\n")
    for k, (name, data, ctype) in files.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; '
                   f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
                   + data + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def main():
    persona = open(sys.argv[1], encoding="utf-8").read()
    knowledge = open(sys.argv[2], encoding="utf-8").read()
    sid = sys.argv[3] if len(sys.argv) > 3 else "test_0906_150338"
    body, ctype = form(
        {"persona": persona, "knowledge": knowledge, "session": sid},
        {"voice": ("voice.wav", open(VOICE, "rb").read(), "audio/wav")})
    req = urllib.request.Request(URL + "/session/start", data=body,
                                 headers={"X-Token": TOK, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=300) as r:
        print(json.dumps(json.load(r), ensure_ascii=False))


if __name__ == "__main__":
    main()
