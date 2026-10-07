const $ = s => document.querySelector(s);
let me, owned = [], selected, registering = false, companyVerificationState = null;
const notice = message => { $('#notice').textContent = message; };
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
async function api(path, method = 'GET', data) {
  const response = await fetch(path, { method, credentials: 'same-origin', headers: data instanceof FormData ? {'X-Requested-With':'Raeburn'} : { 'Content-Type': 'application/json', 'X-Requested-With': 'Raeburn' }, body: data === undefined ? undefined : data instanceof FormData ? data : JSON.stringify(data) });
  const value = await response.json();
  if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'Check the information you entered.');
  return value;
}
async function act(fn) { try { notice(''); await fn(); } catch (error) { notice(error.message); } }
function fields(form) { return Object.fromEntries(new FormData(form)); }
function button(text, fn, secondary = false) { const b = element('button', text, secondary ? 'secondary' : ''); b.type = 'button'; b.onclick = () => act(async () => { b.disabled = true; try { await fn(); } finally { b.disabled = false; } }); return b; }
function show(view) { document.querySelectorAll('.view').forEach(e => e.hidden = e.id !== view); document.querySelectorAll('nav button').forEach(e => e.classList.toggle('active', e.dataset.view === view)); if (view === 'inbox') act(loadThreads); if (view === 'ai') act(loadAI); if (view === 'admin') act(loadAdmin); }
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => show(b.dataset.view));
$('#auth-toggle').onclick = () => { registering = !registering; $('#register-fields').hidden = !registering; $('#auth-submit').textContent = registering ? 'Create account' : 'Sign in'; $('#auth-toggle').textContent = registering ? 'I already have an account' : 'Create an account'; };
$('#auth-form').onsubmit = e => { e.preventDefault(); act(async () => { const data = fields(e.target); if (registering) { data.accept_terms = Boolean(data.accept_terms); await api('/api/register', 'POST', data); } await api('/api/login', 'POST', { email: data.email, password: data.password, totp: data.totp || '' }); await load(); }); };
$('#logout').onclick = () => act(async () => { await api('/api/logout', 'POST'); location.reload(); });
async function load() {
  me = await api('/api/me'); owned = []; try { owned = await api('/api/numbers'); } catch(error) { notice(error.message); }
  $('#auth').hidden = true; $('#workspace').hidden = false; $('#logout').hidden = false; $('#admin-nav').hidden = !me.platform_admin;
  const submitted = Boolean(me.tenant.legal_name && me.tenant.address);
  let verification = null;
  try { verification = await loadCompanyVerification(); } catch(error) { notice(error.message); }
  const checks = verification?.checks || {};
  const raeburnVerified = Boolean(checks.business_email && checks.dns && checks.company_register && checks.director_authority && verification?.status === 'verified');
  const telephoneApproved = me.tenant.status === 'approved';
  const pendingReview = submitted && !raeburnVerified;
  $('#profile-review').hidden = !(pendingReview || raeburnVerified);
  $('#profile-form').hidden = pendingReview || raeburnVerified;
  $('#profile-review-title').textContent = raeburnVerified ? 'Your company is verified' : 'Thank you — your company details are with us';
  $('#profile-review-message').textContent = raeburnVerified
    ? (telephoneApproved
        ? 'Your business and telephone approval are complete. Your service can now be activated.'
        : 'Your Raeburn verification is complete. Your workspace is ready while telephone approval continues automatically in the background.')
    : 'We are verifying your company details. You can check progress here and complete any remaining verification steps.';
  $('#company').textContent = me.tenant.name;
  $('#review-status').textContent = telephoneApproved ? 'Telephone approved' : raeburnVerified ? 'Verified · telephone approval in progress' : pendingReview ? 'Verification in progress' : 'Details needed';
  $('#billing-status').textContent = ({unpaid:'No active subscriptions',active:'Active subscription',past_due:'Payment needs attention',canceled:'Subscription ended',cancelled:'Subscription ended',pending:'Subscription processing',incomplete:'Subscription setup incomplete',trialing:'Trial subscription'})[me.tenant.billing_status] || 'No active subscriptions';
  $('#number-count').textContent = owned.length;
  $('#next-step').textContent = !raeburnVerified
    ? 'Complete your company and director verification to unlock setup.'
    : me.tenant.billing_status !== 'active'
      ? (telephoneApproved
          ? 'Choose your subscription, then select your UK business number.'
          : 'Your workspace is ready. Choose your subscription and set up your service while telephone approval continues in the background.')
      : owned.length
        ? 'Set up call forwarding and start managing your conversations.'
        : telephoneApproved
          ? 'Search for your UK number and submit an activation request.'
          : 'Choose your preferred UK number now. We will request it automatically as soon as telephone approval is complete.';
  for (const key of ['legal_name', 'address', 'registration_number']) $('#profile-form').elements[key].value = me.tenant[key] || '';
  $('#account-verification').textContent = `Email: ${me.email_verified ? 'verified' : 'verification required'} · Authenticator: ${me.mfa_enabled ? 'enabled' : 'not enabled'}`;
  $('#setup-mfa').hidden = me.mfa_enabled;
  $('#resend-verification').hidden = me.email_verified;
  if (me.mfa_enabled) {
    $('#mfa-enrolment').hidden = true;
    $('#mfa-secret').textContent = '';
    $('#mfa-qr').replaceChildren();
    $('#mfa-form').reset();
  }
  renderNumbers();
  if (me.email_verified) { const usage = await api('/api/usage'); $('#usage-summary').textContent = `${usage.sms_segments}/${usage.sms_allowance} SMS segments · ${usage.voice_minutes}/${usage.voice_allowance} call minutes this month`; }
  try { await loadOrders(); } catch(error) { notice(error.message); }
  try { await loadCustomerOS(); } catch(error) { notice(error.message); }
  show(me.email_verified ? 'overview' : 'account');
}
$('#edit-profile').onclick = () => { $('#profile-review').hidden = true; $('#profile-form').hidden = false; $('#profile-form').elements.legal_name.focus(); };
$('#profile-form').onsubmit = e => { e.preventDefault(); act(async () => { await api('/api/profile', 'PUT', fields(e.target)); await load(); show('account'); notice('Thank you — we’ve received your company details. Your activation is now pending review.'); }); };
$('#search-form').onsubmit = e => { e.preventDefault(); act(async () => {
  const results = await api('/api/numbers/search?' + new URLSearchParams(fields(e.target))); $('#search-results').replaceChildren();
  const telephoneApproved = me?.tenant?.status === 'approved';
  for (const n of results) {
    const card = element('article');
    card.append(
      element('h2', n.phone),
      element('p', [n.voice && 'Voice', n.sms && 'SMS'].filter(Boolean).join(' · ') || 'Capabilities confirmed at activation'),
      button(telephoneApproved ? 'Request activation' : 'Choose as preferred number', async () => {
        const order = await api('/api/orders', 'POST', {phone:n.phone,type:n.type,request_key:crypto.randomUUID()});
        await loadOrders();
        notice(order.message || (telephoneApproved
          ? 'Activation requested. Follow its status below.'
          : 'Preferred number saved. We will request it automatically after telephone approval.'));
      })
    );
    $('#search-results').append(card);
  }
  if (!results.length) notice('No available numbers match these digits.');
}); };
async function loadCustomerOS() {
  const brief = await api('/api/customer-os/executive-brief');
  $('#executive-brief-summary').textContent = brief.summary;
  $('#brief-interactions').textContent = brief.interactions_24h;
  $('#brief-promises').textContent = brief.open_promises + (brief.overdue_promises ? ' · ' + brief.overdue_promises + ' overdue' : '');
  $('#brief-recoveries').textContent = brief.open_recoveries;
  $('#brief-risk').textContent = brief.customers_at_risk;
  $('#brief-revenue').textContent = '£' + (brief.potential_revenue / 100).toFixed(2);

  const recoveries = await api('/api/customer-os/recovery');
  $('#recovery-list').replaceChildren();
  for (const item of recoveries) {
    const card = element('article', undefined, 'owned');
    const detail = item.detail || {};
    const title = item.kind === 'missed_call'
      ? 'Missed call' + (detail.from ? ' from ' + detail.from : '')
      : item.kind === 'promise_overdue'
        ? 'Promise overdue'
        : item.kind === 'guarantee'
          ? 'Service promise needs attention'
          : item.kind.replaceAll('_', ' ');
    const due = new Date(item.due_at).toLocaleString();
    card.append(
      element('h3', title),
      element('p', 'Due ' + due),
      button('Mark resolved', async () => {
        await api('/api/customer-os/recovery/' + item.id + '/resolve', 'POST');
        await loadCustomerOS();
        notice('Recovery item resolved.');
      }, true)
    );
    $('#recovery-list').append(card);
  }
  if (!recoveries.length) $('#recovery-list').append(element('p', 'Nothing needs recovery right now.'));

  const guarantees = await api('/api/customer-os/guarantees');
  $('#guarantee-list').replaceChildren();
  for (const rule of guarantees) {
    const card = element('article', undefined, 'owned');
    card.append(
      element('h3', rule.name),
      element('p', 'Target: ' + rule.max_minutes + ' minutes · ' + rule.action.replaceAll('_', ' '))
    );
    $('#guarantee-list').append(card);
  }
  if (!guarantees.length) $('#guarantee-list').append(element('p', 'No service promises configured yet.'));
}

