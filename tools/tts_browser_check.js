'use strict';
/* 서버 PCM 조각을 전부 모은 뒤에만 WAV로 재생하는 검사 클라이언트.
   브라우저는 /app.js 로 불러오고, Node 테스트는 StreamCollector·pcmToWav 만 쓴다.
   문장·PCM·식별자는 로그·저장소에 남기지 않고 메모리에만 둔다. */

var SAMPLE_RATE = 24000;
var CHANNELS = 1;
var FORMAT = 'pcm_s16le';
var MAX_PACKET_SAMPLES = 4800;              // 바이트로는 9600이다
var MAX_LINE = 20000;                       // NDJSON 한 줄과 잔여 버퍼의 상한
var MAX_TOTAL_SAMPLES = SAMPLE_RATE * 45;
var MAX_BODY_BYTES = 8192;
var MAX_WAV_BYTES = 44 + MAX_TOTAL_SAMPLES * 2;
var MAX_WAV_PACKET_BYTES = 9600;            // 서버가 server_wav 한 조각에 담는 상한
var MAX_WAV_CHUNKS = Math.ceil(MAX_WAV_BYTES / MAX_WAV_PACKET_BYTES);
// A 경로는 이 프록시가 메모리에 둔 파일만 가리킨다. 외부 host·상위 경로는 여기서 막는다.
var SERVER_WAV_URL_RE = /^\/audio\/[a-f0-9]{32}\.wav$/;

var FAIL_TEXT = {
  bad_event: '서버 이벤트를 해석할 수 없습니다.',
  bad_order: '이벤트 순서가 계약과 다릅니다.',
  bad_audio: '오디오 조각이 계약과 다릅니다.',
  bad_format: '오디오 형식이 계약과 다릅니다.',
  too_long: '허용 길이를 넘는 음원입니다.',
  truncated: '완료 이벤트 없이 연결이 끊겼습니다.',
  mismatch: '완료 이벤트의 합계가 받은 조각과 다릅니다.',
  hash_mismatch: '전체 PCM 해시가 서버 값과 다릅니다.',
  bad_server_wav: '완료 이벤트의 서버 WAV 정보가 계약과 다릅니다.',
  server_wav_fetch: '서버가 만든 WAV 를 받지 못했습니다.',
  server_wav_format: '서버가 만든 WAV 헤더가 계약과 다릅니다.',
  server_wav_bytes: '서버가 만든 WAV 길이가 완료 이벤트와 다릅니다.',
  server_wav_hash: '서버가 만든 WAV 의 전체 해시가 완료 이벤트와 다릅니다.',
  server_wav_diff: '두 경로의 WAV 전체 바이트가 다릅니다.',
  no_stream: '이 브라우저에서 응답 스트림을 읽을 수 없습니다.',
  body_too_large: '요청 본문이 상한(8192바이트)을 넘습니다.',
  server_error: '서버가 오류를 보고했습니다.'
};

// 서버 error.code 는 프록시 ERROR_CODES 와 같은 목록만 번역한다. 원문 예외는 화면에 쓰지 않는다.
var SERVER_TEXT = {
  unity_active: 'Unity 체험 중이라 생성하지 않았습니다. 체험을 끝낸 뒤 다시 시도하세요.',
  reference_failed: '참조 음성을 준비하지 못했습니다.',
  service_unavailable: '대화 서버나 TTS 엔진을 쓸 수 없습니다.',
  ssh_failed: '원격 실행에 실패했습니다.',
  incomplete: '완료 이벤트 없이 원격 작업이 끝났습니다.',
  timeout: '제한 시간 안에 끝나지 않았습니다.',
  cancelled: '서버가 요청을 취소했습니다.',
  cleanup_failed: '임시 자원 정리에 실패했습니다. 서버에서 확인이 필요합니다.',
  invalid_audio: '서버가 보낸 오디오가 계약과 다릅니다.',
  too_long: '허용 길이를 넘는 음원입니다.',
  bad_request: '요청 형식이 올바르지 않습니다.',
  bad_host: '허용되지 않은 접속 주소입니다.',
  bad_origin: '허용되지 않은 접속 경로입니다.',
  too_large: '요청 본문이 상한을 넘습니다.',
  busy: '서버가 다른 생성을 처리하는 중입니다.',
  tts_failed: '서버에서 음성 생성이 실패했습니다.',
  server_error: '서버가 오류를 보고했습니다.'
};

