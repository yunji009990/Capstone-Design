"""Unity 시작·착석 입력 제어를 검사하는 loopback 전용 모의 서버."""
import argparse
import base64
import hashlib
import json
import pathlib
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description='Unity preparation integration fixture (loopback only).')
parser.add_argument('--model', type=pathlib.Path, default=ROOT / 'tools/_work/body_shape_preview/current_model.private.glb')
args = parser.parse_args()
MODEL = args.model.resolve()
if not MODEL.is_file():
    parser.error('A local combined GLB is required. Use --model PATH.')
FOLDER = ROOT / 'tools/_work/experience_preparation'
FOLDER.mkdir(parents=True, exist_ok=True)
state = dict(model_status=404, model_delay=0.0, ready_delay=0.0,
             audio_packets=0, starts=0, downloads=0)
lock = threading.Lock()


def exact(stream, size):
    result = b''
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if not chunk:
            raise EOFError
        result += chunk
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass  # 검사 취소·종료로 끊긴 로컬 소켓은 정상적인 정리다.
    def log_message(self, *args):
        pass

    def send_json(self, value):
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != '/control':
            self.send_error(404)
            return
        config = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with lock:
            for key in ('model_status', 'model_delay', 'ready_delay'):
                if key in config:
                    state[key] = config[key]
        self.send_json({'ok': True})

    def do_GET(self):
        if self.path == '/health':
            self.send_json(dict(status='ready', mode='streaming_voice', tts=False, test_mode_available=True))
        elif self.path == '/stats':
            with lock:
                snapshot = dict(state)
            self.send_json(snapshot)
        elif self.path == '/session/current':
            self.send_json(dict(session='preparation-validation', has_model=True))
        elif self.path.endswith('/model.glb'):
            with lock:
                code, delay = state['model_status'], state['model_delay']
                state['downloads'] += 1
            time.sleep(delay)
            if code != 200:
                self.send_error(code)
                return
            data = MODEL.read_bytes()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == '/dialogue' and self.headers.get('Upgrade', '').lower() == 'websocket':
            self.websocket()
        else:
            self.send_error(404)

    def websocket(self):
        key = self.headers['Sec-WebSocket-Key']
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101)
        self.send_header('Upgrade', 'websocket')
        self.send_header('Connection', 'Upgrade')
        self.send_header('Sec-WebSocket-Accept', accept)
        self.end_headers()

        def send(message):
            data = json.dumps(message).encode()
            header = bytes([0x81, len(data)]) if len(data) < 126 else bytes([0x81, 126]) + struct.pack('!H', len(data))
            self.wfile.write(header + data)
            self.wfile.flush()

        try:
            while True:
                first, second = exact(self.rfile, 2)
                opcode, length = first & 15, second & 127
                if length == 126:
                    length = struct.unpack('!H', exact(self.rfile, 2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', exact(self.rfile, 8))[0]
                mask = exact(self.rfile, 4) if second & 128 else None
                data = exact(self.rfile, length)
                if mask:
                    data = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
                if opcode == 8:
                    break
                if opcode == 2:
                    with lock:
                        state['audio_packets'] += 1
                elif opcode == 1:
                    message = json.loads(data)
                    if message.get('type') == 'start':
                        with lock:
                            state['starts'] += 1
                            delay = state['ready_delay']
                        time.sleep(delay)
                        send(dict(type='ready', tts=False, voice_mode='disabled'))
                    elif message.get('type') == 'ping':
                        send(dict(type='pong'))
        except (EOFError, OSError, ValueError):
            pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
(FOLDER / 'mock.json').write_text(json.dumps(dict(url=f'http://127.0.0.1:{server.server_port}')), encoding='utf-8')
print('Local readiness fixture listening.', flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