$('#guarantee-form').onsubmit = e => {
  e.preventDefault();
  act(async () => {
    const data = fields(e.target);
    data.max_minutes = Number(data.max_minutes);
    data.enabled = true;
    await api('/api/customer-os/guarantees', 'POST', data);
    e.target.reset();
    e.target.elements.max_minutes.value = 10;
    await loadCustomerOS();
    notice('Service promise added. Raeburn will now track it automatically.');
  });
};

function renderNumbers() {
  $('#owned-numbers').replaceChildren(); $('#send-number').replaceChildren();
  for (const n of owned) {
    if (n.sms) { const option = element('option', n.phone); option.value = n.id; $('#send-number').append(option); }
    const card = element('article', undefined, 'owned'); card.append(element('h3', n.phone), element('p', `WhatsApp: ${n.whatsapp} · RCS: ${n.rcs}`));
    if (n.voice) { const label = element('label', 'Simple call forwarding destination (used when the call menu is off)'); const input = element('input'); input.type='tel'; input.value=n.forwarding; input.placeholder='+447700900000'; label.append(input); card.append(label, button('Save forwarding', async () => { await api(`/api/numbers/${n.id}/forwarding`, 'PUT', {destination:input.value}); notice('Call forwarding updated.'); })); }
    $('#owned-numbers').append(card);
  }
  if (!owned.length) $('#owned-numbers').append(element('p', 'Your activated numbers will appear here.'));
  const voiceNumbers = owned.filter(n => n.voice);
  const routingPanel = $('#call-routing-panel');
  const routingSelect = $('#call-routing-number');
  if (routingPanel && routingSelect) {
    routingPanel.hidden = !voiceNumbers.length;
    routingSelect.replaceChildren();
    for (const n of voiceNumbers) {
      const option = element('option', n.phone);
      option.value = n.id;
      routingSelect.append(option);
    }
    if (voiceNumbers.length) act(loadCallRouting);
  }
}

