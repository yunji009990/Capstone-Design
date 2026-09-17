"""STT 모델 비교용 실제 마이크 음성을 사람이 직접 읽어 녹음한다.

표준 라이브러리만 사용하며 Windows에서 WinMM waveIn으로 16kHz mono PCM16을 캡처한다.
경고음·테스트톤·마이크 모니터링·자동 녹음·출력 레벨 변경은 하지 않는다.
사용 예: python tools/record_stt_samples.py --output .dialogue-work/stt-mic/20260916-jw
"""
import argparse
import ctypes
import hashlib
import json
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIN_SECONDS = 2
MAX_SECONDS = 15
WAVE_MAPPER = 0xFFFFFFFF
WHDR_DONE = 0x00000001
WAVERR_BADFORMAT = 32
CHUNK_SECONDS = 0.25
TIMEOUT_MARGIN = 2.0  # 마지막 버퍼가 채워질 여유. 벽시계 정각에 끊지 않는다.

# 비교용 대본: 인사·숫자/시간·유사 지명·가상 인물 이름·일상 문장·부정 표현 순서다.
SCRIPTS = [
    "안녕하세요 잘 들리세요",
    "내일 오후 세 시에 다시 이야기하자",
    "강남이 아니라 강릉에 다녀왔어",
    "친구 이름은 민수야",
    "오늘은 약속이 없어서 집에서 쉬었어",
    "아니 그 얘기 말고 아까 하던 얘기를 계속해줘",
]

# 정리에 실패한 네이티브 자원. 해제되지 않은 버퍼가 GC되면 use-after-free이므로 여기서 붙잡는다.
RETAINED_RESOURCES = []


class CaptureError(Exception):
    """장치 열기·포맷·캡처·정리 실패. 다른 오디오 포맷으로 대체하지 않는다."""


DWORD_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
HWAVEIN = ctypes.c_void_p
MMRESULT = ctypes.c_uint


class WaveFormatEx(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


class WaveHdr(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_char_p),
        ("dwBufferLength", ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32),
        ("dwUser", DWORD_PTR),
        ("dwFlags", ctypes.c_uint32),
        ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p),
        ("reserved", DWORD_PTR),
    ]


class WaveInCaps(ctypes.Structure):
    _fields_ = [
        ("wMid", ctypes.c_ushort),
        ("wPid", ctypes.c_ushort),
        ("vDriverVersion", ctypes.c_uint32),
        ("szPname", ctypes.c_wchar * 32),
        ("dwFormats", ctypes.c_uint32),
        ("wChannels", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
    ]


class WinMM:
    """winmm.dll waveIn 함수 묶음. 검사에서 같은 메서드의 가짜 객체로 대체할 수 있다."""

    def __init__(self, dll):
        self._dll = dll
        # mmeapi.h 기준 서명. uDeviceID는 waveInOpen이 UINT, waveInGetDevCapsW가 UINT_PTR이다.
        self._bind("waveInOpen", [ctypes.POINTER(HWAVEIN), ctypes.c_uint32,
                                  ctypes.POINTER(WaveFormatEx), DWORD_PTR, DWORD_PTR,
                                  ctypes.c_uint32])
        for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
            self._bind(name, [HWAVEIN, ctypes.POINTER(WaveHdr), ctypes.c_uint32])
        for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
            self._bind(name, [HWAVEIN])
        self._bind("waveInGetDevCapsW", [DWORD_PTR, ctypes.POINTER(WaveInCaps), ctypes.c_uint32])
        self._bind("waveInGetErrorTextW", [MMRESULT, ctypes.c_wchar_p, ctypes.c_uint32])
        dll.waveInGetNumDevs.argtypes = []
        dll.waveInGetNumDevs.restype = ctypes.c_uint32

    def _bind(self, name, argtypes):
        function = getattr(self._dll, name)
        function.argtypes = argtypes
        function.restype = MMRESULT

    def open(self, device_id, fmt):
        """(MMRESULT, handle)을 돌려준다. 콜백을 쓰지 않으므로 CALLBACK_NULL(0)이다."""
        handle = HWAVEIN()
        result = self._dll.waveInOpen(ctypes.byref(handle), device_id, ctypes.byref(fmt), 0, 0, 0)
        return result, handle

    def prepare(self, handle, header):
        return self._dll.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))

    def unprepare(self, handle, header):
        return self._dll.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))

    def add_buffer(self, handle, header):
        return self._dll.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header))

    def start(self, handle):
        return self._dll.waveInStart(handle)

    def stop(self, handle):
        return self._dll.waveInStop(handle)

    def reset(self, handle):
        return self._dll.waveInReset(handle)

    def close(self, handle):
        return self._dll.waveInClose(handle)

    def device_count(self):
        return self._dll.waveInGetNumDevs()

    def device_name(self, index):
        caps = WaveInCaps()
        if self._dll.waveInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)) != 0:
            return None
        return caps.szPname

    def error_text(self, code):
        buffer = ctypes.create_unicode_buffer(256)
        if self._dll.waveInGetErrorTextW(code, buffer, len(buffer)) == 0:
            return "%s (MMRESULT=%d)" % (buffer.value, code)
        return "MMRESULT=%d" % code