var STATUS_TEXT = {
  400: '서버가 요청을 거부했습니다. (400)',
  403: '허용되지 않은 접속 경로입니다. (403)',
  409: '서버가 다른 생성을 처리하는 중입니다. (409)',
  413: '요청 본문이 상한을 넘습니다. (413)',
  415: '요청 Content-Type 이 올바르지 않습니다. (415)',
  500: '서버 내부 오류입니다. (500)',
  503: 'TTS 엔진을 쓸 수 없습니다. (503)'
};

function failure(code, extra) {
  var e = new Error((FAIL_TEXT[code] || '검사에 실패했습니다.') + (extra ? ' ' + extra : ''));
  e.code = code;
  throw e;
}

function isInt(v, min, max) {
  return typeof v === 'number' && isFinite(v) && Math.floor(v) === v && v >= min && v <= max;
}

function isMs(v) {
  return typeof v === 'number' && isFinite(v) && v >= 0;
}

// atob/Buffer 모두 잘못된 입력을 조용히 넘기므로 형태와 길이를 먼저 확인한다.
function decodeBase64(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length % 4 !== 0) return null;
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value)) return null;
  var pad = value.charAt(value.length - 1) === '=' ? (value.charAt(value.length - 2) === '=' ? 2 : 1) : 0;
  var expected = (value.length / 4) * 3 - pad;
  var out;
  if (typeof atob === 'function') {
    var bin;
    try { bin = atob(value); } catch (e) { return null; }
    out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i) & 0xff;
  } else {
    out = new Uint8Array(Buffer.from(value, 'base64'));
  }
  return out.length === expected ? out : null;
}

function StreamCollector() {
  this.rest = '';
  this.stage = null;
  this.started = null;
  this.done = null;
  this.serverErrorCode = null;
  this.parts = [];
  this.chunkCount = 0;
  this.totalSamples = 0;
  this.lastSeq = 0;
  this.boundaries = [];
  this.heartbeats = 0;
}

// 조각 경계에서 줄이 나뉘어도, 여러 줄이 한 번에 와도 같은 결과가 되게 모은다.
StreamCollector.prototype.pushText = function (text) {
  if (!text) return;
  this.rest += text;
  var lines = this.rest.split('\n');
  this.rest = lines.pop();
  for (var i = 0; i < lines.length; i++) this._line(lines[i]);
  if (this.rest.length > MAX_LINE) failure('bad_event');   // 개행 없이 길어지는 줄도 거부한다
};

// 마지막 줄에 개행이 없는 경우를 처리한다.
StreamCollector.prototype.endText = function () {
  var last = this.rest;
  this.rest = '';
  this._line(last);
};

StreamCollector.prototype._line = function (raw) {
  var line = raw.replace(/\r$/, '').trim();
  if (!line) return;
  if (line.length > MAX_LINE) failure('bad_event');
  var ev;
  try { ev = JSON.parse(line); } catch (e) { failure('bad_event'); }
  if (!ev || typeof ev.type !== 'string') failure('bad_event');
  if (ev.type === 'heartbeat') { this.heartbeats += 1; return; }
  if (ev.type === 'status') {
    if (ev.stage === 'preparing' || ev.stage === 'generating') this.stage = ev.stage;
    return;
  }
  if (ev.type === 'started') return this._started(ev);
  if (ev.type === 'audio') return this._audio(ev);
  if (ev.type === 'boundary') return this._boundary(ev);
  if (ev.type === 'done') return this._done(ev);
  if (ev.type === 'error') {
    this.serverErrorCode = typeof ev.code === 'string' ? ev.code : '';
    failure('server_error');
  }
  failure('bad_event');   // 계약에 없는 이벤트는 받지 않는다
};