function addCallRouteRow(value = {}) {
  const container = $('#call-route-options');
  if (container.children.length >= 10) return notice('A call menu can have up to 10 choices.');
  const row = element('div', undefined, 'inline call-route-row');
  const digitLabel = element('label', 'Key');
  const digit = element('select');
  digit.name = 'route_digit';
  for (const d of ['1','2','3','4','5','6','7','8','9','0']) {
    const option = element('option', d);
    option.value = d;
    if (value.digit === d) option.selected = true;
    digit.append(option);
  }
  digitLabel.append(digit);
  const nameLabel = element('label', 'Team or person');
  const name = element('input');
  name.name = 'route_label';
  name.maxLength = 60;
  name.required = true;
  name.placeholder = 'Sales';
  name.value = value.label || '';
  nameLabel.append(name);
  const phoneLabel = element('label', 'Phone or mobile');
  const phone = element('input');
  phone.name = 'route_destination';
  phone.type = 'tel';
  phone.required = true;
  phone.pattern = '\\+44[0-9]{9,10}';
  phone.placeholder = '+447700900000';
  phone.value = value.destination || '';
  phoneLabel.append(phone);
  const remove = button('Remove', () => { row.remove(); }, true);
  row.append(digitLabel, nameLabel, phoneLabel, remove);
  container.append(row);
}

async function loadCallRouting() {
  const numberId = $('#call-routing-number').value;
  if (!numberId) return;
  const config = await api('/api/numbers/' + numberId + '/call-routing');
  const form = $('#call-routing-form');
  form.elements.enabled.checked = Boolean(config.enabled);
  form.elements.greeting.value = config.greeting || '';
  form.elements.fallback.value = config.fallback || '';
  form.elements.ring_seconds.value = String(config.ring_seconds || 20);
  $('#call-route-options').replaceChildren();
  for (const option of (config.options || [])) addCallRouteRow(option);
  if (!(config.options || []).length) {
    addCallRouteRow({digit:'1', label:'Sales', destination:''});
    addCallRouteRow({digit:'2', label:'Accounts', destination:''});
    addCallRouteRow({digit:'0', label:'Operator', destination:''});
  }
}

$('#call-routing-number').onchange = () => act(loadCallRouting);
$('#add-call-route').onclick = () => addCallRouteRow();
$('#call-routing-form').onsubmit = e => {
  e.preventDefault();
  act(async () => {
    const form = e.target;
    const rows = [...$('#call-route-options').querySelectorAll('.call-route-row')];
    const options = rows.map(row => ({
      digit: row.querySelector('[name="route_digit"]').value,
      label: row.querySelector('[name="route_label"]').value.trim(),
      destination: row.querySelector('[name="route_destination"]').value.trim(),
    })).filter(item => item.label || item.destination);
    await api('/api/numbers/' + $('#call-routing-number').value + '/call-routing', 'PUT', {
      enabled: form.elements.enabled.checked,
      greeting: form.elements.greeting.value,
      fallback: form.elements.fallback.value.trim(),
      ring_seconds: Number(form.elements.ring_seconds.value),
      options,
    });
    notice(form.elements.enabled.checked
      ? 'Call menu saved. Incoming callers will hear it when your telephone service is active.'
      : 'Call menu saved and switched off. Simple forwarding will be used instead.');
    await loadCallRouting();
  });
};