def load_winmm():
    """Windows에서만 winmm.dll을 지연 로드한다."""
    if sys.platform != "win32":
        raise CaptureError("이 녹음 도구는 Windows WinMM에서만 동작한다.")
    return WinMM(ctypes.WinDLL("winmm.dll"))


def list_devices(winmm=None):
    """장치 목록만 읽는다. 녹음·장치 열기를 하지 않는다."""
    api = winmm or load_winmm()
    devices = [("mapper", "WAVE_MAPPER (WinMM이 고르는 기본 입력)")]
    for index in range(api.device_count()):
        name = api.device_name(index)
        if name is not None:
            devices.append((str(index), name))
    return devices


def wave_format():
    return WaveFormatEx(
        wFormatTag=1,  # WAVE_FORMAT_PCM
        nChannels=CHANNELS,
        nSamplesPerSec=SAMPLE_RATE,
        nAvgBytesPerSec=SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH,
        nBlockAlign=CHANNELS * SAMPLE_WIDTH,
        wBitsPerSample=SAMPLE_WIDTH * 8,
        cbSize=0,
    )


def expected_bytes(seconds):
    return int(seconds * SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)


def validate_pcm(pcm, seconds):
    """무음은 잡음 검증에 쓸 수 있으므로 거르지 않는다. 빈/홀수/짧은 데이터만 거부한다."""
    if not pcm:
        raise CaptureError("녹음 데이터가 비어 있다.")
    if len(pcm) % SAMPLE_WIDTH:
        raise CaptureError("PCM16 경계에 맞지 않는 길이다: %d바이트" % len(pcm))
    if len(pcm) < expected_bytes(seconds):
        raise CaptureError("녹음이 %d바이트에 못 미친다: %d바이트"
                           % (expected_bytes(seconds), len(pcm)))
    return pcm


def _cleanup(api, handle, prepared, buffers, headers):
    """waveInReset(모든 버퍼를 done으로 반환) → Unprepare → Close. 실패를 성공으로 보고하지 않는다."""
    failures = []
    for name, call in (("waveInStop", lambda: api.stop(handle)),
                       ("waveInReset", lambda: api.reset(handle))):
        result = call()
        if result != 0:
            failures.append("%s: %s" % (name, api.error_text(result)))
    for header in prepared:
        result = api.unprepare(handle, header)
        if result != 0:
            failures.append("waveInUnprepareHeader: %s" % api.error_text(result))
    result = api.close(handle)
    if result != 0:
        failures.append("waveInClose: %s" % api.error_text(result))
    if not failures:
        return None
    # 드라이버가 아직 버퍼를 쥐고 있을 수 있다. 해제될 때까지 파이썬 객체를 살려 둔다.
    RETAINED_RESOURCES.append({"handle": handle, "buffers": buffers, "headers": headers})
    return CaptureError("녹음 장치를 정리하지 못했다. 이 프로세스를 종료한다: " + " / ".join(failures))