StreamCollector.prototype._checkShape = function (ev) {
  if (ev.sample_rate !== SAMPLE_RATE || ev.channels !== CHANNELS || ev.format !== FORMAT) failure('bad_format');
};

StreamCollector.prototype._started = function (ev) {
  if (this.started || this.done) failure('bad_order');
  this._checkShape(ev);
  if (ev.mode !== 'phrase' && ev.mode !== 'whole') failure('bad_event');
  if (!isInt(ev.phrases, 1, 10000)) failure('bad_event');
  this.started = { mode: ev.mode, phrases: ev.phrases };
};

StreamCollector.prototype._audio = function (ev) {
  if (!this.started || this.done) failure('bad_order');
  this._checkShape(ev);
  if (!isInt(ev.seq, 1, 1e9) || ev.seq !== this.lastSeq + 1) failure('bad_audio');
  if (!isInt(ev.samples, 1, MAX_PACKET_SAMPLES)) failure('bad_audio');
  var bytes = decodeBase64(ev.pcm);
  if (!bytes) failure('bad_audio');
  if (bytes.length % 2 !== 0 || bytes.length / 2 !== ev.samples) failure('bad_audio');
  if (this.totalSamples + ev.samples > MAX_TOTAL_SAMPLES) failure('too_long');
  this.parts.push(bytes);
  this.chunkCount += 1;
  this.lastSeq = ev.seq;
  this.totalSamples += ev.samples;
};

StreamCollector.prototype._boundary = function (ev) {
  if (!this.started || this.done) failure('bad_order');
  if (!isInt(ev.phrase, 1, 10000)) failure('bad_event');
  if (!isInt(ev.samples, 0, MAX_TOTAL_SAMPLES)) failure('bad_event');
  if (!isInt(ev.chars, 0, 100000)) failure('bad_event');
  // 경계는 같은 NDJSON 순서로 오므로 순번은 1부터 이어지고 누계는 지금까지 받은 샘플과 같아야 한다.
  if (ev.phrase !== this.boundaries.length + 1) failure('bad_order');
  if (ev.samples !== this.totalSamples) failure('mismatch');
  this.boundaries.push({ phrase: ev.phrase, samples: ev.samples, chars: ev.chars });
};

StreamCollector.prototype._done = function (ev) {
  if (!this.started || this.done) failure('bad_order');
  if (!isInt(ev.samples, 1, MAX_TOTAL_SAMPLES) || !isInt(ev.chunks, 1, 1e9)) failure('bad_event');
  if (typeof ev.sha256 !== 'string' || !/^[0-9a-fA-F]{64}$/.test(ev.sha256)) failure('bad_event');
  if (!isMs(ev.first_pcm_ms) || !isMs(ev.total_ms)) failure('bad_event');
  if (ev.samples !== this.totalSamples || ev.chunks !== this.chunkCount) failure('mismatch');
  if (this.boundaries.length !== this.started.phrases) failure('mismatch');
  if (hasServerWav(ev)) checkServerWavMeta(ev);   // 레거시 done 은 A 정보 없이도 받는다
  this.done = ev;
};

// 스트림이 끝난 뒤 호출한다. 통과하면 이어 붙인 원본 PCM 을 돌려준다.
StreamCollector.prototype.finish = function () {
  if (!this.done) failure('truncated');
  var pcm = new Uint8Array(this.totalSamples * 2);
  var at = 0;
  for (var i = 0; i < this.parts.length; i++) { pcm.set(this.parts[i], at); at += this.parts[i].length; }
  if (at !== pcm.length) failure('mismatch');
  return pcm;
};

StreamCollector.prototype.checkHash = function (hex) {
  if (!this.done) failure('truncated');
  if (String(hex).toLowerCase() !== this.done.sha256.toLowerCase()) failure('hash_mismatch');
};

function writeAscii(view, at, text) {
  for (var i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i));
}

