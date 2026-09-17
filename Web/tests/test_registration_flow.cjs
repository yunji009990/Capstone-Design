// 등록 화면의 실제 스크립트를 실행한다. DOM과 HTTP를 대체하며 외부 서비스는 호출하지 않는다.
// 가상 인물 자료는 화면이 쓰는 presets_v2.json 을 그대로 읽는다.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createHash, randomUUID} = require('node:crypto');
const html = fs.readFileSync(path.resolve(__dirname, '../static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const presets = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../static/presets_v2.json'), 'utf8'));
const SECTION_KINDS = ['background', 'shared_memory', 'preference', 'person',
                       'place_activity', 'about_user', 'news'];

function element(value = '') {
  const listeners = {};
  const self = {files: [], checked: false, disabled: false, style: {}, dataset: {},
    innerHTML: '', textContent: '', classList: {add() {}, remove() {}, toggle() {}},
    addEventListener(event, fn) { (listeners[event] ??= []).push(fn); },
    fire(event = 'input') { this['on' + event]?.({target: this}); for (const fn of listeners[event] || []) fn({target: this}); },
    removeAttribute() {}, appendChild() {},
    // 다시 그린 조각 안에서 다시 찾는 일은 흉내 내지 않는다. 화면 코드는 없을 때를 견뎌야 한다.
    querySelector() { return null; }, querySelectorAll() { return []; },
    play() {}, pause() {}, load() {}, click() { return self.disabled ? undefined : self.onclick?.(); }};
  // 브라우저처럼 value를 비우면 고른 파일도 사라진다. 화면 코드가 이 동작에 기댄다.
  Object.defineProperty(self, 'value', {enumerable: true,
    get: () => value,
    set: next => { value = next; if (next === '') self.files = []; }});
  return self;
}

// 화면은 본문을 글로 먼저 읽는다. 브라우저처럼 text() 를 주고 json() 은 그 글에서 만든다.
function response(data, ok = true) {
  return {ok, status: ok ? 200 : 503, text: async () => JSON.stringify(data),
          json: async () => data};
}
// JSON 이 아닌 본문(처리하지 못한 500, 프록시 HTML 등)을 그대로 흉내 낸다.
function rawResponse(body, status) {
  return {ok: status >= 200 && status < 300, status,
          text: async () => body,
          json: async () => { throw new SyntaxError(`Unexpected token '${body[0]}'`); }};
}
const sha = text => createHash('sha256').update(text).digest('hex');

// 서버 변환기를 흉내 내지 않는다. 화면이 지키는 계약(해시·확인 자료·문항 번호)만 만든다.
// 실제 변환 규칙은 Python 검사가 확인한다.
function draft(raw, options) {
  const d = JSON.parse(raw);
  const items = [{question: '4-1', label: '관계', value: d.person.relation, note: ''},
    {question: '4-5', label: '내 이름', value: d.person.user_name, note: ''},
    {question: '4-8', label: '그분의 이름', value: d.person.person_name, note: ''},
    {question: '7-2', label: '말투', value: d.speech.form, note: ''}];
  for (const [kind, section] of Object.entries(d.sections))
    for (const card of section.cards)
      items.push({question: kind === 'news' ? '6-4' : '5-1', label: kind,
                  value: card.content, note: card.mention_policy});
  return {persona: `인물: ${d.person.relation}; 이름: ${d.person.person_name}; 말투: ${d.speech.form}`,
    knowledge: JSON.stringify(d.heart), rules: d.care.avoid_topics,
    review: {sections: [{title: '확인', items}], notices: options.notices || []},
    issues: options.issues || [],
    survey_revision: sha(raw), preview_revision: sha('preview' + raw)};
}