async function loadOrders() {
  const orders = await api('/api/orders');
  const labels = {
    awaiting_approval: 'Preferred number · waiting for telephone approval',
    queued: 'Activation queued',
    processing: 'Activating',
    active: 'Active',
    review: 'Activation needs attention',
  };
  $('#orders').replaceChildren();
  for (const o of orders) {
    const row = element('article', undefined, 'owned');
    const label = labels[o.status] || o.status;
    row.append(element('h3', o.phone), element('p', label + (o.error ? ' · ' + o.error : '')));
    $('#orders').append(row);
  }
}
const planDescriptions = {
  business: 'Business Number — £7.99 per month: a UK number with call forwarding. Choose Connect if you also need to send business SMS.',
  connect: 'Connect — £14.99 per month: a UK number, call forwarding, 50 outgoing SMS segments per month and a threaded inbox. Monthly usage allowances apply.',
  ai: 'AI Receptionist: Starter £26.99/month with 75 AI minutes, Business £47.99/month with 150 AI minutes, or Growth £69.99/month with 225 AI minutes. Each includes one UK number, AI call handling and business knowledge. Booking and email actions need connected integrations. Activation is being prepared, so this plan cannot be purchased yet.'
};
function updatePlanDescription() {
  const plan = $('#billing-form').elements.plan.value;
  $('#plan-description').textContent = planDescriptions[plan];
  $('#checkout-button').disabled = plan === 'ai';
  $('#checkout-button').textContent = plan === 'ai' ? 'AI activation pending' : 'Continue to secure checkout';
}
$('#billing-form').elements.plan.onchange = updatePlanDescription;
updatePlanDescription();
$('#billing-form').onsubmit = e => { e.preventDefault(); act(async () => { const value = await api('/api/billing/checkout', 'POST', fields(e.target)); location.assign(value.url); }); };
$('#billing-portal').onclick = () => act(async () => { const value = await api('/api/billing/portal', 'POST'); location.assign(value.url); });
async function loadThreads() {
  const threads = await api('/api/threads'); $('#threads').replaceChildren();
  for (const t of threads) { const b = button(t.peer, async () => { selected=t; $('#thread-title').textContent=t.peer; $('#send-form').elements.peer.value=t.peer; $('#send-number').value=t.number_id; await loadMessages(); }, true);
    if(t.conversation_id){b.append(element('small','Owner: '+t.mode));const controls=element('div');for(const mode of ['human','paused','ai'])controls.append(button(mode,async()=>{await api('/api/conversations/'+t.conversation_id+'/mode','PUT',{mode});await loadThreads();},true));$('#threads').append(controls);} b.classList.add('thread'); b.append(element('small', t.preview)); $('#threads').append(b); }
  if (!threads.length) $('#threads').append(element('p', 'No conversations yet. Start one using the message form.'));
}
async function loadMessages() {
  if (!selected) return;
  const messages = await api('/api/messages?' + new URLSearchParams({number_id:selected.number_id,peer:selected.peer,channel:selected.channel||"sms"})); $('#messages').replaceChildren();
  lastInboundId=messages.filter(m=>m.direction==='inbound').at(-1)?.id;
  for (const m of messages) { const bubble = element('div', undefined, 'bubble '+m.direction); bubble.append(element('span',m.body), element('small',`${m.status} · ${new Date(m.at).toLocaleString()}`)); $('#messages').append(bubble); }
  $('#messages').scrollTop = $('#messages').scrollHeight;
}
$('#send-form').onsubmit = e => { e.preventDefault(); act(async () => { const data=fields(e.target); data.consent_confirmed=Boolean(data.consent_confirmed); data.request_key=crypto.randomUUID();data.channel=selected?.channel||"sms"; await api('/api/messages','POST',data); selected={number_id:data.number_id,peer:data.peer,channel:data.channel}; e.target.elements.body.value=''; await loadThreads(); await loadMessages(); notice('Message queued for delivery.'); }); };
async function loadAdmin() {
  const tenants=await api('/api/admin/tenants'); $('#admin-customers').replaceChildren();
  for (const t of tenants) { const card=element('article',undefined,'customer'); card.append(element('h2',t.name),element('p',`${t.legal_name} · ${t.address} · ${t.status} · billing ${t.billing_status}`)); if (!t.connected) card.append(button('Connect telephone service',async()=>{await api(`/api/admin/tenants/${t.id}/connect`,'POST');await loadAdmin();}));
    const form=element('form'); const bundle=element('input'); bundle.placeholder='Approved application reference'; bundle.required=true; const address=element('input'); address.placeholder='Approved address reference (if required)'; const type=element('select'); for (const v of ['Local','Mobile','TollFree']) type.append(element('option',v)); const submit=element('button','Confirm approval'); submit.type='submit'; form.append(bundle,address,type,submit); form.onsubmit=e=>{e.preventDefault();act(async()=>{await api(`/api/admin/tenants/${t.id}/approve`,'POST',{bundle_sid:bundle.value,address_sid:address.value,type:type.value});await loadAdmin();});}; card.append(form); $('#admin-customers').append(card); }
}
if (!new URLSearchParams(location.search).get('token')) api('/api/me').then(load).catch(()=>{});
setInterval(()=>{if(me&&!$('#inbox').hidden)act(async()=>{await loadThreads();await loadMessages();});},15000);

