const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => `${value < 0 ? '-' : ''}${Math.floor(Math.abs(value)/100)}.${String(Math.abs(value)%100).padStart(2,'0')}`;
const billDrafts=new Map(),selectedBills=new Set();let batchEditing=false;
let me, csrf = '', trips = [], current = null, currentData = null, users = [], editing = null, toastTimer, viewRequest = 0, publicSite = false;
async function api(path, method='GET', body) {
  const response = await fetch(path,{method,headers:method==='GET'?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { if(response.status===401 && me) { me=null; await boot(); } throw new Error(data.error || '请求失败'); }
  return data;
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').hidden=true;},5500); }
function safe(action) { return async event => { event?.preventDefault();const button=event?.submitter;if(button)button.disabled=true;try{await action(event);}catch(e){toast(e.message);}finally{if(button?.isConnected)button.disabled=false;} }; }
function confirmAction(title,description,action) { $('confirm-title').textContent=title;$('confirm-description').textContent=description;$('confirm-dialog').showModal();$('confirm-no').onclick=()=>{$('confirm-dialog').close();};$('confirm-yes').onclick=safe(async()=>{$('confirm-dialog').close();await action();}); }
function leaveDrafts(action) {
  if(!billDrafts.size)return action();
  confirmAction('放弃尚未上传的编辑？','离开当前页面后，这些编辑不会保留。已上传的账单不受影响。',async()=>{billDrafts.clear();selectedBills.clear();await action();});
}
const field = (label,id,type='text',extra='') => `<label>${label}<input id="${id}" type="${type}" ${extra} required></label>`;
const empty = message => `<div class="empty">${message}</div>`;
const name = id => currentData?.members.find(m=>m.id===id)?.name || '未知成员';
function nav() {
  $('session-nav').innerHTML=me?`<span>${esc(me.name)} <small>${me.role==='admin'?'管理员':'成员'}</small></span><button id="home" class="text-button">我的旅程</button>${me.role==='admin'?'<button id="admin" class="text-button">管理后台</button>':''}<button id="password" class="text-button">修改密码</button><button id="logout" class="text-button">退出</button>`:`<span class="muted">${publicSite?'旅程账本 · 安全登录':'本地开发版'}</span>`;
  if(!me)return;
  $('home').onclick=safe(()=>leaveDrafts(home));if($('admin'))$('admin').onclick=safe(()=>leaveDrafts(admin));
  $('logout').onclick=safe(()=>leaveDrafts(async()=>{await api('/api/logout','POST',{});await boot();}));
  $('password').onclick=()=>leaveDrafts(()=>{current=null;$('app').innerHTML=`<section class="card auth"><h1>修改自己的密码</h1><form id="password-form">${field('原密码','old-password','password','autocomplete="current-password"')}${field('新密码（至少 6 字符）','new-password','password','minlength="6" maxlength="128" autocomplete="new-password"')}<button class="primary">保存并重新登录</button></form></section>`;$('password-form').onsubmit=safe(async()=>{await api('/api/password','POST',{current:$('old-password').value,password:$('new-password').value});await boot();toast('密码已修改，请重新登录');});});
}
async function boot() {
  ++viewRequest;
  const status=await api('/api/me');me=status.user;csrf=status.csrf;publicSite=status.public;nav();
  if(me)return home();
  if(status.setup_required && !status.setup) {
    $('app').innerHTML='<section class="card auth"><p class="eyebrow">同行 · 旅程账本</p><h1>等待管理员初始化</h1><p class="muted">网站管理员尚未完成首次设置，请稍后再试。</p><button id="retry-setup" class="secondary">刷新状态</button></section>';
    $('retry-setup').onclick=safe(boot);return;
  }
  const setup=status.setup;
  $('app').innerHTML=`<div class="auth-layout"><div><p class="eyebrow">一起出发，轻松算清</p><h1>每段旅程，<br>一本清楚的账。</h1><p class="muted">各自上传，逐笔确认。最后只需按清单转账。</p>${publicSite?'':'<a class="text-link" href="/legacy/">查看旧版本机账本 ↗</a>'}</div><section class="card auth"><h2>${setup?'首次设置管理员':'登录同行'}</h2><p class="muted">${setup?'手动初始化模式：请设置管理员账号；仅限本机首次设置。':'请使用网站管理员提供的账号密码登录。'}</p><form id="auth-form">${field('登录名','username','text','pattern="[a-zA-Z0-9_-]{3,32}" autocomplete="username"')}${setup?field('显示姓名','display-name','text','maxlength="30"'):''}${field('密码','password-input','password',`minlength="${setup?6:1}" maxlength="128" autocomplete="${setup?'new-password':'current-password'}"`)}${setup?field('再次输入密码','password-again','password','minlength="6" autocomplete="new-password"'):''}<button class="primary">${setup?'创建管理员并进入':'登录'}</button></form></section></div>`;
  $('auth-form').onsubmit=safe(async()=>{
    if(setup && $('password-input').value!==$('password-again').value)throw new Error('两次密码输入不一致');
    const body={username:$('username').value,password:$('password-input').value};if(setup)body.name=$('display-name').value;
    await api(setup?'/api/setup':'/api/login','POST',body);await boot();
  });
}
async function home() {
  const request=++viewRequest;
  const response=await api('/api/trips');
  if(request!==viewRequest)return;
  current=null;editing=null;billDrafts.clear();selectedBills.clear();batchEditing=false;trips=response.trips;
  $('app').innerHTML=`<section class="page-head"><div><p class="eyebrow">TRAVEL TOGETHER</p><h1>我的旅程</h1><p class="muted">每段旅程独立记账、独立结算。</p></div>${me.role==='admin'?'<button id="new-trip" class="primary compact">＋ 新建旅程</button>':''}</section><section class="trip-grid">${trips.length?trips.map(t=>`<button class="trip-card" data-trip="${t.id}"><span class="trip-icon">↗</span><h2>${esc(t.name)}</h2><p>${esc(t.start)} 至 ${esc(t.end)}</p><span class="text-link">进入账本 →</span></button>`).join(''):empty(me.role==='admin'?'先在后台创建成员账号，再新建第一段旅程。':'还没有分配给你的旅程，请联系管理员。')}</section>${publicSite?'':`<section class="card note"><strong>旧账本还在</strong><p>旧版数据保留在原浏览器中。进入旅程后可核对成员并迁移本人垫付的账单；不会自动混入新旅程。</p><a href="/legacy/" class="text-link">打开旧版本机账本</a></section>`}`;
  document.querySelectorAll('[data-trip]').forEach(b=>{b.onclick=safe(()=>showTrip(Number(b.dataset.trip)));});
  if($('new-trip'))$('new-trip').onclick=safe(()=>admin('trip'));
}
async function showTrip(id) {
  const request=++viewRequest;
  const data=await api(`/api/trips/${id}`);
  if(request!==viewRequest)return;
  if(current!==id){billDrafts.clear();selectedBills.clear();}
  current=id;editing=null;batchEditing=false;currentData=data;renderTrip();
}
function memberOptions(selected) { return currentData.members.filter(m=>m.enrolled!==0||m.id===selected).map(m=>`<option value="${m.id}" ${m.id===selected?'selected':''}>${esc(m.name)} (${esc(m.username)})${m.enrolled===0?' · 已退出':''}</option>`).join(''); }
function participants(selected) { return currentData.members.filter(m=>m.enrolled!==0||selected.includes(m.id)).map(m=>`<label class="participant"><input type="checkbox" value="${m.id}" ${selected.includes(m.id)?'checked':''}><span>${esc(m.name)}${m.enrolled===0?' · 已退出':''}</span></label>`).join(''); }
function renderTrip() {
  const {trip,members,bills,summary}=currentData;
  const ownMember=members.some(m=>m.id===me.id&&m.enrolled!==0),ownPending=bills.filter(b=>b.owner===me.id&&b.kind==='pending').length;
  $('app').innerHTML=`<section class="page-head"><div><p class="eyebrow">当前旅程 · 独立结算</p><h1>${esc(trip.name)}</h1><p class="muted">${esc(trip.start)} 至 ${esc(trip.end)} · 含开始和结束当天的全部消费</p></div><button id="export" class="primary compact">导出 Excel 结算记录 ↗</button></section><div class="trip-banner">${members.map(m=>`<span>${esc(m.name)}${m.enrolled===0?'（已退出）':m.active?'':'（停用）'}</span>`).join('')}</div><section class="stats"><div><span>共同 AA 花费</span><strong>¥ ${money(summary.total)}</strong></div><div><span>共同消费</span><strong>${bills.filter(b=>b.kind==='shared').length}<small> 笔</small></strong></div><div><span>我的待确认</span><strong>${ownPending}<small> 笔</small></strong></div><div><span>建议转账</span><strong>${summary.transfers.length}<small> 笔</small></strong></div></section><div class="workspace"><div class="left-column">${ownMember?`<section class="card"><div class="section-head"><h2>上传自己的微信 / 支付宝账单</h2><span class="pill">${esc(trip.name)}</span></div><p class="muted">仅导入 ${esc(trip.start)} — ${esc(trip.end)} 的支出。导入后逐笔确认，未确认和私人消费不计入 AA。</p><form id="upload-form"><label class="upload-area">选择 CSV / XLSX<input type="file" id="statement-file" accept=".csv,.xlsx" required><small>最大 5 MB · 请先解压邮件附件</small></label><button class="primary">解析并进入逐笔确认</button></form><p class="footnote">支持个人对账标准表头；退款、失败和资金往来会跳过并提示人工核实。<a href="/samples/wechat-sample.csv" download>下载微信模拟 CSV</a> · <a href="/samples/alipay-sample.csv" download>下载支付宝模拟 CSV</a></p><div id="upload-report"></div></section><section class="card"><h2 id="bill-form-title">手动记一笔</h2><form id="bill-form"><div class="form-grid">${field('消费项目','bill-title','text','maxlength="80"')}${field('金额（元）','bill-amount','text','inputmode="decimal"')}${field('交易日期','bill-date','date',`min="${trip.start}" max="${trip.end}" value="${trip.start}"`)}<label>实际垫付者<select id="bill-payer">${memberOptions(me.id)}</select></label></div><fieldset><legend>是否参加 AA</legend><label class="radio"><input type="radio" name="bill-kind" value="shared" checked>共同消费</label><label class="radio"><input type="radio" name="bill-kind" value="private">私人消费（不结算）</label></fieldset><fieldset id="split-field"><legend>实际参与分摊的成员</legend><div id="participants" class="participants">${participants(members.filter(m=>m.enrolled!==0).map(m=>m.id))}</div></fieldset><p class="footnote">均分尾差按旅程成员添加顺序分配。垫付者可不参与。</p><div class="actions"><button id="bill-submit" class="primary">保存账单</button></div></form></section>`:''}<section class="card"><div class="section-head"><h2>消费明细与逐笔确认</h2><span class="muted">只可调整自己的账单</span></div><label class="filter">查看<select id="bill-filter"><option value="all">全部可见</option><option value="own">我的账单</option><option value="pending">我的待确认</option><option value="shared">共同 AA 消费</option><option value="private">我的私人消费</option></select></label><div id="batch-tools"></div><div id="bill-list"></div></section>${ownMember&&!publicSite?'<section class="card"><h2>安全迁移旧版本机账本</h2><p class="muted">旧账本没有日期。请指定本旅程内的记账日期并映射成员，仅迁移本人垫付的账单；原数据保留。</p><button id="legacy-preview" class="secondary">读取旧账本并核对</button><div id="legacy-migration"></div></section>':''}</div><aside><section class="card settlement"><div class="section-head"><h2>谁给谁转账</h2><span class="pill">仅共同消费</span></div><div id="transfers">${summary.transfers.length?summary.transfers.map((t,i)=>`<div class="transfer"><span>${i+1}</span><div><strong>${esc(name(t.from_id))} → ${esc(name(t.to_id))}</strong><p>转账 <b>¥ ${money(t.cents)}</b></p></div></div>`).join(''):empty('当前无需转账')}</div><button id="copy" class="secondary wide">复制转账清单</button><p class="footnote">导入账单确认完毕后，再核对最终转账。这里不会发起真实付款。</p></section><section class="card"><h2>每个人的账</h2>${summary.rows.map(r=>`<div class="balance-row"><div><strong>${esc(r.name)}</strong><span class="${r.balance<0?'owe':'receive'}">${r.balance>0?'应收':r.balance<0?'应付':'平账'} ${r.balance?'¥ '+money(Math.abs(r.balance)):''}</span></div><small>垫付 ¥ ${money(r.paid)} · 承担 ¥ ${money(r.share)}</small></div>`).join('')}</section></aside></div>`;
  renderBills();$('bill-filter').onchange=()=>{editing=null;batchEditing=false;selectedBills.clear();renderBills();};
  $('export').onclick=safe(async()=>{const result=await api(`/api/trips/${trip.id}/export`,'POST',{});const a=document.createElement('a');a.href=result.url;a.download='';document.body.append(a);a.click();a.remove();toast('Excel 已生成，并保存为本次旅程的结算记录');});
  $('copy').onclick=safe(async()=>{const text=[`${trip.name} · ${trip.start} 至 ${trip.end}`,`共同消费 ¥ ${money(summary.total)}`,...summary.transfers.map(t=>`${name(t.from_id)} → ${name(t.to_id)}：¥ ${money(t.cents)}`),...(summary.transfers.length?[]:['无需转账'])].join('\n');if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);else{const area=document.createElement('textarea');area.value=text;document.body.append(area);area.select();if(!document.execCommand('copy'))throw new Error('请手动复制页面转账建议');area.remove();}toast('转账清单已复制');});
  if(!ownMember)return;
  document.querySelectorAll('input[name="bill-kind"]').forEach(el=>{el.onchange=()=>{$('split-field').hidden=document.querySelector('input[name="bill-kind"]:checked').value==='private';};});
  $('bill-form').onsubmit=safe(async()=>{
    const body={title:$('bill-title').value,amount:$('bill-amount').value,date:$('bill-date').value,payer:Number($('bill-payer').value),kind:document.querySelector('input[name="bill-kind"]:checked').value,participants:[...$('participants').querySelectorAll('input:checked')].map(el=>Number(el.value))};
    await api(`/api/trips/${current}/bills`,'POST',body);await showTrip(current);toast('账单已保存，结算已更新');
  });
  $('upload-form').onsubmit=safe(async()=>{
    const file=$('statement-file').files[0];if(!file)throw new Error('请选择文件');if(file.size>5*1024*1024)throw new Error('文件不能超过 5 MB');
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});
    const report=await api(`/api/trips/${trip.id}/upload`,'POST',{name:file.name,data});await showTrip(trip.id);const r=report.report;
    $('upload-report').innerHTML=`<div class="report"><strong>已导入 ${report.inserted} 笔待确认账单</strong><p>重复 ${report.duplicate} · 日期范围外 ${r.outside} · 非支出 ${r.non_expense} · 退款/失败 ${r.refund} · 资金往来 ${r.transfer} · 无效 ${r.invalid}</p>${r.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}</div>`;
    $('bill-filter').value='pending';renderBills();toast('解析完成，请逐笔确认');
  });
  if($('legacy-preview')) $('legacy-preview').onclick=safe(legacyPreview);
}
// Inline edits never reuse or reset the manual-entry form above.
function billEditor(b) {
  const shared=b.kind!=='private';
  return `<form id="inline-bill-form" class="bill-editor"><div class="section-head"><h3>编辑这笔账单</h3><span class="muted">仅本人可修改</span></div><div class="form-grid">${field('消费项目','edit-title','text','maxlength="80"')}${field('金额（元）','edit-amount','text','inputmode="decimal"')}${field('交易日期','edit-date','date',`min="${currentData.trip.start}" max="${currentData.trip.end}"`)}<label>实际垫付者<select id="edit-payer" ${b.source_file?'disabled':''}>${memberOptions(b.payer)}</select></label></div><fieldset><legend>消费类型</legend><label class="radio"><input type="radio" name="edit-kind" value="shared" ${shared?'checked':''}>共同消费</label><label class="radio"><input type="radio" name="edit-kind" value="private" ${shared?'':'checked'}>私人消费 · 不结算</label></fieldset><fieldset id="edit-split" ${shared?'':'hidden'}><legend>参与分摊的成员</legend><div id="edit-participants" class="participants">${participants(b.participants.length?b.participants:currentData.members.filter(m=>m.enrolled!==0).map(m=>m.id))}</div></fieldset><p class="selection-note">编辑暂存在当前页面，点击账单上的“上传”后同步并确认。</p><div class="actions"><button class="primary compact">保存编辑</button><button id="inline-cancel" type="button" class="secondary">取消</button></div></form>`;
}
function manualDraft() {
  if(!$('bill-form'))return null;
  return {title:$('bill-title').value,amount:$('bill-amount').value,date:$('bill-date').value,payer:$('bill-payer').value,kind:document.querySelector('input[name="bill-kind"]:checked').value,participants:[...$('participants').querySelectorAll('input:checked')].map(el=>Number(el.value))};
}
async function refreshBillView() {
  const draft=manualDraft(),filter=$('bill-filter').value,scroll=window.scrollY;
  await showTrip(current);
  if(draft && $('bill-form')) {
    $('bill-title').value=draft.title;$('bill-amount').value=draft.amount;$('bill-date').value=draft.date;$('bill-payer').value=draft.payer;
    document.querySelector(`input[name="bill-kind"][value="${draft.kind}"]`).checked=true;
    $('participants').innerHTML=participants(draft.participants);$('split-field').hidden=draft.kind==='private';
  }
  $('bill-filter').value=filter;renderBills();window.scrollTo(0,scroll);
}
function visibleBills() {
  const filter=$('bill-filter').value;
  return currentData.bills.filter(b=>filter==='all'||filter==='own'&&b.owner===me.id||filter==='pending'&&b.owner===me.id&&b.kind==='pending'||filter==='shared'&&b.kind==='shared'||filter==='private'&&b.owner===me.id&&b.kind==='private');
}
function billBody(b) {
  const ids=currentData.members.filter(m=>m.enrolled!==0).map(m=>m.id);
  const parts=b.kind==='pending'?b.participants.filter(id=>ids.includes(id)):b.participants;
  return billDrafts.get(b.id)||{title:b.title,amount:money(b.cents),date:b.occurred.slice(0,10),payer:b.payer,kind:b.kind==='private'?'private':'shared',participants:b.participants.length?parts:ids};
}
function selectedOwn() { return currentData.bills.filter(b=>selectedBills.has(b.id)&&b.owner===me.id); }
function uploadBills(bills) {
  if(!bills.length)return;
  if(bills.length>200){toast('每次最多上传 200 笔，请减少选择');return;}
  const items=bills.map(b=>({...billBody(b),id:b.id,version:b.version}));
  const shared=items.filter(b=>b.kind==='shared').length;
  confirmAction(`上传并确认 ${items.length} 笔账单？`,`共同消费 ${shared} 笔将按所选成员纳入 AA；私人消费 ${items.length-shared} 笔不结算。尚未编辑的待确认账单默认由全体旅程成员分摊。`,async()=>{
    await api(`/api/trips/${current}/batch`,'POST',{items});
    items.forEach(b=>{billDrafts.delete(b.id);selectedBills.delete(b.id);});
    await refreshBillView();toast(`已上传 ${items.length} 笔，结算已更新`);
  });
}
function renderBatchTools(list) {
  const own=list.filter(b=>b.owner===me.id),selected=selectedOwn();
  const all=own.length>0&&own.every(b=>selectedBills.has(b.id));
  $('batch-tools').innerHTML=own.length?`<div class="selection-bar"><label><input id="select-all" type="checkbox" ${all?'checked':''}>全选当前列表</label><span class="selection-count">已选 ${selected.length} 笔</span><button id="batch-edit" class="row-button" ${selected.length?'':'disabled'}>批量编辑</button><button id="batch-upload" class="row-button upload" ${selected.length?'':'disabled'}>批量上传</button></div>${batchEditing?`<form id="batch-form" class="bill-editor batch-editor"><h3>统一编辑 ${selected.length} 笔账单</h3><p class="selection-note">仅统一消费类型与分摊成员，保留各笔名称、金额、日期及垫付者。保存编辑后仍需点击上传。</p><fieldset><legend>消费类型</legend><label class="radio"><input type="radio" name="batch-kind" value="shared" checked>共同消费</label><label class="radio"><input type="radio" name="batch-kind" value="private">私人消费</label><label class="radio"><input type="radio" name="batch-kind" value="keep">保持各笔类型</label></fieldset><fieldset id="batch-split"><legend>参与分摊的成员</legend><label class="radio"><input id="batch-keep-parts" type="checkbox">保持各笔原分摊成员</label><div id="batch-participants" class="participants">${participants(currentData.members.filter(m=>m.enrolled!==0).map(m=>m.id))}</div></fieldset><div class="actions"><button class="primary compact">保存所选编辑</button><button type="button" id="batch-cancel" class="secondary">取消</button></div></form>`:''}`:'';
  if(!own.length)return;
  $('select-all').indeterminate=!all&&own.some(b=>selectedBills.has(b.id));
  $('select-all').onchange=e=>{own.forEach(b=>e.target.checked?selectedBills.add(b.id):selectedBills.delete(b.id));batchEditing=false;renderBills();};
  $('batch-edit').onclick=()=>{editing=null;batchEditing=true;renderBills();};
  $('batch-upload').onclick=()=>uploadBills(selectedOwn());
  if(!batchEditing)return;
  const states=selected.map(billBody),kindSet=new Set(states.map(b=>b.kind));
  const commonKind=kindSet.size===1?states[0].kind:'keep';
  const key=parts=>[...parts].sort((a,b)=>a-b).join(',');
  const sameParts=states.length>0&&states.every(b=>key(b.participants)===key(states[0].participants));
  document.querySelector(`[name="batch-kind"][value="${commonKind}"]`).checked=true;
  $('batch-split').hidden=commonKind==='private';
  $('batch-keep-parts').checked=!sameParts;
  $('batch-participants').innerHTML=participants(sameParts?states[0].participants:[]);
  $('batch-participants').hidden=!sameParts;
  $('batch-keep-parts').onchange=()=>{$('batch-participants').hidden=$('batch-keep-parts').checked;};
  $('batch-cancel').onclick=()=>{batchEditing=false;renderBills();};
  document.querySelectorAll('[name="batch-kind"]').forEach(el=>{el.onchange=()=>{$('batch-split').hidden=document.querySelector('[name="batch-kind"]:checked').value==='private';};});
  $('batch-form').onsubmit=safe(async()=>{
    const kind=document.querySelector('[name="batch-kind"]:checked').value,parts=[...$('batch-participants').querySelectorAll('input:checked')].map(el=>Number(el.value));
    const keepParts=$('batch-keep-parts').checked;
    const changes=selectedOwn().map(b=>{
      const old=billBody(b),nextKind=kind==='keep'?old.kind:kind,nextParts=keepParts?old.participants:parts;
      if(nextKind==='shared'&&!nextParts.length)throw new Error('共同消费至少选择一位分摊成员');
      return [b.id,{...old,kind:nextKind,participants:nextKind==='private'?[]:nextParts}];
    });
    changes.forEach(([id,body])=>billDrafts.set(id,body));
    batchEditing=false;renderBills();toast('已保存编辑，点击上传后同步账单');
  });
}
function renderBills() {
  const list=visibleBills();
  const visible=new Set(list.map(b=>b.id));
  [...selectedBills].forEach(id=>{if(!visible.has(id))selectedBills.delete(id);});
  if(!list.some(b=>b.id===editing?.id))editing=null;
  renderBatchTools(list);
  $('bill-list').innerHTML=list.length?list.map(original=>{
    const draft=billDrafts.get(original.id),b=draft?{...original,title:draft.title,cents:Math.round(Number(draft.amount)*100),occurred:draft.date,payer:draft.payer,participants:draft.participants}:original;
    return `<article class="bill-row ${editing?.id===b.id?'is-editing':''}"><div class="bill-summary"><label class="bill-select">${b.owner===me.id?`<input type="checkbox" data-select="${b.id}" aria-label="选择账单：${esc(b.title)}" ${selectedBills.has(b.id)?'checked':''}>`:''}</label><div class="bill-info"><div class="bill-heading"><strong title="${esc(b.title)}">${esc(b.title)}</strong><span class="tag ${draft?'pending':b.kind}">${draft?'待上传':b.kind==='shared'?'共同 AA':b.kind==='private'?'私人':'待确认'}</span></div><p>${esc(b.occurred)} · ${esc(name(b.payer))} 垫付 · ${b.source_file?'导入':'手动'}</p>${(draft?.kind||b.kind)==='shared'?`<p>分摊：${b.participants.map(id=>esc(name(id))).join('、')}</p>`:''}</div><div class="bill-right"><strong>¥ ${money(b.cents)}</strong>${b.owner===me.id?`<div><button class="row-button" data-edit="${b.id}" aria-expanded="${editing?.id===b.id}">${editing?.id===b.id?'收起':'编辑'}</button><button class="row-button upload" data-upload="${b.id}" ${b.kind==='pending'||draft?'':'disabled'}>${b.kind==='pending'||draft?'上传':'已上传'}</button><button class="row-button danger" data-delete="${b.id}">删除</button></div>`:'<small>其他成员录入</small>'}</div></div>${editing?.id===b.id?billEditor({...b,kind:draft?.kind||b.kind}):''}</article>`;
  }).join(''):empty('没有符合条件的账单');
  document.querySelectorAll('[data-select]').forEach(el=>{el.onchange=()=>{const id=Number(el.dataset.select);el.checked?selectedBills.add(id):selectedBills.delete(id);batchEditing=false;renderBatchTools(list);};});
  document.querySelectorAll('[data-edit]').forEach(button=>{button.onclick=()=>{const b=currentData.bills.find(b=>b.id===Number(button.dataset.edit));if(b.owner!==me.id)return;editing=editing?.id===b.id?null:b;batchEditing=false;renderBills();};});
  document.querySelectorAll('[data-upload]').forEach(button=>{button.onclick=()=>uploadBills([currentData.bills.find(b=>b.id===Number(button.dataset.upload))]);});
  if(editing) {
    const draft=billBody(editing);
    $('edit-title').value=draft.title;$('edit-amount').value=draft.amount;$('edit-date').value=draft.date;$('edit-payer').value=draft.payer;
    $('edit-participants').innerHTML=participants(draft.participants);
    $('inline-cancel').onclick=()=>{editing=null;renderBills();};
    document.querySelectorAll('input[name="edit-kind"]').forEach(el=>{el.onchange=()=>{$('edit-split').hidden=document.querySelector('input[name="edit-kind"]:checked').value==='private';};});
    $('inline-bill-form').onsubmit=safe(async()=>{
      const body={title:$('edit-title').value,amount:$('edit-amount').value,date:$('edit-date').value,payer:Number($('edit-payer').value),kind:document.querySelector('input[name="edit-kind"]:checked').value,participants:[...$('edit-participants').querySelectorAll('input:checked')].map(el=>Number(el.value))};
      if(body.kind==='shared'&&!body.participants.length)throw new Error('共同消费至少选择一位分摊成员');
      if(!/^\d+(?:\.\d{1,2})?$/.test(body.amount)||Number(body.amount)<=0)throw new Error('请输入有效金额，最多两位小数');
      billDrafts.set(editing.id,body);editing=null;renderBills();toast('已保存编辑，点击上传后同步账单');
    });
  }
  document.querySelectorAll('[data-delete]').forEach(button=>{button.onclick=()=>confirmAction('删除自己的这笔账单？','会重新计算当前旅程；原始上传文件仍保留。',async()=>{const id=Number(button.dataset.delete);await api(`/api/bills/${id}`,'DELETE',{});billDrafts.delete(id);selectedBills.delete(id);await refreshBillView();});});
}
window.addEventListener('beforeunload',event=>{if(billDrafts.size){event.preventDefault();event.returnValue='';}});
async function legacyPreview() {
  const raw=localStorage.getItem('travel-aa-v1');if(!raw)throw new Error('当前地址下没有旧版账本。旧 localhost 数据不会自动跨地址迁移。');
  let old;try{old=JSON.parse(raw);}catch{throw new Error('旧账本数据无法读取，原数据未改变');}
  if(!Array.isArray(old.members)||!Array.isArray(old.bills))throw new Error('旧账本格式无效');
  $('legacy-migration').innerHTML=`<div class="report"><strong>${esc(old.name||'旧账本')}：${old.bills.length} 笔</strong><p>当前目标：${esc(currentData.trip.name)}。逐一映射原成员；仅迁移被映射为你自己的垫付账单。</p><form id="migrate-form">${field('这些旧账单的记账日期','migration-date','date',`min="${currentData.trip.start}" max="${currentData.trip.end}" value="${currentData.trip.start}"`)}${old.members.map((m,i)=>`<label>${esc(m.name)} 对应<select data-old-index="${i}"><option value="">请选择成员</option>${memberOptions(-1)}</select></label>`).join('')}<button class="secondary">核对后迁移本人账单</button></form></div>`;
  $('migrate-form').onsubmit=safe(async()=>{
    const mapping={};document.querySelectorAll('[data-old-index]').forEach(el=>{if(!el.value)throw new Error('请映射所有旧成员');mapping[String(old.members[Number(el.dataset.oldIndex)].id)]=Number(el.value);});
    const bills=old.bills.filter(b=>mapping[String(b.payer)]===me.id),date=$('migration-date').value;
    if(!bills.length)throw new Error('没有属于本人垫付的旧账单');
    confirmAction(`迁移 ${bills.length} 笔到 ${currentData.trip.name}？`,`统一记账日期为 ${date}。原浏览器账本仍然保留，不会混入其他旅程。`,async()=>{const result=await api(`/api/trips/${current}/migrate`,'POST',{mapping,bills,date});await showTrip(current);toast(`已迁移 ${result.inserted} 笔，跳过重复 ${result.duplicate} 笔`);});
  });
}
async function admin(focus='') {
  const request=++viewRequest;
  const [u,t,f,r]=await Promise.all([api('/api/admin/users'),api('/api/trips'),api('/api/admin/files'),api('/api/admin/records')]);
  if(request!==viewRequest)return;
  current=null;users=u.users;trips=t.trips;
  $('app').innerHTML=`<section class="page-head"><div><p class="eyebrow">ADMINISTRATION</p><h1>管理后台</h1><p class="muted">账号、旅程、文件与每次结算。管理员不能替别人修改分摊账单。</p></div></section><div class="admin-grid"><section class="card"><h2>创建成员账号</h2><form id="user-form">${field('登录名','new-username','text','pattern="[a-zA-Z0-9_-]{3,32}"')}${field('显示姓名','new-name','text','maxlength="30"')}${field('初始密码（至少 6 字符）','new-user-password','password','minlength="6" maxlength="128" autocomplete="new-password"')}<button class="primary">创建成员</button></form></section><section class="card" id="trip-create"><h2>新建独立旅程</h2><form id="trip-form">${field('旅程名称','trip-name','text','maxlength="60"')}<div class="form-grid">${field('开始日期','trip-start','date')}${field('结束日期','trip-end','date')}</div><fieldset><legend>本次旅程成员（按以下顺序分配尾差）</legend><div id="trip-members" class="participants">${users.filter(u=>u.active).map(u=>`<label class="participant"><input type="checkbox" value="${u.id}"><span>${esc(u.name)} (${esc(u.username)})</span></label>`).join('')}</div></fieldset><button class="primary">创建旅程</button></form></section></div><section class="card"><h2>成员账号</h2><div class="table-scroll"><table><thead><tr><th>姓名 / 登录名</th><th>角色</th><th>状态</th><th>管理</th></tr></thead><tbody>${users.map(u=>`<tr><td>${esc(u.name)} <small>${esc(u.username)}</small></td><td>${u.role==='admin'?'管理员':'成员'}</td><td>${u.active?'正常':'停用'}</td><td>${u.role!=='admin'?`<button class="text-button" data-toggle="${u.id}">${u.active?'停用':'启用'}</button> <button class="text-button" data-reset="${u.id}">重置密码</button>`:'使用个人修改密码入口'}</td></tr>`).join('')}</tbody></table></div><div id="reset-panel"></div><p class="footnote">停用账号会立即撤销会话。历史成员和账目保留，避免破坏结算。</p></section><section class="card"><h2>各次旅程的当前 AA 结算</h2><div class="trip-grid">${trips.length?trips.map(t=>`<button class="trip-card small" data-trip="${t.id}"><h3>${esc(t.name)}</h3><p>${t.start} 至 ${t.end}</p><span>查看账目和结算 →</span></button>`).join(''):empty('暂无旅程')}</div></section><section class="card"><h2>上传与生成文件</h2><p class="muted">原始文件包含个人信息。删除文件不删除已解析账单，也不改变历史结算快照。</p><div class="table-scroll"><table><thead><tr><th>旅程 / 文件</th><th>成员</th><th>类型 / 大小</th><th>操作</th></tr></thead><tbody>${f.files.map(file=>`<tr><td>${esc(file.trip_name)}<br><small>${esc(file.name)}</small></td><td>${esc(file.owner_name)}</td><td>${file.kind==='upload'?'上传账单':'Excel结算'} · ${(file.size/1024).toFixed(1)} KB</td><td>${file.deleted?'已删除':`<a href="/api/files/${file.id}" class="text-link" download>下载</a> <button class="text-button danger" data-file-delete="${file.id}">删除文件</button>`}</td></tr>`).join('')||'<tr><td colspan="4">暂无文件</td></tr>'}</tbody></table></div></section><section class="card"><h2>每次生成的结算记录</h2><p class="muted">导出时保存快照，后续修改账单不会覆盖这些记录。</p>${r.records.map(record=>`<details class="record"><summary>${esc(record.trip_name)} · ${esc(new Date(record.created).toLocaleString('zh-CN'))} · ¥ ${money(record.snapshot.summary.total)}</summary><p>旅程日期：${record.snapshot.trip.start} 至 ${record.snapshot.trip.end}</p>${record.snapshot.summary.transfers.map(t=>`<p>${esc(record.snapshot.summary.rows.find(m=>m.id===t.from_id)?.name)} → ${esc(record.snapshot.summary.rows.find(m=>m.id===t.to_id)?.name)}：¥ ${money(t.cents)}</p>`).join('')||'<p>无需转账</p>'}</details>`).join('')||empty('导出 Excel 后生成结算快照')}</section>`;
  $('app').insertAdjacentHTML('beforeend',`<section class="card" id="members-manager"><h2>调整指定旅程的成员</h2><p class="muted">新增或移除访问成员，保留原账单、文件与历史分摊。</p>${trips.length?`<label class="filter">旅程<select id="members-trip">${trips.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select><button id="open-members" class="secondary compact">调整成员</button></label><div id="members-panel"></div>`:empty('先创建一段旅程')}</section>`);
  if($('open-members'))$('open-members').onclick=safe(showMembershipEditor);
  $('user-form').onsubmit=safe(async()=>{await api('/api/admin/users','POST',{username:$('new-username').value,name:$('new-name').value,password:$('new-user-password').value});await admin();toast('成员账号已创建');});
  $('trip-form').onsubmit=safe(async()=>{const result=await api('/api/trips','POST',{name:$('trip-name').value,start:$('trip-start').value,end:$('trip-end').value,members:[...$('trip-members').querySelectorAll('input:checked')].map(el=>Number(el.value))});await showTrip(result.id);toast('新旅程已创建');});
  document.querySelectorAll('[data-trip]').forEach(b=>{b.onclick=safe(()=>showTrip(Number(b.dataset.trip)));});
  document.querySelectorAll('[data-toggle]').forEach(b=>{b.onclick=()=>{const u=users.find(u=>u.id===Number(b.dataset.toggle));confirmAction(`${u.active?'停用':'启用'} ${u.name}？`,'历史账单保留；已有会话将撤销。',async()=>{await api(`/api/admin/users/${u.id}`,'PATCH',{active:!u.active});await admin();});};});
  document.querySelectorAll('[data-reset]').forEach(b=>{b.onclick=()=>{const u=users.find(u=>u.id===Number(b.dataset.reset));$('reset-panel').innerHTML=`<form id="reset-form" class="report"><h3>重置 ${esc(u.name)} 的密码</h3>${field('新密码','reset-password','password','minlength="6" maxlength="128" autocomplete="new-password"')}<button class="secondary">重置并撤销旧会话</button></form>`;$('reset-form').onsubmit=safe(async()=>{await api(`/api/admin/users/${u.id}`,'PATCH',{password:$('reset-password').value});await admin();toast('密码已重置，请私下通知该成员');});};});
  document.querySelectorAll('[data-file-delete]').forEach(b=>{b.onclick=()=>confirmAction('删除这个网站文件？','将删除服务器上的原文件，无法恢复；已解析账单和结算快照保留。',async()=>{await api(`/api/admin/files/${b.dataset.fileDelete}`,'DELETE',{});await admin();});});
  if(focus==='trip')$('trip-create').scrollIntoView({behavior:'smooth'});
  if(focus==='members')$('members-manager').scrollIntoView({behavior:'smooth'});
}
async function showMembershipEditor() {
  const tid=Number($('members-trip').value),data=await api(`/api/trips/${tid}`);
  if(!$('members-panel')||Number($('members-trip').value)!==tid)return;
  const before=data.members.filter(m=>m.enrolled!==0).map(m=>m.id);
  const candidates=users.filter(u=>u.active||before.includes(u.id));
  $('members-panel').innerHTML=`<form id="edit-members-form" class="bill-editor"><h3>${esc(data.trip.name)} · 成员</h3><p class="selection-note">移除成员后，该成员不能再访问此旅程；原账单、分摊、文件和结算记录不会删除或改算。新增成员不会自动分摊以前的消费。至少保留一位成员。</p><div id="edit-trip-members" class="participants">${candidates.map(u=>`<label class="participant"><input type="checkbox" value="${u.id}" ${before.includes(u.id)?'checked':''}><span>${esc(u.name)}${u.active?'':'（停用）'}</span></label>`).join('')}</div><div class="actions"><button class="primary compact">保存成员</button><button id="members-cancel" type="button" class="secondary">取消</button></div></form>`;
  $('members-cancel').onclick=()=>{$('members-panel').innerHTML='';};
  $('members-trip').onchange=()=>{$('members-panel').innerHTML='';};
  $('edit-members-form').onsubmit=safe(async()=>{
    const ids=[...$('edit-trip-members').querySelectorAll('input:checked')].map(el=>Number(el.value));
    if(!ids.length)throw new Error('至少保留一位旅程成员');
    const added=ids.filter(id=>!before.includes(id)).length,removed=before.filter(id=>!ids.includes(id)).length;
    confirmAction(`保存 ${data.trip.name} 的成员调整？`,`新增 ${added} 位，移除 ${removed} 位。既有账单和文件保持不变。`,async()=>{
      await api(`/api/trips/${tid}/members`,'PATCH',{members:ids,members_before:before});
      await admin('members');toast('旅程成员已更新，历史账目与文件保留');
    });
  });
}
boot().catch(e=>{$('app').innerHTML=`<section class="card"><h1>暂时无法连接服务</h1><p>${esc(e.message)}</p><p>请稍后刷新页面；若仍无法连接，请联系管理员。</p></section>`;});