async function page(options = {}) {
  const ids = new Map([...html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)].map(match =>
    ['#' + match[1], Object.assign(element(), {disabled: /\bdisabled\b/.test(match[0]),
      // 처음부터 숨겨 둔 상자는 화면 코드가 그 값을 보고 토글한다.
      style: /style="[^"]*display:\s*none/.test(match[0]) ? {display: 'none'} : {}})]));
  const sectionBoxes = new Map(SECTION_KINDS.map(kind =>
    [`.section[data-section="${kind}"]`, Object.assign(element(), {dataset: {section: kind}})]));
  // 톤·말투 카드는 상자 안으로 한정해서 고른다. 화면 코드와 같은 선택자를 쓴다.
  const groups = {'.s_tr': [element('다정함'), element('차분함')],
    '.s_quirk': [element(), element(), element()],
    '#tones .tone': ['warm_comfort', 'casual_recreation', 'free_dialogue'].map(k => Object.assign(element(), {dataset: {k}})),
    '#forms .form': ['informal', 'formal', 'mixed', 'unknown'].map(f => Object.assign(element(), {dataset: {f}})),
    '.card.step': Array.from({length: 10}, (_, i) => Object.assign(element(), {dataset: {step: String(i + 1)}}))};
  const select = selector => {
    if (selector.includes(',')) return selector.split(',').flatMap(part => select(part.trim()));
    if (selector === '.s_tr:checked') return groups['.s_tr'].filter(el => el.checked);
    if (groups[selector]) return groups[selector];
    return selector.startsWith('#') ? [ids.get(selector)].filter(Boolean) : [];
  };
  const find = selector => ids.get(selector) || sectionBoxes.get(selector) || null;
  class Form {
    constructor() { this.values = new Map(); }
    append(key, value) { this.values.set(key, value); }
    get(key) { return this.values.get(key); }
  }
  const calls = [], waiting = [], timeouts = new Map(), revoked = [];
  let timer = 0, blobs = 0;
  const context = vm.createContext({
    document: {querySelector: find, querySelectorAll: select, createElement: () => element()},
    window: {scrollTo() {}}, location: {reload() {}}, FormData: Form,
    // 미리 듣기 URL 이 파일마다 다르고, 바꿀 때 해제되는지 보려면 실제처럼 세어야 한다.
    AbortController, URL: {createObjectURL: () => 'blob:' + (++blobs),
                           revokeObjectURL: url => revoked.push(url)},
    setInterval: () => 1, clearInterval() {},
    setTimeout: fn => { timeouts.set(++timer, fn); return timer; }, clearTimeout: id => timeouts.delete(id),
    confirm: () => false,
    fetch: async (url, request = {}) => {
      calls.push({url, body: request.body});
      if (url === '/gate') return response({required: false});
      if (url === '/status') return response({health: {}, current: {}});
      if (url === '/presets_v2.json') return response(presets);
      if (url === '/persona') {
        const data = draft(request.body.get('survey'), options);
        if (options.deferPersona) return new Promise((resolve, reject) => {
          waiting.push(() => resolve(response(data)));
          if (!options.ignoreAbort) request.signal.addEventListener('abort', () => reject(Object.assign(new Error(), {name: 'AbortError'})));
        });
        if (options.failPersona) return response({detail: '가상 실패'}, false);
        if (options.personaValidation)
          return response({detail: [{loc: ['body', 'survey'], msg: '설문 형식이 올바르지 않습니다'},
                                    {loc: ['body', 'survey'], msg: '관계를 적어 주세요'}]}, false);
        if (options.personaCrash) return rawResponse('Internal Server Error', 500);
        if (options.malformedPersona) return response({persona: '', knowledge: '', rules: '', survey_revision: ''});
        return response(data);
      }
      if (url === '/publish_direct') {
        // 첫 요청만 실패시키는 선택지. 다시 누르면 성공해야 한다.
        if (options.publishCrash) {
          if (options.publishCrashOnce) options.publishCrash = false;
          return rawResponse('Internal Server Error', 500);
        }
        if (options.publishDetail) return response({detail: options.publishDetail}, false);
        if (options.publishBrokenSuccess) return rawResponse('OK', 200);
        // 200 이지만 등록된 세션이 없는 응답. JSON 이라고 성공으로 보면 안 된다.
        if (options.publishNoSession) return response(options.publishNoSession);
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
  const payload = () => run('surveyPayload()');
  const confirmReview = () => { ids.get('#confirm').checked = true; ids.get('#confirm').fire('change'); };
  // 파일 고르기는 사람이 하는 일이다. 인물을 바꾸면 다시 골라야 하므로 따로 뗐다.
  const chooseVoice = (name = 'synthetic.wav') => run(`pick({name: '${name}'})`);
  const readyInputs = () => {
    input('#s_rel', '할머니'); input('#s_name', '가상사용자'); input('#s_pname', '가상인물');
    for (const id of ['#c1', '#c2', '#c3']) { ids.get(id).checked = true; ids.get(id).fire('change'); }
    chooseVoice();
  };
  return {get: id => ids.get(id), section: kind => sectionBoxes.get(`.section[data-section="${kind}"]`),
    groups, calls, revoked, waiting, timeouts, run, input, readyInputs, chooseVoice, payload, confirmReview,
    next: () => ids.get('#next').onclick(), publish: () => ids.get('#publish').onclick(),
    loadTest: async () => { await ids.get('#testbtn').onclick(); },
    // 확인 화면까지 간 뒤 확인란을 누른다. 등록에는 두 가지가 모두 필요하다.
    review: async () => { run('showStep(8)'); await ids.get('#next').onclick(); },
    reviewed: async function () { await this.review(); confirmReview(); }};
}

test('빈 설문으로 시작하고 예시 인물·세션 ID 입력이 없다', async () => {
  const p = await page();
  assert.equal(p.get('#persona').value, ''); assert.equal(p.get('#knowledge').value, '');
  assert.equal(p.get('#publish').disabled, true);
  assert.equal(p.get('#session'), undefined);
  assert.equal(p.get('#confirm').checked, false);
  const survey = p.payload();
  assert.equal(survey.survey_schema_version, 2);
  assert.equal(survey.person.relation, '');
  for (const kind of ['shared_memory', 'news']) assert.equal(survey.sections[kind].cards.length, 0);
  assert.equal(p.calls.filter(x => x.url === '/presets_v2.json').length, 0);
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
});

test('테스트 버튼은 고른 가상 인물만 채우고 등록을 대신 누르지 않는다', async () => {
  const p = await page();
  await p.loadTest();
  assert.equal(p.payload().person.relation, '');            // 메뉴를 연 것만으로는 채우지 않는다
  p.run("applyPreset(presets.find(x => x.key === 'grandmother'))");
  const survey = p.payload(), source = presets.profiles[0].answers;
  assert.equal(survey.person.relation, source.person.relation);
  assert.equal(survey.person.person_name, source.person.person_name);
  assert.equal(survey.speech.form, source.speech.form);
  assert.equal(survey.sections.shared_memory.cards.length, source.sections.shared_memory.cards.length);
  assert.equal(survey.sections.news.cards.some(c => c.mention_policy === 'exclude_ai'), true);
  assert.notEqual(p.get('#testflag').style.display, 'none');
  assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
  assert.equal(p.get('#s_img').files.length, 0);
});

test('가상 인물을 바꾸면 이전 카드·상태가 섞이지 않는다', async () => {
  const p = await page();
  p.readyInputs();
  await p.loadTest();
  p.run("applyPreset(presets.find(x => x.key === 'grandmother'))");
  // 가상 예시 두 인물이 모두 '적겠습니다'이므로 모름·거절 상태는 여기서 직접 만든다.
  p.run("setSectionState('person', 'unknown')");
  p.run("setSectionState('about_user', 'declined')");
  p.chooseVoice();                                          // 인물을 고른 뒤 파일을 고른다
  await p.reviewed();
  assert.equal(p.get('#publish').disabled, false);
  p.run("applyPreset(presets.find(x => x.key === 'friend'))");
  const survey = p.payload(), friend = presets.profiles[1].answers;
  assert.equal(survey.person.relation, '친구');
  assert.equal(survey.sections.person.state, 'answered');   // 이전 '모름'이 남지 않는다
  assert.equal(survey.sections.about_user.state, 'answered'); // 이전 '거절'도 남지 않는다
  assert.equal(survey.sections.about_user.cards.length, friend.sections.about_user.cards.length);
  assert.equal(survey.sections.shared_memory.cards.length, friend.sections.shared_memory.cards.length);
  assert(!JSON.stringify(survey).includes('박순자'));
  assert(!JSON.stringify(survey).includes('수박'));
  assert.equal(p.get('#confirm').checked, false);           // 이전 인물의 확인이 남지 않는다
  assert.equal(p.get('#publish').disabled, true);
  p.run("clearSurvey()");
  assert.equal(p.payload().person.relation, '');
  assert.equal(p.payload().sections.person.state, 'answered');
});

test('인물을 바꾸면 이전 사진·음성 선택을 비우고 다시 고르게 한다', async () => {
  const p = await page();
  p.readyInputs();
  p.get('#s_img').files = [{name: 'old.png', size: 2048, type: 'image/png'}];
  await p.loadTest();
  p.run("applyPreset(presets.find(x => x.key === 'friend'))");
  assert.equal(p.run('dirFile'), null);
  assert.equal(p.run('previewUrl'), null);
  assert.equal(p.revoked.length, 1);                        // 이전 미리 듣기 URL 을 놓는다
  assert.equal(p.get('#direct').style.display, 'none');
  assert.equal(p.get('#s_img').files.length, 0);
  assert.equal(p.get('#publish').disabled, true);
  assert(p.get('#testmsg').textContent.includes('다시 골라'));
  assert(p.get('#voicenote').textContent.includes('다시 골라'));
  p.run("clearSurvey()");                                   // '설문 비우기'도 같다
  assert.equal(p.run('dirFile'), null);
  assert.equal(p.get('#s_img').files.length, 0);
});

test('음성을 고르면 네트워크 없이 미리 듣기가 준비되고 바꾸면 이전 URL을 놓는다', async () => {
  const p = await page();
  p.readyInputs();
  const before = p.calls.length;
  assert.equal(p.run('dirFile.name'), 'synthetic.wav');
  assert.equal(p.get('#direct').style.display, '');
  assert.equal(p.get('#dirplayer').src, p.run('previewUrl'));
  p.chooseVoice('another.m4a');                             // 같은 자리에서 파일만 바꾼다
  assert.equal(p.run('dirFile.name'), 'another.m4a');
  assert.deepEqual(p.revoked.length, 1);
  assert.notEqual(p.run('previewUrl'), p.revoked[0]);
  assert.equal(p.get('#dirplayer').src, p.run('previewUrl'));
  p.run('showStep(8)');                                     // 확인 화면도 그대로 쓴다
  assert.equal(p.get('#next').disabled, false);
  assert.equal(p.calls.length, before);                     // 업로드·분리 호출이 전혀 없다
});

test('문항이 정한 대상만 고를 수 있고 고른 대상이 설문에 남는다', async () => {
  const p = await page();
  p.readyInputs();
  const fixed = p.run("addCard('shared_memory', {content: '수박'})");
  assert.equal(p.payload().sections.shared_memory.cards[0].subject, 'both');
  assert(p.section('shared_memory').innerHTML.includes('누구 이야기: 함께'));
  assert(!p.section('shared_memory').innerHTML.includes('data-field="subject"'));
  p.run("addCard('preference', {content: '믹스커피'})");
  assert.equal(p.payload().sections.preference.cards[0].subject, 'person');
  const choices = p.section('preference').innerHTML;
  assert(choices.includes('data-field="subject"'));
  assert(choices.includes('>나<') && choices.includes('>그분<') && choices.includes('>함께<'));
  assert(!choices.includes('>다른 사람<'));
  const id = p.payload().sections.preference.cards[0].id;
  p.run(`setCardField('preference', '${id}', 'subject', 'user')`);
  assert.equal(p.payload().sections.preference.cards[0].subject, 'user');
  assert.equal(p.payload().sections.shared_memory.cards[0].id, fixed);
  p.run("addCard('news', {content: '취직했다'})");
  assert(p.section('news').innerHTML.includes('>다른 사람<'));
});

test('등록은 고른 파일과 설문 원문·두 개정 해시만 보내고 ID·인물 텍스트를 보내지 않는다', async () => {
  const p = await page(); p.readyInputs(); await p.reviewed(); await p.next();
  await p.publish();
  const post = p.calls.find(x => x.url === '/publish_direct').body;
  assert.equal(post.get('session'), undefined);
  assert.equal(post.get('persona'), undefined);
  assert.equal(post.get('knowledge'), undefined);
  assert.equal(post.get('voice').name, 'synthetic.wav');
  assert.match(post.get('survey_revision'), /^[a-f0-9]{64}$/);
  assert.match(post.get('preview_revision'), /^[a-f0-9]{64}$/);
  assert.equal(JSON.parse(post.get('survey')).person.relation, '할머니');
  assert.match(p.get('#donesid').textContent, /^[a-f0-9]{32}$/);
  // 등록 경로는 하나뿐이다. 분리·조각 조회 경로를 부르지 않는다.
  assert.equal(p.calls.some(x => x.url.startsWith('/extract')), false);
  assert.equal(p.calls.some(x => x.url.startsWith('/file/')), false);
  assert.equal(p.calls.some(x => x.url === '/publish'), false);
});

test('확인란을 누르기 전에는 등록도 다음 단계도 막힌다', async () => {
  const p = await page(); p.readyInputs(); await p.review();
  assert.equal(p.get('#publish').disabled, true);
  assert(p.get('#navinfo').textContent.includes('확인란'));
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
  p.confirmReview();
  assert.equal(p.get('#publish').disabled, false);
  await p.publish(); assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 1);
});

test('새 입력은 이전 확인과 작성 결과를 무효로 만든다', async () => {
  const changes = [p => p.groups['#tones .tone'][0].onclick(), p => p.input('#s_name', '다른사용자'),
    p => p.groups['#forms .form'][1].onclick(),
    p => p.run("addCard('shared_memory', {content: '새 추억'})"),
    p => { p.groups['.s_tr'][0].checked = true; p.groups['.s_tr'][0].fire('change'); },
    p => p.input('#s_unsaid', '새로 적은 말'),
    p => p.run("setSectionState('person', 'declined')")];
  for (const change of changes) {
    const p = await page(); p.readyInputs(); await p.reviewed();
    assert.equal(p.get('#publish').disabled, false);
    change(p);
    assert.equal(p.get('#publish').disabled, true);
    assert.equal(p.get('#confirm').checked, false);
    assert.equal(p.get('#persona').value, '');
    await p.reviewed(); assert.equal(p.get('#publish').disabled, false);
    assert.equal(p.calls.filter(x => x.url === '/persona').length, 2);
  }
});

test('같은 입력에서 앞뒤로 움직여도 작성 결과와 확인이 유지된다', async () => {
  const p = await page(); p.readyInputs(); await p.reviewed();
  p.run('showStep(5)'); p.run('showStep(9)');
  assert.equal(p.get('#publish').disabled, false);
  await p.review();                                   // 같은 설문이면 다시 부르지 않는다
  assert.equal(p.calls.filter(x => x.url === '/persona').length, 1);
});

test('카드 추가·삭제·상태 변경이 설문에 반영되고 빈 카드는 저장되지 않는다', async () => {
  const p = await page(); p.readyInputs();
  const id = p.run("addCard('shared_memory', {content: '마루에서 수박', time: '초등학생 때', certainty: 'unsure', mention_policy: 'on_request'})");
  p.run("addCard('shared_memory')");                  // 빈 카드
  assert.equal(p.run("sections.shared_memory.cards.length"), 2);
  let cards = p.payload().sections.shared_memory.cards;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].certainty, 'unsure');
  assert.equal(cards[0].mention_policy, 'on_request');
  p.run(`setCardField('shared_memory', '${id}', 'content', '고친 추억')`);
  assert.equal(p.payload().sections.shared_memory.cards[0].content, '고친 추억');
  p.run(`removeCard('shared_memory', '${id}')`);
  assert.equal(p.payload().sections.shared_memory.cards.length, 0);
  p.run("addCard('news', {content: '취직했다'}); setSectionState('news', 'none')");
  assert.equal(p.payload().sections.news.state, 'none');
  assert.equal(p.payload().sections.news.cards.length, 0);
});

