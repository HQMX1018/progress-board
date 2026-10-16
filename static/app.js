'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { user: null, day: '', today: '', data: null, page: 'overview', member: '', list: '', trace: null, traceReturn: 'overview', feedback: [], serial: 0, ledgerQuery: '', ledgerPage: 1, requestQueries: {requests:'',requests_done:''} };
const names = { overview:'每日总览', requests:'我的需求 · 未完成', requests_done:'我的需求 · 已完成', daily:'每日简报', weekly:'每周简报', ledger:'总报记录', feedback:'意见反馈', agent:'Agent 接口', member:'成员工作', list:'工作清单', trace:'进度依据' };
const hiddenMemberIds = new Set(['wei', 'cc', 'codex', 'test']);
function visibleMember(user) { return !hiddenMemberIds.has(user.userid) && !['王', 'CC 自动化', 'codex', 'test'].includes(user.name); }
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4500); }
async function api(path, payload, headers = {}) {
  const response = await fetch(path, { method: payload === undefined ? 'GET' : 'POST', headers: { 'Content-Type':'application/json', ...headers }, body: payload === undefined ? undefined : JSON.stringify(payload), credentials:'same-origin' });
  const data = await response.json();
  if (!response.ok) { if (response.status === 401) showLogin(); throw new Error(data.error || '请求失败'); }
  return data;
}
function showLogin() { $('#workspace').hidden = true; $('#login').hidden = false; }
function rangeContains(date) { const day = (date || '').slice(0,10); return day && day >= state.data.start && day <= state.day; }
function pending(items) { return items.filter(i => !['done','archived'].includes(i.group) && i.state !== '挂起'); }
function relevant(user) { return state.data.items.filter(i => i.members.includes(user)); }
function completed(items) { return items.filter(i => state.data.done_ids.includes(i.id)); }
function find(id) { return state.data.items.find(i => i.id === id); }
function owner(item) { return `${item.owner_label || '负责人'}：${item.owner_name}`; }
function workStatus(item) {
  if (item.group === 'done') return '已完成';
  if (item.group === 'archived') return '已归档';
  if (item.state === '历史状态未知') return '状态待确认';
  if (item.source_type === 'bug') return ['待分析', '挂起'].includes(item.state) ? '未开工' : '处理中';
  return item.group === 'current' || item.processed > 0 || item.state === '开发中' ? '处理中' : '未开工';
}
function progress(item) { return `<button class="progress-button ${item.group === 'done' ? 'done' : ''}" data-trace="${esc(item.id)}"><span class="progress-meta"><strong>${workStatus(item)}</strong><span class="small muted">查看工作记录与证据 →</span></span></button>`; }
function card(item) { return `<article class="work-card"><h3>${esc(item.title)}</h3>${progress(item)}<div class="small muted">${esc(owner(item))} · ${esc(item.source)}</div><div class="small muted">${esc(item.next_step)}</div>${item.planned_for ? `<div class="small">计划 ${esc(item.planned_for)}${item.due_at ? ' · 预计完成 '+esc(item.due_at) : ''}</div>` : ''}</article>`; }
function itemList(items, count = 8) { return items.length ? items.slice(0,count).map(card).join('') + (items.length > count ? `<details><summary class="detail-toggle">展开其余 ${items.length-count} 项</summary>${items.slice(count).map(card).join('')}</details>` : '') : '<div class="empty">暂无对应工作</div>'; }
function panel(title, body, suffix = '') { return `<section class="panel"><div class="panel-head"><h2>${title}</h2>${suffix}</div><div class="panel-body">${body}</div></section>`; }
function stats() { const s = state.data.stats; return `<div class="stats">${[['planned','计划工作','按明确计划日期统计'],['new','新增工作','按创建日期统计'],['done','完成工作','按完成日期去重统计'],['pending','当前待处理','包含跨日未结与后续计划']].map(([key,label,hint]) => `<button class="stat" data-list="${key}"><div class="muted">${label}</div><div class="number">${s[key]}<span>项</span></div><div class="small muted">${hint}</div></button>`).join('')}</div>`; }
function exceptions() {
  const rows = state.data.exceptions;
  const counts = new Map(); rows.forEach(r => counts.set(r.kind, (counts.get(r.kind)||0)+1));
  return `<details class="exception-strip"><summary>异常事项 ${rows.length} 项${rows.length ? ' · '+[...counts].map(([k,n])=>esc(k)+' '+n).join(' / ')+' · 点击查看' : ' · 当前无异常'}</summary><div class="panel-body">${rows.map(r=>`<article class="exception-row"><div class="work-top"><strong>${esc(r.title)}</strong><span class="tag orange">${esc(r.kind)}</span></div><p>${esc(r.owner)} · ${esc(r.note)}</p><p>下一步：${esc(r.next_step)}</p><button class="text-btn" data-trace="${esc(r.item_id)}">查看工作依据 →</button></article>`).join('')}<p class="small muted">状态停留提示沿用 Bug 平台阈值，不据此推断个人投入。</p></div></details>`;
}
function members() {
  return state.data.users.filter(visibleMember).map(u=>{
    const items=relevant(u.userid), active=pending(items), done=completed(items), first=active[0]||done[0], a=state.data.activity[u.userid]||{};
    return `<article class="person"><button class="member-heading" data-member="${esc(u.userid)}"><span class="avatar">${esc(u.name.slice(0,1))}</span><span><strong>${esc(u.name)}</strong></span><span class="arrow">查看工作 →</span></button><div class="task">${first?`<h3>${esc(first.title)}</h3>${progress(first)}`:'<p class="small muted">当前无关联工作</p>'}<div class="task-meta"><span>关联待处理 ${active.length} 项</span><span>关联本期完成 ${done.length} 项</span></div><div class="task-meta"><span>当日提交 ${a.submitted||0}</span><span>评论补充 ${a.comments||0}</span><span>测试操作 ${a.qa||0}</span></div></div></article>`;
  }).join('');
}
function overview() {
  const tabs='';
  return stats()+exceptions()+`<div class="grid"><div><section class="panel"><div class="panel-head"><div><h2>成员工作</h2><span class="small muted">点击查看当前、计划和已完成工作</span></div>${tabs}</div>${members()}<div class="panel-body small muted">成员视图展示参与关联；同一工作可能关联多人，首页完成数按工作项去重。</div></section>${panel('今日已完成',itemList(completed(state.data.items)),`<span class="tag green">${state.data.stats.done} 项</span>`)}</div><div>${panel('当前待处理',itemList(pending(state.data.items)),`<span class="tag">${state.data.stats.pending} 项</span>`)}${panel('统计口径','<details><summary class="detail-toggle">查看计划、新增与完成的定义</summary>'+state.data.notes.map(n=>`<p class="small muted">${esc(n)}</p>`).join('')+'</details>')}</div></div>`;
}
function memberWorkGroups(items) {
  const current=pending(items).filter(i=>workStatus(i)==='处理中');
  const planned=items.filter(i=>!['done','archived'].includes(i.group)&&!current.includes(i));
  return {current,planned,done:completed(items)};
}
function memberView() {
  const user=state.data.users.find(u=>u.userid===state.member&&visibleMember(u)); if(!user)return '<div class="empty">该成员不在展示范围内</div>';
  const {current,planned,done}=memberWorkGroups(relevant(user.userid));
  return `<div class="actions"><button class="btn" data-go="overview">← 返回总览</button><h2>${esc(user.name)}的工作</h2></div><p class="inline-note">按提交、分配及实际操作关联，非个人独立交付归因；Bug 上的分配人是回归负责人。</p><div class="member-grid">${panel('当前工作',itemList(current,12),`<span class="tag">${current.length} 项</span>`)}${panel('计划工作 / 暂缓',itemList(planned,12),`<span class="tag">${planned.length} 项</span>`)}${panel('本期已完成',itemList(done,12),`<span class="tag green">${done.length} 项</span>`)}</div>`;
}
function listView() {
  const type=state.list;
  const ids=type==='planned'?state.data.plan_ids:type==='new'?state.data.new_ids:type==='done'?state.data.done_ids:null;
  const items=ids?state.data.items.filter(i=>ids.includes(i.id)):pending(state.data.items);
  return '<button class="btn" data-go="overview">← 返回总览</button>'+panel(({planned:'计划工作',new:'新增工作',done:'完成工作',pending:'当前待处理'})[type],itemList(items,20),`<span class="tag">${items.length} 项</span>`);
}
function requestItems(page=state.page) {
  const done=page==='requests_done',term=(state.requestQueries[page]||'').trim().toLowerCase();
  const time=i=>(done?i.completed_at:i.created_at)||i.created_at||'';
  return state.data.items.filter(i=>i.reporter===state.user.userid&&(done?i.group==='done':!['done','archived'].includes(i.group))&&(i.title+i.id).toLowerCase().includes(term)).sort((a,b)=>time(a).localeCompare(time(b))||a.id.localeCompare(b.id));
}
function requestCards() {
  const done=state.page==='requests_done',items=requestItems();
  const entry=i=>`<div class="request-entry"><div class="small muted">${done?'完成时间':'提交时间'}：${esc((done?i.completed_at:i.created_at)||'未提供')}</div>${card(i)}</div>`;
  return `<p class="small muted">共 ${items.length} 项 · ${done?'按完成时间':'按提交时间'}从早到晚</p>${items.length?items.slice(0,15).map(entry).join('')+(items.length>15?`<details><summary class="detail-toggle">展开其余 ${items.length-15} 项</summary>${items.slice(15).map(entry).join('')}</details>`:''):`<div class="empty">暂无${done?'已完成':'未完成'}的需求与问题</div>`}`;
}
function requestsView() {
  const done=state.page==='requests_done';
  const tabs=`<div class="tabs"><button class="tab ${done?'':'active'}" data-go="requests">未完成</button><button class="tab ${done?'active':''}" data-go="requests_done">已完成</button></div>`;
  return `<div class="notice">从原平台自动获取提交记录，无需重复填写。<a href="${esc(state.bugUrl)}/bugs/new" target="_blank" rel="noopener">去提交问题 ↗</a>　<a href="${esc(state.bugUrl)}/requirements" target="_blank" rel="noopener">查看需求 ↗</a></div>${panel(`我的需求与问题 · ${done?'已完成':'未完成'}`,`<div class="filter"><input id="request-search" placeholder="搜索标题或编号" aria-label="搜索我的需求" value="${esc(state.requestQueries[state.page]||'')}"></div><div id="request-items">${requestCards()}</div>`,tabs)}`;
}
function reportView(weekly) {
  const data=state.data,s=data.stats;
  const text=`${weekly?'每周':'每日'}简报 ${data.start} — ${state.day}\n\n计划工作：${s.planned} 项\n新增工作：${s.new} 项\n完成工作：${s.done} 项\n当前待处理：${s.pending} 项\n\n本期完成\n${completed(data.items).map(i=>`· ${i.title}（${i.owner_name}）`).join('\n')||'暂无完成记录'}\n\n异常事项：${data.exceptions.length} 项\n${data.exceptions.slice(0,10).map(e=>`· ${e.title}：${e.note}；${e.next_step}`).join('\n')}\n\n后续计划\n${data.items.filter(i=>i.group==='planned').slice(0,10).map(i=>`· ${i.title}：${i.planned_for||'未设日期'}`).join('\n')||'暂无计划记录'}\n\n口径：计划、新增、完成可能重叠；完成按工作项去重。需求侧记录从 Bug 平台归集。`;
  state.reportText=text;
  return stats()+exceptions()+panel(`${weekly?'本周':'当日'}工作简报`,`<div class="report-copy">${esc(text)}</div>`,`<button class="btn" data-copy-report>复制简报</button>`);
}
function ledgerItems() {
  return state.data.items.filter(i=>i.group==='done').sort((a,b)=>(b.completed_at||'').localeCompare(a.completed_at||'')||a.id.localeCompare(b.id));
}
function ledgerResults() {
  const all=ledgerItems(),term=state.ledgerQuery.trim().toLowerCase();
  const rows=all.filter(i=>`${i.title} ${i.id} ${i.owner_name} ${i.source}`.toLowerCase().includes(term));
  const pages=Math.max(1,Math.ceil(rows.length/30));state.ledgerPage=Math.max(1,Math.min(state.ledgerPage,pages));
  const shown=rows.slice((state.ledgerPage-1)*30,state.ledgerPage*30);
  return `<div class="panel-body small muted">共 ${all.length} 项累计完成工作${term?`，搜索匹配 ${rows.length} 项`:''} · 第 ${state.ledgerPage}/${pages} 页</div><div class="table-wrap"><table><thead><tr><th>完成工作项</th><th>完成时间</th><th>来源</th><th>负责人 / 提交人</th><th>工作留痕</th></tr></thead><tbody>${shown.map(i=>`<tr><td><strong>${esc(i.title)}</strong><div class="small muted">${esc(i.id)}</div><span class="tag green">已完成</span></td><td>${esc(i.completed_at||'未提供')}</td><td>${esc(i.source)}</td><td>${esc(owner(i))}</td><td><button class="text-btn" data-trace="${esc(i.id)}">查看记录与证据 →</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">暂无符合条件的已完成工作</td></tr>'}</tbody></table></div><div class="panel-body actions"><button class="btn" data-ledger-page="${state.ledgerPage-1}" ${state.ledgerPage===1?'disabled':''}>上一页</button><span class="muted small">每页 30 项</span><button class="btn" data-ledger-page="${state.ledgerPage+1}" ${state.ledgerPage===pages?'disabled':''}>下一页</button></div>`;
}
function ledgerView() {
  return `<div class="notice">累计展示截至 ${esc(state.day)} 已完成的全部工作，不限当天或本周；按工作项去重，不含未完成和非 Bug / 重复归档。完成时间以来源记录为准。</div><section class="panel"><div class="panel-head"><h2>累计已完成工作</h2><span class="tag green">${ledgerItems().length} 项</span></div><div class="filter"><input id="ledger-search" aria-label="搜索总报记录" maxlength="200" placeholder="搜索工作标题、编号、人员或来源" value="${esc(state.ledgerQuery)}"></div><div id="ledger-results">${ledgerResults()}</div></section>`;
}
function workHistory(events) {
  const visible=events.filter(e=>e.kind!=='状态流转');
  return visible.length?visible.map(e=>`<article class="report-line"><strong>${esc(e.kind)}</strong><div class="small muted">${esc(e.time)}${e.actor?' · '+esc(e.actor):''}</div>${e.kind==='评论补充'?`<div class="evidence-detail">${esc(e.text)}</div>`:`<details><summary class="detail-toggle">查看记录</summary><div class="evidence-detail">${esc(e.text)}</div></details>`}</article>`).join(''):'<div class="empty">暂无额外记录</div>';
}
function traceView() {
  const {item,events}=state.trace, canEdit=item.source_type==='board'&&item.owner===state.user.userid;
  return `<div class="actions"><button class="btn" data-go="${esc(state.traceReturn)}">← 返回工作视图</button>${item.url?`<a class="btn" href="${esc(item.url)}" target="_blank" rel="noopener">打开来源记录 ↗</a>`:''}</div><p class="inline-note">${esc(item.title)} · ${esc(owner(item))} · ${esc(item.source)}</p>${panel('工作进度',`<p><span class="tag ${item.group === 'done' ? 'green' : ''}">${workStatus(item)}</span></p><p class="small muted">${esc(item.plan_note)}。${item.source_type==='bug'?'三阶段依据来自真实状态流转，回退后按当前状态重算。':'已处理与已验收分别记录。'}</p>${canEdit?`<div class="actions"><button class="btn" data-work-state="current">设为处理中</button><button class="btn" data-work-state="planned">设为计划</button><button class="btn primary" data-work-state="done">全部验收后标记完成</button><button class="btn" data-blocked="${item.blocked?'false':'true'}">${item.blocked?'解除阻塞':'标记受阻'}</button></div>`:''}`)}<section class="panel"><div class="panel-head"><h2>子项与进度依据</h2></div><div class="table-wrap"><table><thead><tr><th>子项</th><th>状态</th><th>完成时间</th><th>证据 / 记录</th>${canEdit?'<th>更新</th>':''}</tr></thead><tbody>${item.subtasks.map(c=>`<tr><td>${esc(c.title)}</td><td><span class="tag ${c.status==='verified'?'green':''}">${({pending:'未开工',processed:'处理中',verified:'已完成'})[c.status]}</span></td><td>${esc(c.time||'—')}</td><td>${c.note?`<details><summary class="detail-toggle">查看处理记录</summary><div class="evidence-detail">${esc(c.note)}</div></details>`:''}${c.evidence_url?`<a href="${esc(c.evidence_url)}" target="_blank" rel="noopener">打开真实证据 ↗</a>`:c.status==='pending'?'<span class="muted">暂无完成证据</span>':''}</td>${canEdit?`<td><button class="text-btn" data-child="${esc(c.id)}">更新</button></td>`:''}</tr>`).join('')}</tbody></table></div></section>${panel('工作记录与变更',workHistory(events))}`;
}
function feedbackView() {
  return `<div class="grid">${panel('帮助完善本平台',`<form id="feedback-form" class="feedback-form"><label for="feedback-type">反馈类型</label><select id="feedback-type"><option>使用不便</option><option>功能建议</option><option>数据问题</option><option>页面异常</option></select><label for="feedback-area">相关页面</label><select id="feedback-area">${['每日总览','成员工作','我的需求','每日简报','每周简报','其他'].map(n=>`<option>${n}</option>`).join('')}</select><label for="feedback-title">一句话描述</label><input id="feedback-title" required maxlength="200"><label for="feedback-content">问题 / 建议</label><textarea id="feedback-content" required rows="5" maxlength="4000"></textarea><p class="small muted">反馈会持久保存，记录当前登录人。</p><button class="btn primary" type="submit">提交反馈</button></form>`)}${panel('反馈记录',state.feedback.length?state.feedback.map(f=>`<article class="feedback-item"><div class="work-top"><strong>${esc(f.title)}</strong><span class="tag">${esc(f.status)}</span></div><p>${esc(f.content)}</p><span class="muted small">${esc(f.author)} · ${esc(f.area)} · ${esc(f.created_at)}</span></article>`).join(''):'<div class="empty">暂无反馈</div>')}</div>`;
}
function agentView() {
  return panel('Agent 填报接口',`<p>为当前登录人创建独立密钥，Agent 只能更新该身份的工作，不会修改 Bug 平台。</p><form id="token-form"><label for="token-name">密钥用途</label><div class="actions"><input id="token-name" required maxlength="100" placeholder="例如：开发 Agent"><button class="btn primary">创建密钥</button></div></form><div id="token-created"></div><h3>接口示例</h3><p class="small muted">密钥通过 Authorization: Bearer 提供；每次写入必须带唯一 Idempotency-Key。重复提交同一内容会返回原结果，不重复计数。</p><pre>POST /api/work-items
Authorization: Bearer &lt;你的密钥&gt;
Idempotency-Key: task-123-create
Content-Type: application/json

{
  "title": "通知接口联调",
  "status": "current",
  "planned_for": "${state.today}",
  "next_step": "验证异常场景",
  "subtasks": [
    {"title": "字段确认"},
    {"title": "联调验证"}
  ]
}

POST /api/work-items/&lt;id&gt;/events
Authorization: Bearer &lt;你的密钥&gt;
Idempotency-Key: task-123-step-1
Content-Type: application/json

{
  "subtask_id": "1",
  "status": "processed",
  "note": "字段已确认，等待联调验证",
  "evidence_url": "http://192.168.31.145:8000/bugs/123"
}

子项状态：pending / processed / verified
工作状态：planned / current / done
更新工作状态：{"work_status":"done"}
完成前必须验收所有子项；blocked 可标记阻塞。
GET /api/dashboard?day=YYYY-MM-DD
GET /api/items/&lt;id&gt;?day=YYYY-MM-DD</pre><div id="token-list"></div>`);
}
function render() {
  $('#page-title').textContent=({overview:'当前待处理，今日已完成',member:'成员工作详情',trace:'可追溯的进度依据'})[state.page]||names[state.page];
  $('#breadcrumb').textContent=names[state.page];
  $('#page-description').textContent=state.page==='requests'?'未完成的需求与问题，按提交时间从早到晚查看。':state.page==='requests_done'?'已完成的需求与问题，按完成时间从早到晚查看。':state.page==='ledger'?'累计已完成工作留痕，按完成时间倒序查看。':state.page==='weekly'?`本周 ${state.data.start} 至 ${state.day}，按工作项去重汇总。`:'计划、新增和完成工作自动汇总，点击进度查看依据。';
  document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===state.page));
  $('#content').innerHTML=({overview,member:memberView,list:listView,requests:requestsView,requests_done:requestsView,daily:()=>reportView(false),weekly:()=>reportView(true),ledger:ledgerView,feedback:feedbackView,agent:agentView,trace:traceView})[state.page]();
  $('#read-status').textContent=`数据读取时间：${state.data.read_at} · 来源 Bug 平台（只读）+ 工作汇报 · 查看日期 ${state.day}`;
}
async function refresh() {
  const serial=++state.serial;$('#refresh').disabled=true;
  try { const data=await api(`/api/dashboard?day=${state.day}&period=${state.page==='weekly'?'week':'day'}`);if(serial!==state.serial)return;state.data=data;$('#load-error').hidden=true;if(state.page==='feedback')state.feedback=(await api('/api/feedback')).feedback;render(); }
  catch(e){$('#load-error').textContent=e.message+' 已保留上次读取的内容。';$('#load-error').hidden=false;}
  finally{$('#refresh').disabled=false;}
}
async function go(page) { state.page=page;await refresh();if(page==='agent')await renderTokens();window.scrollTo({top:0,behavior:'smooth'}); }
async function trace(id) { const previous=state.page;const result=await api(`/api/items/${encodeURIComponent(id)}?day=${state.day}`);state.trace=result;state.traceReturn=previous==='trace'?state.traceReturn:previous;state.page='trace';render();window.scrollTo({top:0,behavior:'smooth'}); }
async function renderTokens() { const data=await api('/api/tokens');if(!$('#token-list'))return;$('#token-list').innerHTML='<h3>已创建的密钥</h3>'+data.tokens.map(t=>`<div class="report-line">${esc(t.name)} · ${esc(t.created_at)} <button class="text-btn" data-revoke="${esc(t.digest)}">撤销</button></div>`).join(''); }
async function updateWork(payload) { await api(`/api/work-items/${state.trace.item.id}/events`,payload,{'Idempotency-Key':crypto.randomUUID?crypto.randomUUID():String(Date.now())});await refresh();await trace(state.trace.item.id); }
document.addEventListener('click',async e=>{
  const b=e.target.closest('button,[data-trace]');if(!b)return;
  try {
    if(b.dataset.page){await go(b.dataset.page);return;}
    if(b.dataset.go){await go(b.dataset.go);return;}
    if(b.dataset.close){$('#'+b.dataset.close).close();return;}
    if(b.dataset.trace){await trace(b.dataset.trace);return;}
    if(b.dataset.member){state.member=b.dataset.member;await go('member');return;}
    if(b.dataset.ledgerPage){state.ledgerPage=Number(b.dataset.ledgerPage);$('#ledger-results').innerHTML=ledgerResults();return;}
    if(b.dataset.list){state.list=b.dataset.list;await go('list');return;}
    if(b.hasAttribute('data-copy-report')){try{await navigator.clipboard.writeText(state.reportText);toast('已复制简报');}catch{toast('浏览器未允许复制，请选择简报文字复制。');}return;}
    if(b.dataset.child){const c=state.trace.item.subtasks.find(s=>s.id===b.dataset.child);state.child=c.id;$('#update-title').textContent=c.title;$('#child-status').value=c.status;$('#child-note').value=c.note||'';$('#child-url').value=c.evidence_url||'';$('#update-dialog').showModal();return;}
    if(b.dataset.workState){await updateWork({work_status:b.dataset.workState});return;}
    if(b.hasAttribute('data-blocked')){await updateWork({blocked:b.dataset.blocked==='true'});return;}
    if(b.dataset.revoke){await api('/api/tokens/revoke',{digest:b.dataset.revoke});await renderTokens();toast('密钥已撤销');}
  }catch(error){toast(error.message);}
});
document.addEventListener('input',e=>{
  if(e.target.id==='ledger-search'){state.ledgerQuery=e.target.value;state.ledgerPage=1;$('#ledger-results').innerHTML=ledgerResults();return;}
  if(e.target.id!=='request-search')return;
  state.requestQueries[state.page]=e.target.value;
  $('#request-items').innerHTML=requestCards();
});
document.addEventListener('submit',async e=>{
  e.preventDefault();const form=e.target,submit=form.querySelector('button[type="submit"],button:not([type])');if(submit)submit.disabled=true;
  try {
    if(form.id==='login-form'){await api('/api/login',{userid:$('#userid').value.trim()});await boot();}
    else if(form.id==='work-form'){await api('/api/work-items',{title:$('#work-title').value,status:$('#work-state').value,planned_for:$('#work-plan').value||null,due_at:$('#work-due').value||null,next_step:$('#work-next').value,subtasks:$('#work-children').value.split('\n').map(s=>s.trim()).filter(Boolean).map(title=>({title}))},{'Idempotency-Key':crypto.randomUUID?crypto.randomUUID():String(Date.now())});$('#work-dialog').close();form.reset();await refresh();toast('工作已保存');}
    else if(form.id==='update-form'){await updateWork({subtask_id:state.child,status:$('#child-status').value,note:$('#child-note').value,evidence_url:$('#child-url').value});$('#update-dialog').close();toast('进度已更新并留痕');}
    else if(form.id==='feedback-form'){await api('/api/feedback',{category:$('#feedback-type').value,area:$('#feedback-area').value,title:$('#feedback-title').value,content:$('#feedback-content').value});await refresh();toast('反馈已保存');}
    else if(form.id==='token-form'){const result=await api('/api/tokens',{name:$('#token-name').value});$('#token-created').innerHTML='<p class="notice">密钥仅显示一次，请保存。请勿将密钥写入公共代码库。</p><input class="token-value" aria-label="新建密钥" readonly>';$('#token-created input').value=result.token;await renderTokens();}
  } catch(error){if(form.id==='login-form')$('#login-error').textContent=error.message;else toast(error.message);}
  finally{if(submit)submit.disabled=false;}
});
$('#refresh').onclick=refresh;$('#day').onchange=()=>{state.day=$('#day').value;refresh();};
$('#new-work').onclick=()=>{$('#work-plan').value=state.today;$('#work-dialog').showModal();};
$('#logout').onclick=async()=>{try{await api('/api/logout',{});showLogin();}catch(e){toast(e.message);}};
async function boot(){try{const data=await api('/api/me');state.user=data.user;state.today=data.today;state.day=state.day||data.today;state.bugUrl=data.bug_url;$('#day').value=state.day;$('#day').max=data.today;$('#identity').textContent=data.user.name;$('#login').hidden=true;$('#workspace').hidden=false;await refresh();}catch(e){showLogin();}}
boot();