// 44바이트 헤더만 붙인다. 무음 추가·정규화·리샘플은 하지 않는다.
function pcmToWav(pcm, sampleRate, channels) {
  var rate = sampleRate || SAMPLE_RATE;
  var ch = channels || CHANNELS;
  var out = new Uint8Array(44 + pcm.length);
  var view = new DataView(out.buffer);
  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + pcm.length, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, ch, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * ch * 2, true);
  view.setUint16(32, ch * 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, 'data');
  view.setUint32(40, pcm.length, true);
  out.set(pcm, 44);
  return out;
}

function hasServerWav(done) {
  return !!done && (done.server_wav_bytes !== undefined || done.server_wav_chunks !== undefined
    || done.server_wav_sha256 !== undefined || done.server_wav_url !== undefined);
}

function checkServerWavUrl(url) {
  if (typeof url !== 'string' || !SERVER_WAV_URL_RE.test(url)) failure('bad_server_wav');
  return url;
}

// done 의 A 필드는 타입·상한·URL 형태까지 본 뒤에만 쓴다.
function checkServerWavMeta(done) {
  if (!done || typeof done !== 'object') failure('bad_server_wav');
  if (!isInt(done.server_wav_bytes, 46, MAX_WAV_BYTES)) failure('bad_server_wav');
  if (!isInt(done.server_wav_chunks, 1, MAX_WAV_CHUNKS)) failure('bad_server_wav');
  // 워커는 고정 9600바이트로 나누므로 조각 수는 한 값으로 정해진다.
  if (done.server_wav_chunks !== Math.ceil(done.server_wav_bytes / MAX_WAV_PACKET_BYTES)) {
    failure('bad_server_wav');
  }
  // 해시는 소문자 hex 로만 받는다. 아래 비교가 소문자 hexOf 와 직접 맞춰지기 때문이다.
  if (typeof done.server_wav_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(done.server_wav_sha256)) {
    failure('bad_server_wav');
  }
  checkServerWavUrl(done.server_wav_url);
  return done;
}