test('이름 역할과 말투 선택이 관계와 따로 저장된다', async () => {
  const p = await page(); p.readyInputs();
  p.input('#s_pname', '박순자'); p.input('#s_name', '지훈');
  p.groups['#forms .form'][1].onclick();                     // 존댓말
  const survey = p.payload();
  assert.equal(survey.person.person_name, '박순자');
  assert.equal(survey.person.user_name, '지훈');
  assert.equal(survey.person.relation, '할머니');
  assert.equal(survey.speech.form, 'formal');
  p.groups['#forms .form'][3].onclick();
  assert.equal(p.payload().speech.form, 'unknown');
});

test('실제 대사는 세 쌍까지 상황·대답·기억 정도로 저장된다', async () => {
  const p = await page(); p.readyInputs();
  p.run("addSample({situation: '늦게 왔을 때', line: '밥은 먹었니', certainty: 'exact'})");
  p.run("addSample({line: '됐고'})");
  p.run("addSample({line: '그래'})");
  assert.equal(p.run("addSample({line: '넘침'})"), '');
  const rows = p.payload().speech.samples;
  assert.equal(rows.length, 3);
  assert.equal(rows[0].situation, '늦게 왔을 때');
  assert.equal(rows[0].certainty, 'exact');
  assert.equal(rows[1].certainty, 'approximate');
});