def capture_pcm(device_id, seconds, winmm=None):
    """지정 장치에서 16kHz mono PCM16을 seconds 만큼 캡처해 bytes로 돌려준다.

    CALLBACK_NULL로 열고 WHDR_DONE 폴링만 쓴다. 콜백 포인터가 없어 수명·경합 문제가 없고,
    버퍼는 seconds(<=15초) 분량을 미리 큐잉해 재큐잉하지 않는다.
    """
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise CaptureError("녹음 길이는 %d~%d초여야 한다: %r" % (MIN_SECONDS, MAX_SECONDS, seconds))
    api = winmm or load_winmm()
    result, handle = api.open(device_id, wave_format())
    if result == WAVERR_BADFORMAT:
        raise CaptureError("장치가 16kHz mono PCM16을 지원하지 않는다: %s" % api.error_text(result))
    if result != 0:
        raise CaptureError("마이크 장치를 열지 못했다: %s" % api.error_text(result))

    chunk = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * CHUNK_SECONDS)
    count = int(expected_bytes(seconds) / chunk)
    buffers = [ctypes.create_string_buffer(chunk) for _ in range(count)]
    headers = [WaveHdr(lpData=ctypes.cast(buf, ctypes.c_char_p), dwBufferLength=chunk)
               for buf in buffers]
    prepared = []
    pcm = None
    primary = None
    try:
        for header in headers:
            result = api.prepare(handle, header)
            if result != 0:
                raise CaptureError("녹음 버퍼 준비에 실패했다: %s" % api.error_text(result))
            prepared.append(header)
            result = api.add_buffer(handle, header)
            if result != 0:
                raise CaptureError("녹음 버퍼 등록에 실패했다: %s" % api.error_text(result))
        result = api.start(handle)
        if result != 0:
            raise CaptureError("녹음을 시작하지 못했다: %s" % api.error_text(result))
        deadline = time.monotonic() + seconds + TIMEOUT_MARGIN
        while not all(header.dwFlags & WHDR_DONE for header in headers):
            if time.monotonic() >= deadline:
                raise CaptureError("%d초 녹음이 %.0f초 안에 끝나지 않았다. 이번 클립은 실패로 둔다."
                                   % (seconds, seconds + TIMEOUT_MARGIN))
            time.sleep(0.05)
        pcm = validate_pcm(b"".join(buf.raw[:header.dwBytesRecorded]
                                    for buf, header in zip(buffers, headers)), seconds)
    except BaseException as error:  # 중단·장치 오류에도 정리는 수행한다.
        primary = error
    cleanup_error = _cleanup(api, handle, prepared, buffers, headers)
    if primary is not None:  # 첫 예외를 cleanup 예외로 덮지 않는다.
        if cleanup_error is not None:
            raise primary from cleanup_error
        raise primary
    if cleanup_error is not None:
        raise cleanup_error
    return pcm


def write_wav(path, pcm):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


def write_manifest(output, samples, device_id, seconds, completed):
    manifest = {
        "schema_version": 1,
        "completed": completed,
        "recorded_count": len(samples),
        "planned_count": len(SCRIPTS),
        "samples": samples,
        "capture": {
            "device_id": "WAVE_MAPPER" if device_id == WAVE_MAPPER else device_id,
            "backend": "winmm_wavein",
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "sample_width": SAMPLE_WIDTH,
            "duration_seconds": seconds,
            "format_note": "waveIn에 16kHz mono PCM16을 요청해 그 포맷으로 받는다는 뜻이다. "
                           "Windows·드라이버 내부의 리샘플링 여부까지 보장하지 않는다.",
            "device_note": "WAVE_MAPPER는 WinMM이 고른 입력이며 Unity의 현재 마이크와 같다는 보장이 없다. "
                           "--list-devices 로 확인해 같은 마이크 ID를 --device 로 지정한다.",
        },
    }
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def ask(prompt, question, allowed):
    """허용한 응답만 받는다. 임의 입력으로 녹음이 시작되지 않게 한다."""
    while True:
        answer = prompt(question).strip().lower()
        if answer in allowed:
            return answer
        print("%s 중에서 고른다." % ", ".join(name or "Enter" for name in allowed))