// A 로 받은 파일을 헤더 → done 길이 → B 조립본 전체 바이트 순서로 본다.
function checkServerWavBytes(actual, expectedWav, done) {
  if (!(actual instanceof Uint8Array) || !(expectedWav instanceof Uint8Array)) failure('bad_server_wav');
  if (actual.length < 44) failure('server_wav_format');
  var view = new DataView(actual.buffer, actual.byteOffset, actual.byteLength);
  function tag(at, text) {
    for (var i = 0; i < text.length; i++) if (view.getUint8(at + i) !== text.charCodeAt(i)) return false;
    return true;
  }
  if (!tag(0, 'RIFF') || !tag(8, 'WAVE') || !tag(12, 'fmt ') || !tag(36, 'data')) failure('server_wav_format');
  if (view.getUint32(16, true) !== 16 || view.getUint16(20, true) !== 1) failure('server_wav_format');
  if (view.getUint16(22, true) !== CHANNELS || view.getUint32(24, true) !== SAMPLE_RATE) failure('server_wav_format');
  if (view.getUint16(34, true) !== 16) failure('server_wav_format');
  if (view.getUint32(40, true) !== actual.length - 44) failure('server_wav_format');
  if (view.getUint32(4, true) !== actual.length - 8) failure('server_wav_format');
  if (actual.length !== done.server_wav_bytes) failure('server_wav_bytes');
  if (actual.length !== expectedWav.length) failure('server_wav_diff');
  for (var j = 0; j < actual.length; j++) if (actual[j] !== expectedWav[j]) failure('server_wav_diff');
  return true;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    StreamCollector: StreamCollector,
    pcmToWav: pcmToWav,
    decodeBase64: decodeBase64,
    hasServerWav: hasServerWav,
    checkServerWavUrl: checkServerWavUrl,
    checkServerWavMeta: checkServerWavMeta,
    checkServerWavBytes: checkServerWavBytes
  };
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', function () {
    var el = {};
    ['text', 'mode', 'start', 'cancel', 'state', 'audio', 'download', 'audio-server', 'download-server',
     'detail', 'n-chunks', 'n-seconds', 'n-elapsed',
     'baseline-load', 'baseline-state', 'audio-baseline'].forEach(function (id) { el[id] = document.getElementById(id); });

    var generation = 0;      // 늦은 콜백과 오래된 done 을 버리는 기준
    var controller = null;
    var objectUrl = null;
    var fileName = '';
    var serverUrl = '';      // A 는 Blob 이 아니라 HTTP 완성 파일이다
    var serverFileName = '';
    var baselineGeneration = 0;   // C 전용. A/B 의 generation 과 섞지 않는다
    var baselineController = null;

    function setState(text, kind) {
      el.state.textContent = text;
      el.state.className = 'state' + (kind ? ' ' + kind : '');
    }

    function setBusy(busy) {
      el.text.disabled = busy;
      el.mode.disabled = busy;
      el.start.disabled = busy;
      el.cancel.disabled = !busy;
    }

    function clearResult() {
      el.audio.pause();
      el.audio.removeAttribute('src');
      el.audio.load();
      el['audio-server'].pause();
      el['audio-server'].removeAttribute('src');
      el['audio-server'].load();
      serverUrl = '';
      serverFileName = '';
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
      el.download.disabled = true;
      el['download-server'].disabled = true;
      el['n-chunks'].textContent = '-';
      el['n-seconds'].textContent = '-';
      el['n-elapsed'].textContent = '-';
      el.detail.textContent = '';
    }

    // C 상태는 A/B 생성 상태(el.state)를 덮어쓰지 않는 별도 줄에만 쓴다.
    function setBaselineState(text, kind) {
      el['baseline-state'].textContent = text;
      el['baseline-state'].className = 'state' + (kind ? ' ' + kind : '');
    }

    function clearBaseline() {
      el['audio-baseline'].pause();
      el['audio-baseline'].removeAttribute('src');
      el['audio-baseline'].load();
    }

    // 기존 파일을 받을 수 있는지만 확인하고, 재생은 같은 HTTP URL 로 브라우저가 직접 한다.
    function loadBaseline() {
      var mine = ++baselineGeneration;
      if (baselineController) baselineController.abort();
      baselineController = new AbortController();
      el['baseline-load'].disabled = true;
      fetch('/baseline.wav', { signal: baselineController.signal, cache: 'no-store' })
        .then(function (res) {
          if (!res.ok) throw new Error('baseline');
          return res.arrayBuffer();
        }).then(function (buf) {
          if (mine !== baselineGeneration) return;
          // 본문은 상한만 확인하고 버린다. Blob 을 만들지 않는다.
          if (!buf || buf.byteLength < 44 || buf.byteLength > MAX_WAV_BYTES) throw new Error('baseline');
          el['audio-baseline'].src = '/baseline.wav';   // 자동 재생하지 않는다
          setBaselineState('예전 음원 준비 완료. C를 들어 보세요.', 'ok');
        }).catch(function (e) {
          if (mine !== baselineGeneration) return;
          if (e && e.name === 'AbortError') return;
          clearBaseline();
          setBaselineState('비교 파일을 불러오지 못했습니다.', 'fail');
        }).then(function () {
          if (mine !== baselineGeneration) return;
          baselineController = null;
          el['baseline-load'].disabled = false;
        });
    }

    function addDetail(key, value) {
      var dt = document.createElement('dt');
      dt.textContent = key;
      var dd = document.createElement('dd');
      dd.textContent = value;
      el.detail.appendChild(dt);
      el.detail.appendChild(dd);
    }

    function stageText(stage) {
      if (stage === 'preparing') return '서버 생성 준비 중';
      if (stage === 'generating') return '서버 생성 중';
      return '요청 보냄';
    }

    function hexOf(buffer) {
      var b = new Uint8Array(buffer), s = '';
      for (var i = 0; i < b.length; i++) s += (b[i] < 16 ? '0' : '') + b[i].toString(16);
      return s;
    }

    function httpMessage(status, code) {
      return SERVER_TEXT[code] || STATUS_TEXT[status] || '서버가 요청을 거부했습니다. (HTTP ' + status + ')';
    }

    // 사용자에게는 정해진 문구만 보여준다. 원문 예외 메시지는 쓰지 않는다.
    function failText(collector, e) {
      if (e && e.code === 'server_error') {
        return SERVER_TEXT[collector.serverErrorCode] || FAIL_TEXT.server_error;
      }
      if (e && e.code === 'http') return e.message;      // httpMessage 가 만든 고정 문구
      return (e && FAIL_TEXT[e.code]) || '알 수 없는 오류입니다.';
    }

    function run() {
      var mine = ++generation;
      var mode = el.mode.value;
      var body = JSON.stringify({ text: el.text.value, mode: mode });
      if (new TextEncoder().encode(body).length > MAX_BODY_BYTES) {
        setState(FAIL_TEXT.body_too_large, 'fail');
        return;
      }
      clearResult();
      setBusy(true);
      setState('요청 보냄');
      controller = new AbortController();

      var collector = new StreamCollector();
      var t0 = performance.now();
      var firstRecvMs = null;

      fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        signal: controller.signal
      }).then(function (res) {
        if (!res.ok) {
          return res.text().then(function (raw) {
            var code = '';
            try { var j = JSON.parse(raw); if (j && typeof j.code === 'string') code = j.code; } catch (e) {}
            var err = new Error(httpMessage(res.status, code));
            err.code = 'http';
            throw err;
          });
        }
        if (!res.body || !res.body.getReader) failure('no_stream');
        var reader = res.body.getReader();
        var decoder = new TextDecoder('utf-8');

        function step() {
          return reader.read().then(function (r) {
            if (mine !== generation) return null;      // 세대가 바뀌면 조용히 버린다
            if (r.done) {
              collector.pushText(decoder.decode());
              collector.endText();
              return collector.finish();
            }
            collector.pushText(decoder.decode(r.value, { stream: true }));
            // status·heartbeat 가 아니라 실제 첫 PCM 조각을 받은 시점만 잰다.
            if (firstRecvMs === null && collector.chunkCount > 0) firstRecvMs = performance.now() - t0;
            setState(stageText(collector.stage) + ' — 조각 ' + collector.chunkCount + '개, '
              + (collector.totalSamples / SAMPLE_RATE).toFixed(2) + '초 수신');
            return step();
          });
        }
        return step();
      }).then(function (pcm) {
        if (mine !== generation || !pcm) return null;
        var signal = controller ? controller.signal : undefined;
        return crypto.subtle.digest('SHA-256', pcm).then(function (digest) {
          if (mine !== generation) return null;
          collector.checkHash(hexOf(digest));
          var wav = pcmToWav(pcm, SAMPLE_RATE, CHANNELS);
          var meta = checkServerWavMeta(collector.done);   // A 정보 없이는 비교가 성립하지 않는다
          setState('서버에서 완성한 WAV 확인 중');
          return fetch(meta.server_wav_url, { signal: signal, cache: 'no-store' }).then(function (res) {
            if (!res.ok) failure('server_wav_fetch');
            return res.arrayBuffer();
          }).then(function (buf) {
            if (mine !== generation) return null;          // 지난 요청의 응답으로 덮어쓰지 않는다
            var serverWav = new Uint8Array(buf);
            checkServerWavBytes(serverWav, wav, meta);
            return crypto.subtle.digest('SHA-256', serverWav).then(function (serverDigest) {
              if (mine !== generation) return null;
              if (hexOf(serverDigest) !== meta.server_wav_sha256) failure('server_wav_hash');

              objectUrl = URL.createObjectURL(new Blob([wav], { type: 'audio/wav' }));
              fileName = 'tts_check_browser_' + collector.started.mode + '_' + Date.now() + '.wav';
              serverUrl = meta.server_wav_url;
              serverFileName = 'tts_check_server_' + collector.started.mode + '_' + Date.now() + '.wav';
              el['audio-server'].src = serverUrl;   // A: 서버가 만든 완성 파일을 HTTP 로 재생
              el.audio.src = objectUrl;             // B: 브라우저가 조립한 Blob. 자동 재생하지 않는다
              el['download-server'].disabled = false;
              el.download.disabled = false;

              var elapsed = (performance.now() - t0) / 1000;
              el['n-chunks'].textContent = String(collector.chunkCount);
              el['n-seconds'].textContent = (collector.totalSamples / SAMPLE_RATE).toFixed(2);
              el['n-elapsed'].textContent = elapsed.toFixed(2);
              addDetail('생성 방식', collector.started.mode === 'phrase' ? '문장별 생성' : '전체 한 번에 생성');
              addDetail('서버가 알린 문장 수', String(collector.started.phrases));
              addDetail('받은 문장 경계 수', String(collector.boundaries.length));
              addDetail('서버 생성 첫 PCM (ms)', collector.done.first_pcm_ms.toFixed(1));
              addDetail('서버 생성 전체 (ms)', collector.done.total_ms.toFixed(1));
              addDetail('브라우저 첫 PCM 수신 (ms)', firstRecvMs === null ? '-' : firstRecvMs.toFixed(1));
              addDetail('전체 샘플 수', String(collector.done.samples));
              addDetail('PCM sha256', collector.done.sha256.toLowerCase());
              addDetail('A WAV 바이트', String(meta.server_wav_bytes));
              addDetail('A WAV 조각 수', String(meta.server_wav_chunks));
              addDetail('A WAV sha256', meta.server_wav_sha256);
              addDetail('A·B 전체 바이트', '같음 (' + wav.length + '바이트)');
              setState('같은 음원 준비 완료. A와 B를 번갈아 들어 보세요.', 'ok');
              return null;
            });
          });
        });
      }).catch(function (e) {
        if (mine !== generation) return;          // 지난 요청의 오류로 새 요청을 끊지 않는다
        clearResult();                            // 불완전 음원은 재생하지 않는다
        if (e && e.name === 'AbortError') { setState('취소했습니다.'); return; }
        if (controller) controller.abort();       // 프로토콜 오류면 원격 생성도 끊는다
        setState('실패: ' + failText(collector, e), 'fail');
      }).then(function () {
        if (mine !== generation) return;
        controller = null;
        setBusy(false);
      });
    }

    el.start.addEventListener('click', run);
    el.cancel.addEventListener('click', function () {
      generation += 1;                            // 해시 계산 중 취소해도 새 URL 을 만들지 않는다
      if (controller) { controller.abort(); controller = null; }
      clearResult();
      setBusy(false);
      setState('취소했습니다.');
    });
    el.download.addEventListener('click', function () {
      if (!objectUrl) return;
      var a = document.createElement('a');
      a.href = objectUrl;                        // 재생본과 같은 PCM
      a.download = fileName;
      a.click();
    });
    el['download-server'].addEventListener('click', function () {
      if (!serverUrl) return;
      var a = document.createElement('a');
      a.href = serverUrl;                        // A 재생본과 같은 파일
      a.download = serverFileName;
      a.click();
    });
    el['baseline-load'].addEventListener('click', loadBaseline);
    // 세 경로를 겹쳐 듣지 않게 한쪽이 재생되면 나머지를 멈춘다.
    el.audio.addEventListener('play', function () { el['audio-server'].pause(); el['audio-baseline'].pause(); });
    el['audio-server'].addEventListener('play', function () { el.audio.pause(); el['audio-baseline'].pause(); });
    el['audio-baseline'].addEventListener('play', function () { el.audio.pause(); el['audio-server'].pause(); });
    window.addEventListener('pagehide', function () {
      generation += 1;
      if (controller) { controller.abort(); controller = null; }
      clearResult();
      baselineGeneration += 1;
      if (baselineController) { baselineController.abort(); baselineController = null; }
      clearBaseline();
    });
  });
}