test('길이 초과를 알리고 등록을 막는다', async () => {
  const issues = [{code: 'knowledge_too_long', question: '9', message: 'AI에 전달할 내용이 깁니다.'}];
  const p = await page({issues}); p.readyInputs(); await p.review();
  assert(p.get('#issues').innerHTML.includes('AI에 전달할 내용이 깁니다.'));
  p.confirmReview();
  assert.equal(p.get('#publish').disabled, true);
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
  assert(p.get('#navinfo').textContent.includes('깁니다'));
});

test('확인 화면은 문항 번호와 알림을 보여주고 사용자 글을 실행하지 않는다', async () => {
  const p = await page({notices: ['<b>말투</b>가 다를 수 있습니다']});
  p.readyInputs();
  p.input('#s_rel', '<img src=x onerror=alert(1)>');
  await p.review();
  const review = p.get('#review').innerHTML;
  assert(review.includes('4-1'));
  assert(review.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert(!review.includes('<img src=x'));
  assert(p.get('#notices').innerHTML.includes('&lt;b&gt;말투&lt;/b&gt;'));
  // 번호는 span 안에, 고치기 단추는 제 칸에 둔다. 칸 이름이 사라지면 style.css 의
  // 너비 규칙이 풀려 1280px 에서도 「고치기」가 두 줄로 접힌다.
  assert(review.includes('<td class="qcol"><span class="qnum">'));
  assert(review.includes('<td class="fix"><button'));
});

test('작성 실패·잘못된 응답 후 등록을 막고 다시 작성하면 복구된다', async () => {
  for (const key of ['failPersona', 'malformedPersona']) {
    const options = {[key]: true}, p = await page(options); p.readyInputs(); await p.review();
    assert.equal(p.run('step'), 9); assert.equal(p.get('#persona').value, '');
    assert.equal(p.get('#next').disabled, true); assert.equal(p.get('#publish').disabled, true);
    await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
    options[key] = false; await p.get('#remake').onclick();
    p.confirmReview();
    assert.equal(p.get('#next').disabled, false);
  }
});

test('JSON이 아닌 500 응답을 등록 실패로 읽고 설문과 파일을 지키며 다시 시도할 수 있다', async () => {
  // 실제 장애: M4A 업로드가 500 plain text 를 돌려주자 화면이 r.json() 에서 터져
  // 「Unexpected token 'I'」가 참여자에게 그대로 보였다.
  const options = {publishCrash: true, publishCrashOnce: true};
  const p = await page(options); p.readyInputs(); await p.reviewed();
  const survey = JSON.stringify(p.payload());
  await p.publish();
  assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 1);
  assert.equal(p.run('published'), false);
  assert.equal(p.get('#done').style.display, 'none');       // 완료 화면으로 넘어가지 않는다
  assert.doesNotMatch(p.get('#donesid').textContent, /^[a-f0-9]{32}$/);
  const shown = p.get('#out').textContent;
  assert(shown.includes('처리하지 못했습니다'));
  assert(!shown.includes('Unexpected token'));
  assert(!shown.includes('Internal Server Error'));
  assert.equal(JSON.stringify(p.payload()), survey);        // 설문이 지워지지 않는다
  assert.equal(p.run('dirFile') === null, false);           // 고른 파일도 그대로
  assert.equal(p.get('#publish').disabled, false);          // 다시 누를 수 있다
  await p.publish();
  assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 2);
  assert.equal(p.run('published'), true);
  assert.match(p.get('#donesid').textContent, /^[a-f0-9]{32}$/);
});