$('#forgot-password').onclick = () => act(async () => { const email = $('#auth-form').elements.email.value; if (!email) throw Error('Enter your account email first.'); await api('/api/security/request/reset', 'POST', {email}); notice('If this account is eligible, a recovery email will arrive shortly.'); });
$('#resend-verification').onclick = () => act(async () => { await api('/api/security/request/verify','POST',{email:me.email}); notice('Verification email requested.'); });
function renderMfaQr(modules) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${modules.length} ${modules.length}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Scan this QR code with your authenticator app');
  svg.setAttribute('class', 'mfa-qr');
  const background = document.createElementNS(ns, 'rect');
  background.setAttribute('width', '100%'); background.setAttribute('height', '100%'); background.setAttribute('fill', 'white');
  const path = document.createElementNS(ns, 'path');
  let squares = '';
  modules.forEach((row, y) => row.forEach((dark, x) => { if (dark) squares += `M${x},${y}h1v1h-1z`; }));
  path.setAttribute('d', squares); path.setAttribute('fill', 'black');
  svg.append(background, path);
  $('#mfa-qr').replaceChildren(svg);
}
$('#setup-mfa').onclick = () => act(async () => {
  const button = $('#setup-mfa'); button.disabled = true;
  try {
    $('#mfa-qr').replaceChildren(); $('#mfa-secret').textContent=''; $('#mfa-enrolment').hidden=true;
    const value = await api('/api/security/mfa/setup', 'POST');
    renderMfaQr(value.qr_modules); $('#mfa-secret').textContent=value.secret; $('#mfa-enrolment').hidden=false;
  } finally { button.disabled = false; }
});
$('#mfa-form').onsubmit = e => { e.preventDefault(); act(async()=>{await api('/api/security/mfa/confirm','POST',fields(e.target)); $('#mfa-enrolment').hidden=true; $('#mfa-secret').textContent=''; $('#mfa-qr').replaceChildren(); location.assign('/?mfa=enabled');}); };
const actionParams = new URLSearchParams(location.search);
const action = actionParams.get('action');
const actionToken = actionParams.get('token');
if (actionToken && ['verify','reset'].includes(action)) {
  history.replaceState(null,'',location.pathname);
  $('#auth-form').hidden=true; $('#forgot-password').hidden=true; $('#recovery-form').hidden=false;
  $('#new-password-label').hidden=action==='verify'; $('#recovery-title').textContent=action==='verify'?'Verify your email':'Set a new password';
  $('#recovery-form').onsubmit=e=>{e.preventDefault();act(async()=>{await api('/api/security/complete/'+action,'POST',{token:actionToken,password:fields(e.target).password||''}); location.assign('/');});};
}
api('/api/service').then(s=>{if(s.billing_email&&/^[^\s@]+@[^\s@]+$/.test(s.billing_email)){$('#billing-contact').href='mailto:'+s.billing_email;$('#billing-contact').hidden=false;}for(const [id,url] of [['terms-link',s.terms_url],['privacy-link',s.privacy_url]]){if(url&&url.startsWith('https://'))$( '#'+id).href=url;}$('#auth-toggle').hidden=!s.registration_open;}).catch(()=>{});

