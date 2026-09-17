'use strict';
// 프런트 수집기 단위 검사. 네트워크는 쓰지 않고 pushText 로만 NDJSON 을 먹인다.
// 실행: node tools/tests/test_tts_browser_client.cjs

const assert = require('assert');
const crypto = require('crypto');
const { StreamCollector, pcmToWav, checkServerWavUrl, checkServerWavMeta,
        checkServerWavBytes } = require('../tts_browser_check.js');

const RATE = 24000;
const HEX32 = 'ab12cd34'.repeat(4);
let passed = 0;

function run(name, fn) {
  try { fn(); passed += 1; console.log('ok   ' + name); }
  catch (e) { console.error('FAIL ' + name + ': ' + e.message); process.exitCode = 1; }
}

function throwsCode(code, fn) {
  try { fn(); } catch (e) { assert.strictEqual(e.code, code); return e; }
  assert.fail('실패하지 않았다: ' + code);
}

function b64(bytes) { return Buffer.from(bytes).toString('base64'); }
function shape(extra) {
  return Object.assign({ sample_rate: RATE, channels: 1, format: 'pcm_s16le' }, extra);
}
function audio(seq, bytes) {
  return shape({ type: 'audio', seq, samples: bytes.length / 2, pcm: b64(bytes) });
}
function toNdjson(events) { return events.map((e) => JSON.stringify(e)).join('\n'); }

function feed(collector, text, size) {
  for (let i = 0; i < text.length; i += size) collector.pushText(text.slice(i, i + size));
  collector.endText();
}

function makeGood() {
  const a = Buffer.from([1, 2, 3, 4, 5, 6]);
  const b = Buffer.from([7, 8, 250, 251]);
  const pcm = Buffer.concat([a, b]);
  const events = [
    { type: 'status', stage: 'preparing' },
    shape({ type: 'started', mode: 'phrase', phrases: 2 }),
    { type: 'status', stage: 'generating' },
    { type: 'heartbeat' },
    audio(1, a),
    { type: 'boundary', phrase: 1, samples: 3, chars: 12 },
    audio(2, b),
    { type: 'boundary', phrase: 2, samples: 5, chars: 9 },
    { type: 'done', samples: 5, chunks: 2, sha256: crypto.createHash('sha256').update(pcm).digest('hex'),
      first_pcm_ms: 412.5, total_ms: 980.25 }
  ];
  return { text: toNdjson(events), pcm, events };
}

function wavOf(pcm) { return Buffer.from(pcmToWav(pcm, RATE, 1)); }

function metaOf(wav, extra) {
  return Object.assign({
    server_wav_bytes: wav.length,
    server_wav_chunks: Math.ceil(wav.length / 9600) || 1,
    server_wav_sha256: crypto.createHash('sha256').update(wav).digest('hex'),
    server_wav_url: '/audio/' + HEX32 + '.wav'
  }, extra);
}

run('조각 경계가 어디서 잘려도 같은 PCM 을 모은다 (마지막 줄 개행 없음)', () => {
  const good = makeGood();
  for (const size of [1, 3, 7, 40, 5000]) {
    const c = new StreamCollector();
    feed(c, good.text, size);
    const pcm = c.finish();
    assert.deepStrictEqual(Buffer.from(pcm), good.pcm);
    c.checkHash(crypto.createHash('sha256').update(Buffer.from(pcm)).digest('hex'));
    assert.strictEqual(c.chunkCount, 2);
    assert.strictEqual(c.totalSamples, 5);
    assert.strictEqual(c.boundaries.length, 2);
    assert.strictEqual(c.heartbeats, 1);
    assert.strictEqual(c.stage, 'generating');
  }
});

run('마지막 줄에 개행이 있어도 통과한다', () => {
  const good = makeGood();
  const c = new StreamCollector();
  c.pushText(good.text + '\n');
  c.endText();
  assert.deepStrictEqual(Buffer.from(c.finish()), good.pcm);
});

run('WAV 는 44바이트 헤더 + 원본 PCM 과 정확히 같다', () => {
  const good = makeGood();
  const c = new StreamCollector();
  feed(c, good.text, 11);
  const pcm = c.finish();
  const wav = Buffer.from(pcmToWav(pcm, RATE, 1));
  assert.strictEqual(wav.length, 44 + pcm.length);
  assert.strictEqual(wav.slice(0, 4).toString('ascii'), 'RIFF');
  assert.strictEqual(wav.slice(8, 12).toString('ascii'), 'WAVE');
  assert.strictEqual(wav.readUInt32LE(24), RATE);
  assert.strictEqual(wav.readUInt16LE(22), 1);
  assert.strictEqual(wav.readUInt16LE(34), 16);
  assert.strictEqual(wav.readUInt32LE(40), pcm.length);
  assert.deepStrictEqual(wav.slice(44), good.pcm);
});