test('200이지만 JSON이 아닌 등록 응답을 성공으로 취급하지 않는다', async () => {
  const p = await page({publishBrokenSuccess: true}); p.readyInputs(); await p.reviewed();
  await p.publish();
  assert.equal(p.run('published'), false);
  assert.equal(p.get('#done').style.display, 'none');
  assert.doesNotMatch(p.get('#donesid').textContent, /^[a-f0-9]{32}$/);
  assert(p.get('#out').textContent.includes('이해하지 못했습니다'));
  assert.equal(p.get('#publish').disabled, false);
});

test('세션 코드가 없거나 형식이 아닌 200 응답을 등록 완료로 넘기지 않는다', async () => {
  // 등록 서버는 UUID4 를 하이픈 없이 32자리로 준다. 그 형태가 아니면 참여자가
  // 지울 수도 없는 코드를 받게 되므로 완료 화면으로 넘기지 않는다.
  for (const body of [{}, [], {survey_saved: true}, {session: null}, {session: ''},
                      {session: '1234'}, {session: 'Z'.repeat(32)},
                      {session: randomUUID()}]) {
    const p = await page({publishNoSession: body});
    p.readyInputs(); await p.reviewed(); await p.publish();
    assert.equal(p.run('published'), false, JSON.stringify(body));
    assert.equal(p.get('#done').style.display, 'none');
    assert.doesNotMatch(p.get('#donesid').textContent, /^[0-9a-f]{32}$/);
    assert(p.get('#out').textContent.includes('확인하지 못했습니다'));
    assert.equal(p.get('#publish').disabled, false);        // 다시 시도할 수 있다
  }
  const good = await page();                                 // 정상 형태는 그대로 통과한다
  good.readyInputs(); await good.reviewed(); await good.publish();
  assert.equal(good.run('published'), true);
  assert.match(good.get('#donesid').textContent, /^[0-9a-f]{32}$/);
});

