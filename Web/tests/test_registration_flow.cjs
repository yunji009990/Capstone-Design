// 등록 화면의 실제 스크립트를 실행한다. DOM과 HTTP를 대체하며 외부 서비스는 호출하지 않는다.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createHash, randomUUID} = require('node:crypto');
const html = fs.readFileSync(path.resolve(__dirname, '../static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function element(value = '') {
  const listeners = {};
  return {value, files: [], checked: false, disabled: false, style: {}, dataset: {},
    innerHTML: '', textContent: '', classList: {add() {}, remove() {}, toggle() {}},
    addEventListener(event, fn) { (listeners[event] ??= []).push(fn); },
    fire(event = 'input') { this['on' + event]?.({target: this}); for (const fn of listeners[event] || []) fn({target: this}); },
    removeAttribute() {}, appendChild() {}, querySelectorAll() { return []; },
    play() {}, click() { return this.disabled ? undefined : this.onclick?.(); }};
}

function response(data, ok = true) { return {ok, status: ok ? 200 : 503, json: async () => data}; }
function draft(raw) {
  const data = JSON.parse(raw);
  return {persona: `인물: ${data.relation}; 이름: ${data.user_name}; 톤: ${data.tone_setting}; 성격: ${data.personality_traits}`,
    knowledge: JSON.stringify([data.shared_memories, data.missed_moment, data.unsaid_words, data.wished_to_hear]),
    survey_revision: createHash('sha256').update(raw).digest('hex')};
}

async function page(options = {}) {
  const ids = new Map([...html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)].map(match =>
    ['#' + match[1], Object.assign(element(), {disabled: /\bdisabled\b/.test(match[0])})]));
  const groups = {'.s_tr': [element('다정함'), element('차분함')],
    '.s_quirk': [element(), element(), element()], '.s_when': [element(), element(), element()],
    '.s_mem': [element(), element(), element()],
    '.tone': ['warm_comfort', 'casual_recreation', 'free_dialogue'].map(k => Object.assign(element(), {dataset: {k}})),
    '.card.step': Array.from({length: 10}, (_, i) => Object.assign(element(), {dataset: {step: String(i + 1)}}))};
  const select = selector => {
    if (selector.includes(',')) return selector.split(',').flatMap(part => select(part.trim()));
    if (selector === '.s_tr:checked') return groups['.s_tr'].filter(el => el.checked);
    return selector.startsWith('#') ? [ids.get(selector)].filter(Boolean) : groups[selector] || [];
  };
  class Form {
    constructor() { this.values = new Map(); }
    append(key, value) { this.values.set(key, value); }
    get(key) { return this.values.get(key); }
  }
  const calls = [], waiting = [], timeouts = new Map();
  let timer = 0;
  const context = vm.createContext({
    document: {querySelector: selector => ids.get(selector) || null, querySelectorAll: select, createElement: () => element()},
    window: {scrollTo() {}}, location: {reload() {}}, FormData: Form,
    AbortController, URL: {createObjectURL: () => 'blob:synthetic'},
    setInterval: () => 1, clearInterval() {},
    setTimeout: fn => { timeouts.set(++timer, fn); return timer; }, clearTimeout: id => timeouts.delete(id),
    confirm: () => false,
    fetch: async (url, request = {}) => {
      calls.push({url, body: request.body});
      if (url === '/gate') return response({required: false});
      if (url === '/status') return response({health: {}, current: {}});
      if (url === '/persona') {
        const data = draft(request.body.get('survey'));
        if (options.deferPersona) return new Promise((resolve, reject) => {
          waiting.push(() => resolve(response(data)));
          if (!options.ignoreAbort) request.signal.addEventListener('abort', () => reject(Object.assign(new Error(), {name: 'AbortError'})));
        });
        if (options.failPersona) return response({detail: '가상 실패'}, false);
        if (options.malformedPersona) return response({persona: '', knowledge: '', survey_revision: ''});
        return response(data);
      }
      if (url === '/publish_direct' || url === '/publish') {
        const data = {session: randomUUID().replaceAll('-', ''), survey_saved: true, model_queued: false,
                      registration_warning: options.warning || '', sent: {}};
        return options.deferPublish ? new Promise(resolve => waiting.push(() => resolve(response(data)))) : response(data);
      }
      throw Error('Unexpected request: ' + url);
    },
  });
  vm.runInContext(script, context, {filename: 'Web/static/index.html'});
  await new Promise(resolve => setImmediate(resolve));
  const run = code => vm.runInContext(code, context);
  const input = (selector, value, event = 'input') => { ids.get(selector).value = value; ids.get(selector).fire(event); };
  const readyInputs = () => {
    input('#s_rel', '할머니'); input('#s_name', '가상사용자');
    for (const id of ['#c1', '#c2', '#c3']) { ids.get(id).checked = true; ids.get(id).fire('change'); }
    input('#nspk', '1', 'change'); run("pick({name: 'synthetic.wav'}); start();");
  };
  return {get: id => ids.get(id), groups, calls, waiting, timeouts, run, input, readyInputs,
    next: () => ids.get('#next').onclick(), publish: () => ids.get('#publish').onclick(),
    review: async () => { run('showStep(8)'); await ids.get('#next').onclick(); }};
}