run('완료 이벤트 없는 EOF 는 실패다', () => {
  const c = new StreamCollector();
  feed(c, toNdjson([shape({ type: 'started', mode: 'whole', phrases: 1 }), audio(1, Buffer.from([1, 2]))]), 9);
  throwsCode('truncated', () => c.finish());
});

run('해시 불일치는 실패다', () => {
  const good = makeGood();
  const c = new StreamCollector();
  feed(c, good.text, 13);
  c.finish();
  throwsCode('hash_mismatch', () => c.checkHash('0'.repeat(64)));
});

run('깨진 base64·홀수 바이트·크기 불일치는 실패다', () => {
  const started = shape({ type: 'started', mode: 'whole', phrases: 1 });
  const bad = [
    shape({ type: 'audio', seq: 1, samples: 2, pcm: '@@@@' }),
    shape({ type: 'audio', seq: 1, samples: 2, pcm: 'AAA' }),
    shape({ type: 'audio', seq: 1, samples: 2, pcm: b64(Buffer.from([1, 2, 3])) }),
    shape({ type: 'audio', seq: 1, samples: 3, pcm: b64(Buffer.from([1, 2, 3, 4])) }),
    shape({ type: 'audio', seq: 1, samples: 4801, pcm: b64(Buffer.alloc(9602)) })
  ];
  for (const ev of bad) {
    const c = new StreamCollector();
    throwsCode('bad_audio', () => feed(c, toNdjson([started, ev]), 25));
  }
});

run('순번 불일치는 실패다', () => {
  const c = new StreamCollector();
  const text = toNdjson([
    shape({ type: 'started', mode: 'whole', phrases: 1 }),
    audio(1, Buffer.from([1, 2])),
    audio(3, Buffer.from([3, 4]))
  ]);
  throwsCode('bad_audio', () => feed(c, text, 17));
});

run('형식·샘플레이트·채널이 다르면 실패다', () => {
  const c1 = new StreamCollector();
  throwsCode('bad_format', () => feed(c1, JSON.stringify(
    { type: 'started', sample_rate: 16000, channels: 1, format: 'pcm_s16le', mode: 'whole', phrases: 1 }), 8));
  const c2 = new StreamCollector();
  throwsCode('bad_format', () => feed(c2, toNdjson([
    shape({ type: 'started', mode: 'whole', phrases: 1 }),
    { type: 'audio', sample_rate: RATE, channels: 2, format: 'pcm_s16le', seq: 1, samples: 1, pcm: b64(Buffer.from([1, 2])) }
  ]), 30));
});

run('started 중복·오디오 선행·done 이후 오디오는 실패다', () => {
  const started = shape({ type: 'started', mode: 'whole', phrases: 1 });
  const pcm = Buffer.from([1, 2]);
  const boundary = { type: 'boundary', phrase: 1, samples: 1, chars: 2 };
  const done = { type: 'done', samples: 1, chunks: 1,
    sha256: crypto.createHash('sha256').update(pcm).digest('hex'), first_pcm_ms: 1, total_ms: 2 };
  const cases = [
    [started, started],
    [audio(1, pcm)],
    [started, audio(1, pcm), boundary, done, audio(2, pcm)],
    [started, audio(1, pcm), boundary, done, done]
  ];
  for (const events of cases) {
    const c = new StreamCollector();
    throwsCode('bad_order', () => feed(c, toNdjson(events), 21));
  }
});

run('done 의 합계 불일치는 실패다', () => {
  const pcm = Buffer.from([1, 2, 3, 4]);
  const hash = crypto.createHash('sha256').update(pcm).digest('hex');
  for (const done of [{ samples: 3, chunks: 1 }, { samples: 2, chunks: 2 }]) {
    const c = new StreamCollector();
    const text = toNdjson([
      shape({ type: 'started', mode: 'whole', phrases: 1 }),
      audio(1, pcm),
      Object.assign({ type: 'done', sha256: hash, first_pcm_ms: 1, total_ms: 2 }, done)
    ]);
    throwsCode('mismatch', () => feed(c, text, 23));
  }
});