test('서버가 준 JSON 오류 문구와 검증 목록을 그대로 사람 말로 보여준다', async () => {
  const p = await page({publishDetail: '설문이 바뀌었습니다. 인물 확인에서 다시 확인해 주세요.'});
  p.readyInputs(); await p.reviewed(); await p.publish();
  assert(p.get('#out').textContent.includes('인물 확인에서 다시 확인'));
  assert.equal(p.run('published'), false);
  const v = await page({personaValidation: true}); v.readyInputs(); await v.review();
  assert(v.get('#mkinfo').textContent.includes('설문 형식이 올바르지 않습니다'));
  assert(v.get('#mkinfo').textContent.includes('관계를 적어 주세요'));
  assert.equal(v.get('#publish').disabled, true);
});

test('인물 미리보기가 JSON이 아닌 오류를 이해 가능한 문구로 바꾼다', async () => {
  const options = {personaCrash: true}, p = await page(options);
  p.readyInputs(); await p.review();
  assert(p.get('#mkinfo').textContent.includes('처리하지 못했습니다'));
  assert(!p.get('#mkinfo').textContent.includes('Unexpected token'));
  assert.equal(p.get('#publish').disabled, true);
  options.personaCrash = false;                     // 같은 입력으로 다시 작성하면 복구된다
  await p.get('#remake').onclick(); p.confirmReview();
  assert.equal(p.get('#publish').disabled, false);
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
  p.confirmReview();
  assert.equal(p.get('#next').disabled, false);
});