let lastInboundId, draftPoll;
async function loadAI() {
  const p=await api('/api/ai/profile');
  $('#ai-provider-status').textContent=p.configured?'AI service ready.':'AI service is not yet ready for activation.';
  for(const key of ['business_info','greeting','language']) $('#ai-form').elements[key].value=p[key];
  for(const key of ['enabled','voice_enabled','autonomous','paused']) $('#ai-form').elements[key].checked=p[key];
  await loadOperations();
}
$('#ai-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);d.enabled=Boolean(d.enabled);d.voice_enabled=Boolean(d.voice_enabled);d.autonomous=Boolean(d.autonomous);d.paused=Boolean(d.paused);await api('/api/ai/profile','PUT',d);notice('AI settings saved.');});};
$('#draft-ai').onclick=()=>act(async()=>{
  if(!lastInboundId) throw Error('Select a conversation with a customer message first.');
  const thread={...selected};
  const j=await api('/api/ai/drafts/'+lastInboundId,'POST');
  clearInterval(draftPoll);
  notice('Generating an AI draft…');
  let attempts=0;
  draftPoll=setInterval(()=>act(async()=>{
    const d=await api('/api/ai/drafts/'+j.id);
    if(d.status==='ready') {clearInterval(draftPoll);if(selected?.number_id===thread.number_id&&selected?.peer===thread.peer){$('#send-form').elements.body.value=d.reply;notice('AI draft ready. Review it before sending.');}else notice('Draft ready. Return to its conversation to load it again.');}
    else if(['failed','generating'].includes(d.status)&&++attempts>30 || d.status==='failed'){clearInterval(draftPoll);notice('AI draft unavailable. Write a reply or contact support.');}
    else if(++attempts>60){clearInterval(draftPoll);notice('AI worker is unavailable. Try again later.');}
  }),2000);
});

async function loadOperations(){
 await loadCommercialControls();
 document.querySelectorAll("[data-owner]").forEach(e=>e.hidden=me.role!=="owner");
 const [metrics,books,actions,leads,sources,depts]=await Promise.all(['/api/operations','/api/bookings','/api/actions','/api/leads','/api/knowledge','/api/departments'].map(p=>api(p)));
 $('#ai-metrics').replaceChildren();for(const [key,value] of Object.entries(metrics))$('#ai-metrics').append(element('p',key.replaceAll('_',' ')+': '+value));
 for(const [id,rows,render] of [['ai-bookings',books,b=>b.peer+' · '+new Date(b.starts_at).toLocaleString()+' · '+b.status],['ai-actions',actions,a=>a.kind+' · '+a.status+' · '+a.receipt],['ai-leads',leads,l=>l.summary+' · '+l.status],['knowledge-list',sources,k=>k.title+' · v'+k.version+' · '+(k.approved?'approved':'draft')],['department-list',depts,d=>d.name+' · '+(d.bookings_enabled?'booking enabled':'disabled')]]){ $('#'+id).replaceChildren();for(const row of rows){ const line=element('p',render(row)); if(id==='ai-actions'&&me.role==='owner'&&['queued','awaiting_confirmation'].includes(row.status))line.append(button('Cancel request',async()=>{await api('/api/actions/'+row.id+'/cancel','POST',{});await loadOperations();},true)); if(id==='knowledge-list'){const detail=element('details');detail.append(element('summary','Read source'),element('p',row.content));line.append(detail);if(me.role==='owner')line.append(button('Edit / review',()=>{const form=$('#knowledge-form');form.dataset.id=row.id;for(const key of ['title','content','source'])form.elements[key].value=row[key];form.elements.approved.checked=row.approved;form.scrollIntoView({behavior:'smooth'});},true));}if(id==='department-list'&&me.role==='owner')line.append(button('Edit department',()=>{const form=$('#department-form');form.dataset.id=row.id;for(const key of ['name','description','timezone','duration','opens','closes','weekdays'])form.elements[key].value=row[key];form.elements.bookings_enabled.checked=row.bookings_enabled;form.scrollIntoView({behavior:'smooth'});},true)); $('#'+id).append(line); } }
}
async function loadCommercialControls(){
 const [catalog,payments,integrations]=await Promise.all(['/api/catalog','/api/payments','/api/integrations'].map(p=>api(p)));
 $('#merchant-select').replaceChildren();for(const i of integrations.filter(i=>i.kind==='stripe_merchant'&&i.enabled)){const option=element('option',i.name);option.value=i.id;$('#merchant-select').append(option);}
 $('#catalog-list').replaceChildren();for(const item of catalog){const row=element('p',item.name+' · GBP '+(item.amount/100).toFixed(2)+' · '+(item.enabled?'enabled':'disabled'));if(me.role==='owner')row.append(button(item.enabled?'Disable':'Enable',async()=>{await api('/api/catalog/'+item.id,'PUT',{enabled:!item.enabled});await loadCommercialControls();},true));$('#catalog-list').append(row);}
 $('#payment-list').replaceChildren();for(const pay of payments)$('#payment-list').append(element('p',pay.id+' · GBP '+(pay.amount/100).toFixed(2)+' · '+pay.status));
 if(me.role==='owner'){const quality=await api('/api/ai/quality-schedule');$('#quality-status').textContent=quality.status+' · '+(quality.enabled?'daily checks enabled':'daily checks disabled')+' · consecutive failures '+(quality.consecutive_failures||0);const keys=await api('/api/integration-keys');$('#integration-key-list').replaceChildren();for(const key of keys){const row=element('p',key.name+' · '+key.scopes.join(', ')+' · '+(key.revoked?'revoked':'expires '+new Date(key.expires_at).toLocaleDateString()));if(!key.revoked)row.append(button('Revoke',async()=>{await api('/api/integration-keys/'+key.id,'DELETE');await loadCommercialControls();},true));$('#integration-key-list').append(row);}}
}
$('#catalog-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);d.all_taxes_included_confirmed=Boolean(d.all_taxes_included_confirmed);await api('/api/catalog','POST',d);e.target.reset();await loadCommercialControls();});};
$('#action-review-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);const id=d.action_id;delete d.action_id;await api('/api/actions/'+id+'/review','POST',d);e.target.reset();await loadOperations();});};
$('#integration-key-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);const key=await api('/api/integration-keys','POST',{name:d.name,scopes:[d.scope]});$('#new-integration-key').textContent='Copy this key to your integration now. It is shown once: '+key.token;e.target.reset();await loadCommercialControls();});};
$('#finance-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);d.amount=Number(d.amount);await api('/api/finance','POST',d);const report=await api('/api/finance?period='+d.period);$('#finance-summary').textContent='Recorded revenue GBP '+(report.recorded_revenue/100).toFixed(2)+' · recorded costs GBP '+(report.recorded_costs/100).toFixed(2)+' · recorded margin GBP '+(report.recorded_margin/100).toFixed(2)+'. '+report.note;});};

$('#knowledge-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);d.approved=Boolean(d.approved);await api(e.target.dataset.id?'/api/knowledge/'+e.target.dataset.id:'/api/knowledge',e.target.dataset.id?'PUT':'POST',d);delete e.target.dataset.id;e.target.reset();await loadOperations();});};
$('#department-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);for(const k of ['duration','opens','closes'])d[k]=Number(d[k]);d.bookings_enabled=Boolean(d.bookings_enabled);await api(e.target.dataset.id?'/api/departments/'+e.target.dataset.id:'/api/departments',e.target.dataset.id?'PUT':'POST',d);delete e.target.dataset.id;e.target.reset();await loadOperations();});};

$('#knowledge-import-form').onsubmit=e=>{e.preventDefault();act(async()=>{await api('/api/knowledge/import','POST',new FormData(e.target));e.target.reset();await loadOperations();});};
$('#quality-form').onsubmit=e=>{e.preventDefault();act(async()=>{const d=fields(e.target);const terms=s=>s.split(',').map(v=>v.trim()).filter(Boolean);await api('/api/ai/quality-schedule','PUT',{enabled:Boolean(d.enabled),cases:[{question:d.question,expected_terms:terms(d.expected_terms),forbidden_terms:terms(d.forbidden_terms)}]});await loadCommercialControls();});};

async function loadCompanyVerification() {
  const value = await api('/api/company-verification');
  const status = $('#company-verification-status');
  const checks = value.checks || {};
  const prerequisiteProofs = Boolean(checks.business_email && checks.dns && checks.company_register);
  const awaitingDirector = prerequisiteProofs && !checks.director_authority && (value.status === 'pending' || value.identity_retry);

  status.classList.remove('checking');
  if (!value.available) {
    status.textContent = 'Secure company verification is being configured. Activation remains subject to verification.';
  } else if (value.status === 'verified') {
    status.textContent = 'Company verification complete — your company, domain and director authority are verified.';
  } else if (awaitingDirector) {
    status.textContent = 'Company and domain verified — complete director identity to continue.';
  } else if (value.status === 'pending') {
    status.textContent = 'Verifying — we are checking your company and domain. Complete any waiting steps below.';
    status.classList.add('checking');
  } else {
    status.textContent = value.message;
  }

  $('#company-verification-explanation').textContent = value.explanation || '';
  $('#company-verification-stage').textContent = value.stage ? `Stage ${value.stage} of ${value.stage_total} — ${value.stage_label}` : '';

  if (checks.company_register) {
    $('#profile-review').hidden = true;
    $('#profile-form').hidden = true;
    $('#review-status').textContent = checks.director_authority ? 'Identity verified' : 'Company verified';
  }
  $('#company-verification-form').hidden = !value.available || !['not_started','expired','invalidated'].includes(value.status);
  $('#company-proof-instructions').hidden = !value.available || !['pending','held','verified'].includes(value.status);
  $('#company-identity').hidden = !value.identity_ready;
  $('#company-identity').textContent = value.identity_retry ? 'Retry director identity securely' : 'Verify director identity securely';
  $('#company-verification-retry').hidden = !value.retryable;
  const telephonePanel = $('#company-telephone-approval');
  const telephonePreflight = $('#company-telephone-preflight');
  const telephoneStart = $('#company-telephone-start');
  if (telephonePanel) {
    const telephoneComplete = value.telephone_status === 'approved';
    telephonePanel.hidden = !checks.director_authority || telephoneComplete;
    telephonePreflight.hidden = true;
    telephoneStart.hidden = true;
    if (!telephoneComplete) {
      $('#company-telephone-readiness').textContent = value.telephone_authorized
        ? 'Telephone approval has started automatically. We are reviewing your application; this page will update automatically. Telephone approval typically takes 1–3 business days, although some applications may be approved sooner.'
        : 'Telephone approval will start automatically. Telephone approval typically takes 1–3 business days, although some applications may be approved sooner.';
    }
  }

  if (value.checks) {
    const labels = {business_email:'Business email',dns:'Domain control',company_register:'Company register',director_authority:'Director authority'};
    $('#company-proof-checks').textContent = Object.entries(value.checks).map(([key,passed])=>`${labels[key]}: ${passed?'verified':'waiting'}`).join(' · ');

    const dnsSetup = $('#company-dns-setup');
    const dnsVerified = $('#company-dns-verified');
    dnsSetup.hidden = Boolean(checks.dns);
    dnsVerified.hidden = !checks.dns;

    if (!checks.dns) {
      $('#company-dns-name').textContent = '_raeburn-connect';
      $('#company-dns-value').textContent = value.dns_value;
      $('#company-dns-guidance').textContent = `In your DNS provider, add a TXT record. For Cloudflare and most DNS dashboards, enter only _raeburn-connect in the Name/Host field — the domain ${value.domain} is added automatically. Enter the exact value below as the TXT content, then save the record. Keep it in place for ongoing checks.`;
    }

    if (value.telephone_status) $('#company-verification-explanation').textContent += ' Telephone approval: ' + value.telephone_status.replaceAll('_',' ') + '.';
  }
  companyVerificationState = value;
  return value;
}
let telephonePreflightReady = false;
$('#company-telephone-preflight').onclick = () => act(async () => {
  const button = $('#company-telephone-preflight');
  button.disabled = true;
  try {
    const result = await api('/api/company-verification/telephone-preflight', 'POST');
    telephonePreflightReady = Boolean(result.ready);
    $('#company-telephone-readiness').textContent =
      'Ready for telephone approval. Your verified company details, registered address and director contact mobile meet the current ' +
      result.number_type + ' business-number requirements. Nothing has been submitted yet.';
    $('#company-telephone-start').hidden = !telephonePreflightReady;
  } finally {
    button.disabled = false;
  }
});
$('#company-telephone-start').onclick = () => act(async () => {
  if (!telephonePreflightReady) throw Error('Run the telephone approval readiness check first.');
  const button = $('#company-telephone-start');
  button.disabled = true;
  try {
    const result = await api('/api/company-verification/telephone-start', 'POST');
    telephonePreflightReady = false;
    $('#company-telephone-start').hidden = true;
    $('#company-telephone-preflight').hidden = true;
    $('#company-telephone-readiness').textContent =
      result.status === 'approved'
        ? 'Telephone approval is complete.'
        : 'Telephone approval has started. We are reviewing your application; this page will update automatically.';
    await loadCompanyVerification();
    notice('Telephone approval started. Telephone approval typically takes 1–3 business days, although some applications may be approved sooner.');
  } finally {
    button.disabled = false;
  }
});

$('#company-verification-form').onsubmit = e => {e.preventDefault();act(async()=>{
  const data = fields(e.target); data.authority_confirmed = e.target.elements.authority_confirmed.checked;
  await api('/api/company-verification/start','POST',data); await load(); show('account');
  notice('Thank you — your secure verification has started. Check your business email and add the DNS record shown below.');
});};
$('#company-verification-retry').onclick = ()=>act(async()=>{await api('/api/company-verification/retry','POST');await loadCompanyVerification();notice('Verification checks restarted. Your existing TXT record remains valid.');});
let identityHandoffUrl = '';
let identityHandoffPoll = null;
function mobileIdentityDevice() {
  if (navigator.userAgentData?.mobile === true) return true;
  if (/Android|iPhone|iPod|Mobile/i.test(navigator.userAgent)) return true;
  return navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
}
function closeIdentityHandoff() {
  identityHandoffUrl = '';
  if (identityHandoffPoll) {
    clearInterval(identityHandoffPoll);
    identityHandoffPoll = null;
  }
  $('#identity-handoff-qr').removeAttribute('src');
  if ($('#identity-handoff').open) $('#identity-handoff').close();
}
function watchIdentityHandoff() {
  if (identityHandoffPoll) clearInterval(identityHandoffPoll);
  identityHandoffPoll = setInterval(async () => {
    if (!$('#identity-handoff').open) return closeIdentityHandoff();
    try {
      const value = await loadCompanyVerification();
      const checks = value.checks || {};
      if (checks.director_authority || value.status === 'verified') {
        closeIdentityHandoff();
        notice('Director identity verified. Your account verification has been updated.');
      } else if (value.status === 'held' && !value.identity_retry) {
        closeIdentityHandoff();
        notice(value.explanation || 'The director identity result needs attention.');
      }
    } catch (_) {}
  }, 2000);
}
$('#identity-handoff-close').onclick = closeIdentityHandoff;
$('#identity-handoff-cancel').onclick = closeIdentityHandoff;
$('#identity-handoff-open').onclick = () => {
  if (!identityHandoffUrl) return;
  const target = new URL(identityHandoffUrl);
  if (target.protocol !== 'https:' || target.hostname !== 'verify.stripe.com') return notice('We could not open the secure identity check. Please try again.');
  location.assign(target.href);
};
$('#company-identity').onclick = ()=>act(async()=>{
  const result=await api('/api/company-verification/identity','POST');
  const target=new URL(result.url);
  if(target.protocol!=='https:'||target.hostname!=='verify.stripe.com')throw Error('We could not open the secure identity check. Please try again.');
  if (mobileIdentityDevice()) {
    location.assign(target.href);
    return;
  }
  identityHandoffUrl = target.href;
  $('#identity-handoff-qr').src = '/api/company-verification/identity-qr?ts=' + Date.now();
  $('#identity-handoff').showModal();
  watchIdentityHandoff();
});
const companyProofToken = actionParams.get('company_proof');
if(companyProofToken){
  history.replaceState(null,'',location.pathname);
  $('#company-email-confirmation').hidden=false;
  $('#company-confirm-email').onclick=()=>act(async()=>{
    await api('/api/company-verification/confirm-email','POST',{token:companyProofToken});
    $('#company-email-confirmation').hidden=true;await load();show('account');notice('Your business email is verified. Thank you.');
  });
}
setInterval(()=>{if(me && !document.hidden && !$('#account').hidden)loadCompanyVerification().catch(()=>{});},30000);