def _record_all(output, seconds, device_id, capture, prompt, samples, state):
    for index, reference in enumerate(SCRIPTS, start=1):
        sample_id = "mic_%03d" % index
        while True:
            print("[%d/%d] %s" % (index, len(SCRIPTS), reference))
            if ask(prompt, "Enter=녹음 시작, q=끝내기 > ", ("", "q")) == "q":
                return
            print("녹음 중... %d초" % seconds)
            pcm = capture(device_id, seconds)
            print("녹음 끝. %.1f초 분량." % (len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)))
            answer = ask(prompt, "Enter=저장, r=다시 녹음, q=끝내기 > ", ("", "r", "q"))
            if answer == "r":
                continue  # 저장 전이므로 이번 버퍼는 그대로 버린다.
            if answer == "q":
                return
            write_wav(output / (sample_id + ".wav"), pcm)
            samples.append({
                "id": sample_id,
                "audio": sample_id + ".wav",
                "reference": reference,
                "source": "user_microphone",
                "condition": "actual_mic",
                "bytes": len(pcm),
                "duration_seconds": round(len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH), 3),
                "sha256": hashlib.sha256(pcm).hexdigest(),
                "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            write_manifest(output, samples, device_id, seconds, False)
            print("저장했다: %s.wav\n" % sample_id)
            break
    state["completed"] = True


def run_session(output, seconds, device_id, capture=capture_pcm, prompt=input):
    """대본을 하나씩 읽어 녹음한다. 반환값은 (manifest, 종료 코드)."""
    print("대본 %d개를 순서대로 읽는다. 각 문장은 Enter를 누른 뒤 %d초 동안만 녹음된다."
          % (len(SCRIPTS), seconds))
    print("말이 끝나기 전에 %d초가 끊길 수 있으니 Enter 직후 바로 읽기 시작한다." % seconds)
    print("3개 이상 녹음하면 비교에 사용할 수 있다. 저장 위치: %s\n" % output)

    samples = []
    state = {"completed": False}
    try:
        _record_all(output, seconds, device_id, capture, prompt, samples, state)
    except (KeyboardInterrupt, EOFError):
        print("\n중단됨.")
    finally:
        # CaptureError로 빠져나갈 때도 부분 manifest를 남기고 원래 예외는 그대로 올린다.
        manifest = write_manifest(output, samples, device_id, seconds, state["completed"])
        print("녹음 %d개, completed=%s. manifest: %s"
              % (len(samples), str(state["completed"]).lower(), output / "manifest.json"))
        if len(samples) < 3:
            print("비교에는 3개 이상이 필요하다.")
    return manifest, 0 if state["completed"] else 1


def parse_args(argv):
    parser = argparse.ArgumentParser(description="STT 비교용 실제 마이크 음성 녹음")
    parser.add_argument("--output", type=Path, help="새 저장 폴더. 기존 폴더는 덮어쓰지 않는다.")
    parser.add_argument("--seconds", type=int, default=6,
                        help="문장별 자동 정지 시간 (%d~%d초)" % (MIN_SECONDS, MAX_SECONDS))
    parser.add_argument("--device", default="mapper", help="입력 장치 ID. 기본은 WAVE_MAPPER.")
    parser.add_argument("--list-devices", action="store_true", help="장치 목록만 출력한다.")
    args = parser.parse_args(argv)
    if not MIN_SECONDS <= args.seconds <= MAX_SECONDS:
        parser.error("--seconds 는 %d~%d 범위여야 한다." % (MIN_SECONDS, MAX_SECONDS))
    if args.device == "mapper":
        args.device_id = WAVE_MAPPER
    elif args.device.isdigit():
        args.device_id = int(args.device)
    else:
        parser.error("--device 는 mapper 또는 0 이상의 장치 ID다.")
    if not args.list_devices and args.output is None:
        parser.error("--output 이 필요하다.")
    return args


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한국어 대본을 그대로 출력한다.
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.list_devices:
            for device_id, name in list_devices():
                print("%s\t%s" % (device_id, name))
            print("Unity가 쓰는 마이크와 같은 장치를 --device 로 지정한다.")
            return 0
        if args.output.exists():
            print("이미 있는 폴더다. 새 이름을 쓴다: %s" % args.output, file=sys.stderr)
            return 2
        args.output.mkdir(parents=True)
        _, code = run_session(args.output, args.seconds, args.device_id)
        return code
    except CaptureError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