test('동의 해제나 관계 삭제는 등록할 수 없다', async () => {
  const p = await page(); p.readyInputs(); await p.reviewed();
  p.get('#c2').checked = false; p.get('#c2').fire('change');
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
  p.get('#c2').checked = true; p.get('#c2').fire('change');
  p.input('#s_rel', ' ');
  await p.publish(); assert.equal(p.calls.filter(x => x.url.startsWith('/publish')).length, 0);
});

test('연속 클릭과 완료 후 클릭은 중복 세션을 만들지 않는다', async () => {
  const p = await page({deferPublish: true}); p.readyInputs(); await p.reviewed();
  const first = p.publish(); await p.publish();
  assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 1);
  p.waiting[0](); await first; await p.publish();
  assert.equal(p.calls.filter(x => x.url === '/publish_direct').length, 1);
});

test('설문 저장의 부분 실패를 완료 화면에서 알린다', async () => {
  const p = await page({warning: '설문 기록을 저장하지 못했습니다.'});
  p.readyInputs(); await p.reviewed(); await p.publish();
  assert.equal(p.get('#donewarning').style.display, '');
  assert(p.get('#donewarning').textContent.includes('저장하지 못했습니다'));
});

// ── 4-11·4-12 떠나신 경위 ──
// 고인이라는 기본 사실은 대화 서버가 넣는다. 화면이 받는 것은 개별 원인·시점뿐이다.