run('깨진 JSON 과 서버 error 이벤트를 구분한다', () => {
  const c1 = new StreamCollector();
  throwsCode('bad_event', () => feed(c1, '{"type":"status"', 5));
  const c2 = new StreamCollector();
  const e = throwsCode('server_error', () => feed(c2, JSON.stringify({ type: 'error', code: 'busy' }), 7));
  assert.strictEqual(c2.serverErrorCode, 'busy');
  assert.ok(e.message.length > 0);
});

run('조각 상한 4800샘플을 넘는 payload 는 거부한다', () => {
  const c = new StreamCollector();
  const text = toNdjson([
    shape({ type: 'started', mode: 'whole', phrases: 1 }),
    shape({ type: 'audio', seq: 1, samples: 4801, pcm: b64(Buffer.alloc(9602)) })
  ]);
  throwsCode('bad_audio', () => feed(c, text, 64));
});

run('음원이 하나도 없는 done 은 거부한다', () => {
  const c = new StreamCollector();
  const done = { type: 'done', samples: 0, chunks: 0,
    sha256: crypto.createHash('sha256').update(Buffer.alloc(0)).digest('hex'),
    first_pcm_ms: 0, total_ms: 1 };
  const text = toNdjson([shape({ type: 'started', mode: 'whole', phrases: 1 }), done]);
  throwsCode('bad_event', () => feed(c, text, 40));
});

run('상한을 넘는 줄과 계약에 없는 이벤트는 거부한다', () => {
  const c1 = new StreamCollector();
  throwsCode('bad_event', () => { c1.pushText('x'.repeat(20001) + '\n'); });
  const c2 = new StreamCollector();
  throwsCode('bad_event', () => { c2.pushText('x'.repeat(20001)); });   // 개행 없는 잔여도 본다
  const c3 = new StreamCollector();
  throwsCode('bad_event', () => feed(c3, JSON.stringify({ type: 'debug', raw: 'x' }), 9));
});

run('문장 경계의 순번·누계·개수를 검사한다', () => {
  const started = shape({ type: 'started', mode: 'phrase', phrases: 2 });
  const pcm = Buffer.from([1, 2, 3, 4]);
  const c1 = new StreamCollector();
  throwsCode('bad_order', () => feed(c1, toNdjson([started, audio(1, pcm),
    { type: 'boundary', phrase: 2, samples: 2, chars: 3 }]), 19));
  const c2 = new StreamCollector();
  throwsCode('mismatch', () => feed(c2, toNdjson([started, audio(1, pcm),
    { type: 'boundary', phrase: 1, samples: 5, chars: 3 }]), 19));
  const c3 = new StreamCollector();
  const done = { type: 'done', samples: 2, chunks: 1,
    sha256: crypto.createHash('sha256').update(pcm).digest('hex'), first_pcm_ms: 1, total_ms: 2 };
  throwsCode('mismatch', () => feed(c3, toNdjson([started, audio(1, pcm),
    { type: 'boundary', phrase: 1, samples: 2, chars: 3 }, done]), 19));
});

run('A URL 은 프록시 경로만 허용하고 상위 경로·외부 host 를 거부한다', () => {
  const ok = '/audio/' + HEX32 + '.wav';
  assert.strictEqual(checkServerWavUrl(ok), ok);
  const bad = [
    '/audio/../secret.wav',
    '/audio/' + HEX32 + '.wav/../x.wav',
    '/audio//' + HEX32 + '.wav',
    'http://evil.example/audio/' + HEX32 + '.wav',
    '//evil.example/audio/' + HEX32 + '.wav',
    'file:///audio/' + HEX32 + '.wav',
    '/other/' + HEX32 + '.wav',
    '/audio/' + HEX32 + '.wav?x=1',
    '/audio/' + HEX32.toUpperCase() + '.wav',
    '/audio/' + HEX32 + '.WAV',
    '/audio/' + 'a'.repeat(31) + '.wav',
    '/audio/' + 'a'.repeat(33) + '.wav',
    '', null, 7
  ];
  for (const url of bad) throwsCode('bad_server_wav', () => checkServerWavUrl(url));
});