test('빈 인물로 시작하고 예시 채우기·세션 ID 입력이 없다', async () => {
  const p = await page();
  assert.equal(p.get('#persona').value, ''); assert.equal(p.get('#knowledge').value, '');
  assert.equal(p.get('#persona').disabled, true); assert.equal(p.get('#publish').disabled, true);
  assert.equal(p.get('#session'), undefined); assert.equal(p.get('#testfill'), undefined);
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
});

test('설문 확인 후 두 음성 경로 모두 작성 버전을 보내고 ID를 직접 보내지 않는다', async () => {
  for (const route of ['/publish_direct', '/publish']) {
    const p = await page(); p.readyInputs(); await p.review(); await p.next();
    if (route === '/publish') p.run("mode = 'extract'; picked = 'speaker'; job = 'job';");
    await p.publish();
    const post = p.calls.find(x => x.url === route).body;
    assert.equal(post.get('session'), undefined);
    assert.match(post.get('survey_revision'), /^[a-f0-9]{64}$/);
    assert(post.get('persona').includes('할머니'));
    assert.match(p.get('#donesid').textContent, /^[a-f0-9]{32}$/);
  }
});

test('설문이 같으면 인물 확인에서 직접 고친 문장을 유지한다', async () => {
  const p = await page(); p.readyInputs(); await p.review();
  p.input('#persona', p.get('#persona').value + '\n직접 편집');
  await p.review();
  assert(p.get('#persona').value.includes('직접 편집'));
  assert.equal(p.calls.filter(x => x.url === '/persona').length, 1);
});

test('설문 수정 후 이전 수동 편집을 버리고 새 설문으로 자동 작성한다', async () => {
  const p = await page(); p.readyInputs(); await p.review();
  p.input('#persona', p.get('#persona').value + '\n이전 편집');
  p.input('#s_rel', '어머니');
  assert.equal(p.get('#persona').value, ''); assert.equal(p.get('#publish').disabled, true);
  await p.review();
  assert(p.get('#persona').value.includes('어머니')); assert(!p.get('#persona').value.includes('이전 편집'));
  await p.publish(); assert(p.calls.find(x => x.url === '/publish_direct').body.get('persona').includes('어머니'));
});

test('톤·추억·이름·성격·마음 변경도 이전 작성 결과를 무효화한다', async () => {
  const changes = [p => p.groups['.tone'][0].onclick(), p => p.input('#s_name', '다른사용자'),
    p => { p.groups['.s_mem'][0].value = '새 추억'; p.groups['.s_mem'][0].fire(); },
    p => { p.groups['.s_tr'][0].checked = true; p.groups['.s_tr'][0].fire('change'); },
    p => p.input('#s_unsaid', '새로 적은 말')];
  for (const change of changes) {
    const p = await page(); p.readyInputs(); await p.review();
    change(p); assert.equal(p.get('#publish').disabled, true); assert.equal(p.get('#persona').value, '');
    await p.review(); assert.equal(p.get('#publish').disabled, false);
    assert.equal(p.calls.filter(x => x.url === '/persona').length, 2);
  }
});

test('작성 실패·잘못된 응답 후 등록을 막고 다시 작성하면 복구된다', async () => {
  for (const key of ['failPersona', 'malformedPersona']) {
    const options = {[key]: true}, p = await page(options); p.readyInputs(); await p.review();
    assert.equal(p.run('step'), 9); assert.equal(p.get('#persona').value, '');
    assert.equal(p.get('#next').disabled, true); assert.equal(p.get('#publish').disabled, true);
    await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
    options[key] = false; await p.get('#remake').onclick();
    assert.equal(p.get('#next').disabled, false);
  }
});

test('이전 설문의 늦은 응답이 새 작성 결과를 덮어쓰지 않는다', async () => {
  const p = await page({deferPersona: true, ignoreAbort: true}); p.readyInputs();
  const old = p.review(); p.input('#s_rel', '어머니'); const latest = p.review();
  p.waiting[1](); await latest;
  const result = p.get('#persona').value;
  p.waiting[0](); await old;
  assert.equal(p.get('#persona').value, result); assert(result.includes('어머니'));
});

test('작성 요청 시간 초과 후 빈 상태에서 재시도할 수 있다', async () => {
  const options = {deferPersona: true}, p = await page(options); p.readyInputs();
  const pending = p.review(); [...p.timeouts.values()][0](); await pending;
  assert.equal(p.get('#persona').value, ''); assert(p.get('#mkinfo').textContent.includes('시간이 초과'));
  options.deferPersona = false; await p.get('#remake').onclick();
  assert.equal(p.get('#next').disabled, false);
});

test('빈 인물 텍스트나 동의 해제는 등록할 수 없다', async () => {
  const p = await page(); p.readyInputs(); await p.review(); p.input('#persona', ' ');
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
  await p.get('#remake').onclick(); p.get('#c2').checked = false; p.get('#c2').fire('change');
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
});

test('연속 클릭과 완료 후 클릭은 중복 세션을 만들지 않는다', async () => {
  const p = await page({deferPublish: true}); p.readyInputs(); await p.review();
  const first = p.publish(); await p.publish();
  assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 1);
  p.waiting[0](); await first; await p.publish();
  assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 1);
});

test('설문 저장의 부분 실패를 완료 화면에서 알린다', async () => {
  const p = await page({warning: '설문 기록을 저장하지 못했습니다.'}); p.readyInputs(); await p.review(); await p.publish();
  assert.equal(p.get('#donewarning').style.display, '');
  assert(p.get('#donewarning').textContent.includes('저장하지 못했습니다'));
});