// vm 안에서 만든 객체는 프로토타입이 달라 deepEqual 로 견주지 못한다. 글로 견준다.
const same = (value, expected) => assert.equal(JSON.stringify(value), JSON.stringify(expected));
const BLANK_PASSING = {state: 'unknown', cause: '', time: '', mention_policy: 'on_request'};

test('떠나신 경위는 비워 둔 채 시작하고 빈 값으로만 보낸다', async () => {
  const p = await page();
  same(p.run('passing'), BLANK_PASSING);
  same(p.payload().passing, BLANK_PASSING);
  // 적지 않았다는 안내가 화면에 있고, 내용 칸은 아직 그리지 않는다.
  // 안내는 전달·설정 사실만 말한다(생성 답변 보장 문구 금지).
  assert(p.get('#passingbox').innerHTML.includes('AI에 전달하지 않고 모르는 것으로 설정합니다'));
  assert(!p.get('#passingbox').innerHTML.includes('data-passing="cause"'));
});

test('사망 사실을 없애는 선택지나 먼저 꺼내는 선택지를 두지 않는다', async () => {
  const p = await page();
  p.run("setPassing('state', 'answered')");
  const shown = p.get('#passingbox').innerHTML;
  assert(!shown.includes('실제로 없습니다'));
  assert(!shown.includes('먼저 언급 가능'));
  assert(shown.includes('내가 꺼낼 때만'));
  assert(shown.includes('AI에 전달하지 않음'));
  same(Object.keys(p.run('PASSING_STATES')), ['answered', 'unknown', 'declined']);
  same(Object.keys(p.run('PASSING_MENTION')), ['on_request', 'exclude_ai']);
});

test('상태를 되돌리면 적어 둔 경위도 함께 비운다', async () => {
  const p = await page();
  p.run("setPassing('state', 'answered')");
  p.run("setPassing('cause', '오래 앓으시다 돌아가셨습니다')");
  p.run("setPassing('time', '재작년 겨울')");
  assert.equal(p.payload().passing.cause, '오래 앓으시다 돌아가셨습니다');
  p.run("setPassing('state', 'declined')");
  same(p.payload().passing, {state: 'declined', cause: '', time: '', mention_policy: 'on_request'});
  assert(!JSON.stringify(p.payload()).includes('오래 앓으시다'));
});

test('경위를 고치면 확인한 내용이 무효가 되고 다시 확인해야 한다', async () => {
  const p = await page();
  p.readyInputs(); await p.reviewed();
  assert.equal(p.get('#publish').disabled, false);
  p.run("setPassing('state', 'answered')");
  assert.equal(p.get('#confirm').checked, false);
  assert.equal(p.get('#publish').disabled, true);
  assert(p.get('#mkinfo').textContent.includes('설문이 변경'));
});

test('가상 인물의 경위를 가져오고 인물을 바꾸면 남기지 않는다', async () => {
  const p = await page();
  await p.loadTest();
  p.run("applyPreset(presets.find(x => x.key === 'grandmother'))");
  same(p.payload().passing, presets.profiles[0].answers.passing);
  p.run("applyPreset(presets.find(x => x.key === 'friend'))");
  same(p.payload().passing, presets.profiles[1].answers.passing);
  assert.equal(p.payload().passing.state, 'answered');
  assert.equal(p.payload().passing.time, '2026년 5월 29일');
  assert(!JSON.stringify(p.payload()).includes('오래 앓으시다'));
  p.run('clearSurvey()');
  same(p.payload().passing, BLANK_PASSING);
});