run('done 의 A 메타는 타입·상한을 검사한다', () => {
  const wav = wavOf(Buffer.from([1, 2, 3, 4, 5, 6]));
  assert.strictEqual(checkServerWavMeta(metaOf(wav)).server_wav_bytes, wav.length);
  const bad = [
    { server_wav_bytes: 0 },
    { server_wav_bytes: 45 },
    { server_wav_bytes: 50.5 },
    { server_wav_bytes: '50' },
    { server_wav_bytes: undefined },
    { server_wav_chunks: 0 },
    { server_wav_chunks: 1.5 },
    { server_wav_chunks: undefined },
    { server_wav_chunks: 1000000 },                             // 전체 상한을 넘는 조각 수
    { server_wav_chunks: 2 },                                   // 작은 WAV 는 한 조각이다
    { server_wav_bytes: 20000, server_wav_chunks: 1 },          // 9600바이트 상한과 어긋난다
    { server_wav_sha256: 'zz' },
    { server_wav_sha256: 'a'.repeat(63) },
    { server_wav_sha256: 'A'.repeat(64) },                      // 소문자 hex 만 받는다
    { server_wav_sha256: undefined },
    { server_wav_url: '/audio/../etc/passwd.wav' },
    { server_wav_url: undefined }
  ];
  for (const extra of bad) throwsCode('bad_server_wav', () => checkServerWavMeta(metaOf(wav, extra)));
  throwsCode('bad_server_wav', () => checkServerWavMeta(null));
});

run('A 파일은 헤더·길이·전체 바이트가 모두 맞아야 통과한다', () => {
  const pcm = Buffer.from([1, 2, 3, 4, 5, 6]);
  const wav = wavOf(pcm);
  const meta = metaOf(wav);
  assert.strictEqual(checkServerWavBytes(new Uint8Array(wav), new Uint8Array(wav), meta), true);

  throwsCode('server_wav_format', () =>                          // WAV 가 아님
    checkServerWavBytes(new Uint8Array(wav.slice(0, 20)), new Uint8Array(wav), meta));
  const badTag = Buffer.from(wav); badTag.write('RIFX', 0, 'ascii');
  throwsCode('server_wav_format', () => checkServerWavBytes(new Uint8Array(badTag), new Uint8Array(wav), meta));
  const badRate = Buffer.from(wav); badRate.writeUInt32LE(16000, 24);
  throwsCode('server_wav_format', () => checkServerWavBytes(new Uint8Array(badRate), new Uint8Array(wav), meta));
  const badChannels = Buffer.from(wav); badChannels.writeUInt16LE(2, 22);
  throwsCode('server_wav_format', () => checkServerWavBytes(new Uint8Array(badChannels), new Uint8Array(wav), meta));
  const badData = Buffer.from(wav); badData.writeUInt32LE(4, 40);
  throwsCode('server_wav_format', () => checkServerWavBytes(new Uint8Array(badData), new Uint8Array(wav), meta));

  const longer = wavOf(Buffer.concat([pcm, Buffer.from([9, 9])]));   // done 길이와 다름
  throwsCode('server_wav_bytes', () => checkServerWavBytes(new Uint8Array(longer), new Uint8Array(longer), meta));

  const diff = Buffer.from(wav); diff[44] = wav[44] ^ 0xff;          // 길이는 같고 내용이 다름
  throwsCode('server_wav_diff', () => checkServerWavBytes(new Uint8Array(diff), new Uint8Array(wav), metaOf(diff)));
});

run('done 에 A 필드가 있으면 계약을 검사하고, 없으면 레거시로 받는다', () => {
  const good = makeGood();
  const wav = wavOf(good.pcm);
  const last = good.events[good.events.length - 1];
  const withA = good.events.slice(0, -1).concat([Object.assign({}, last, metaOf(wav))]);
  const c1 = new StreamCollector();
  feed(c1, toNdjson(withA), 31);
  assert.deepStrictEqual(Buffer.from(c1.finish()), good.pcm);
  assert.strictEqual(c1.done.server_wav_bytes, wav.length);
  assert.strictEqual(c1.done.server_wav_url, '/audio/' + HEX32 + '.wav');

  const badA = good.events.slice(0, -1).concat([Object.assign({}, last,
    metaOf(wav, { server_wav_url: '/audio/../etc/passwd.wav' }))]);
  const c2 = new StreamCollector();
  throwsCode('bad_server_wav', () => feed(c2, toNdjson(badA), 31));

  const c3 = new StreamCollector();
  feed(c3, good.text, 31);
  assert.strictEqual(c3.done.server_wav_url, undefined);
});

console.log('\n통과 ' + passed + '건');
