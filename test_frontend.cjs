// Render-path checks against the deployed API; no synthetic work is written.
const vm = require('vm');
const fs = require('fs');
const assert = require('assert');
const base = process.env.BOARD_URL || 'http://192.168.31.145:9090';
async function main() {
  const login = await fetch(base + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ userid: 'codex' }), signal: AbortSignal.timeout(10000) });
  assert(login.ok);
  const cookie = login.headers.get('set-cookie').split(';')[0];
  const nodes = new Map();
  function element(key) {
    if (!nodes.has(key)) nodes.set(key, { innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, dataset: {}, classList: { toggle() {} }, addEventListener() {}, close() {}, showModal() {} });
    return nodes.get(key);
  }
  const document = { querySelector: element, querySelectorAll: () => [], addEventListener() {} };
  const context = { document, window: { scrollTo() {} }, navigator: { clipboard: { writeText: async () => {} } }, crypto: require('crypto').webcrypto, setTimeout: () => 1, clearTimeout() {}, console, assert,
    fetch: (path, options = {}) => fetch(base + path, { ...options, headers: { ...options.headers, Cookie: cookie }, signal: AbortSignal.timeout(10000) }) };
  vm.createContext(context);
  const scriptResponse = await fetch(base + '/app.js');
  assert(scriptResponse.ok);
  const code = (process.env.BOARD_LOCAL_JS ? fs.readFileSync(__dirname + '/static/app.js','utf8') : await scriptResponse.text()).replace(/boot\(\);\s*$/, '');
  try {
    vm.runInContext(code, context);
    await vm.runInContext('boot()', context);
    assert(!element('#workspace').hidden);
    const home = element('#content').innerHTML;
    assert(home.includes('data-list="planned"'));
    assert(home.includes('data-list="new"'));
    assert(home.includes('class="exception-strip"'));
    assert(!home.includes('exception-strip" open'));
    assert(home.indexOf('成员工作') < home.indexOf('今日已完成'));
    assert(!home.includes('今日承诺'));
    assert(!home.includes('承诺工作项'));
    assert(!home.includes('技术 / Agent'));
    assert(!home.includes('data-role='));
    assert(!home.includes('data-member="wei"'));
    assert(!home.includes('data-member="cc"'));
    assert(!home.includes('data-member="codex"'));
    assert(!home.includes('data-member="test"'));
    assert(!home.includes('<progress'));
    vm.runInContext(`
      assert.equal(workStatus({group:'planned',source_type:'board',processed:0}),'未开工');
      assert.equal(workStatus({group:'current',source_type:'board',processed:0}),'处理中');
      assert.equal(workStatus({group:'current',source_type:'bug',state:'待分析'}),'未开工');
      assert.equal(workStatus({group:'current',source_type:'bug',state:'待测试'}),'处理中');
      assert.equal(workStatus({group:'done'}),'已完成');
      const pendingFixture=[
        {id:'held',group:'planned',state:'挂起'},
        {id:'active',group:'current',state:'待修复'},
        {id:'plan',group:'planned',state:'计划'},
        {id:'blocked-active',group:'current',state:'处理中',blocked:true},
        {id:'complete',group:'done',state:'已完成'},
        {id:'archive',group:'archived',state:'非Bug·重复'}
      ];
      assert.deepEqual(Array.from(pending(pendingFixture),i=>i.id),['active','plan','blocked-active']);
      assert.equal(pending(state.data.items).length,state.data.stats.pending);
      state.member='codex';
      assert(memberView().includes('该成员不在展示范围内'));
    `, context);
    await vm.runInContext("state.member=state.data.users.find(visibleMember).userid;go('member')", context);
    assert(element('#content').innerHTML.includes('计划工作'));
    await vm.runInContext("trace(state.data.items.find(i=>i.source_type==='bug').id)", context);
    assert(element('#content').innerHTML.includes('子项与进度依据'));
    assert(!element('#content').innerHTML.includes('<progress'));
    for (const page of ['requests', 'requests_done', 'daily', 'weekly', 'ledger', 'feedback', 'agent']) {
      await vm.runInContext(`go('${page}')`, context);
      assert(element('#content').innerHTML.length > 100);
      assert(element('#load-error').hidden);
    }
    vm.runInContext(`
      const originalData=state.data;
      const completeSample={id:'old-done',title:'Older completed work',group:'done',completed_at:'2026-09-30T10:00:00',owner_name:'Alice',owner_label:'负责人',source:'工作汇报'};
      state.data={items:[completeSample,{...completeSample,id:'not-done',group:'current'},{...completeSample,id:'archived',group:'archived'}]};
      state.ledgerQuery='';state.ledgerPage=1;
      assert.equal(ledgerItems().length,1);
      assert(ledgerResults().includes('old-done'));
      assert(!ledgerResults().includes('not-done'));
      assert(!ledgerResults().includes('archived'));
      state.ledgerQuery='no-match';assert(ledgerResults().includes('暂无符合条件'));
      state.data={items:Array.from({length:31},(_,i)=>({...completeSample,id:'done-'+i}))};
      state.ledgerQuery='';state.ledgerPage=1;assert(ledgerResults().includes('第 1/2 页'));
      state.ledgerPage=2;assert(ledgerResults().includes('第 2/2 页'));
      state.ledgerQuery='Older completed work';state.ledgerPage=1;
      assert(ledgerResults().includes('搜索匹配 31 项'));
      state.data=originalData;state.ledgerQuery='';state.ledgerPage=1;
    `, context);
    vm.runInContext(`
      const requestOriginalData=state.data,requestOriginalPage=state.page;
      const requestSample={reporter:state.user.userid,title:'Request',source_type:'bug',state:'待分析',owner_name:'Alice',created_at:'2026-10-02T09:00:00',completed_at:'2026-10-04T10:00:00'};
      state.data={items:[
        {...requestSample,id:'pending-new',group:'current'},
        {...requestSample,id:'done-new',group:'done'},
        {...requestSample,id:'pending-old',group:'planned',created_at:'2026-10-01T09:00:00'},
        {...requestSample,id:'done-old',group:'done',completed_at:'2026-10-03T10:00:00'},
        {...requestSample,id:'other-person',group:'current',reporter:'someone-else'},
        {...requestSample,id:'archived-request',group:'archived'}
      ]};
      state.page='requests';
      assert.deepEqual(Array.from(requestItems(),i=>i.id),['pending-old','pending-new']);
      assert(requestsView().includes('我的需求与问题 · 未完成'));
      assert(!requestsView().includes('request-state'));
      state.page='requests_done';
      assert.deepEqual(Array.from(requestItems(),i=>i.id),['done-old','done-new']);
      assert(requestsView().includes('我的需求与问题 · 已完成'));
      state.requestQueries.requests_done='done-old';assert.equal(requestItems().length,1);
      state.page='requests';assert.equal(requestItems().length,2);
      state.data=requestOriginalData;state.page=requestOriginalPage;state.requestQueries={requests:'',requests_done:''};
    `, context);
    vm.runInContext("assert.equal(esc('<script>'), '&lt;script&gt;')", context);
    const staticHtml = await fetch(base).then(r => r.text());
    assert(staticHtml.includes('/app.js'));
    if(!process.env.BOARD_LOCAL_JS){assert(staticHtml.includes('data-page="ledger"'));assert(staticHtml.includes('data-page="requests_done"'));}
    const css = await fetch(base + '/style.css');
    assert(css.ok);
    console.log('PASS: live API + all page render paths, member detail, trace, compact exceptions, completed placement, safe text rendering, static assets. Browser screenshot QA is not included.');
  } finally {
    await fetch(base + '/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: cookie }, body: '{}' });
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
