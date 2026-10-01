const $ = s => document.querySelector(s);
let me, owned = [], selected, registering = false;
const notice = message => { $('#notice').textContent = message; };
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
async function api(path, method = 'GET', data) {
  const response = await fetch(path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'Raeburn' }, body: data === undefined ? undefined : JSON.stringify(data) });
  const value = await response.json();
  if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'Check the information you entered.');
  return value;
}
async function act(fn) { try { notice(''); await fn(); } catch (error) { notice(error.message); } }
function fields(form) { return Object.fromEntries(new FormData(form)); }
function button(text, fn, secondary = false) { const b = element('button', text, secondary ? 'secondary' : ''); b.type = 'button'; b.onclick = () => act(async () => { b.disabled = true; try { await fn(); } finally { b.disabled = false; } }); return b; }
function show(view) { document.querySelectorAll('.view').forEach(e => e.hidden = e.id !== view); document.querySelectorAll('nav button').forEach(e => e.classList.toggle('active', e.dataset.view === view)); if (view === 'inbox') act(loadThreads); if (view === 'admin') act(loadAdmin); }
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => show(b.dataset.view));
$('#auth-toggle').onclick = () => { registering = !registering; $('#register-fields').hidden = !registering; $('#auth-submit').textContent = registering ? 'Create account' : 'Sign in'; $('#auth-toggle').textContent = registering ? 'I already have an account' : 'Create an account'; };
$('#auth-form').onsubmit = e => { e.preventDefault(); act(async () => { const data = fields(e.target); if (registering) { data.accept_terms = Boolean(data.accept_terms); await api('/api/register', 'POST', data); } await api('/api/login', 'POST', { email: data.email, password: data.password }); await load(); }); };
$('#logout').onclick = () => act(async () => { await api('/api/logout', 'POST'); location.reload(); });
async function load() {
  me = await api('/api/me'); owned = await api('/api/numbers');
  $('#auth').hidden = true; $('#workspace').hidden = false; $('#logout').hidden = false; $('#admin-nav').hidden = !me.platform_admin;
  $('#company').textContent = me.tenant.name; $('#review-status').textContent = me.tenant.status; $('#billing-status').textContent = me.tenant.billing_status; $('#number-count').textContent = owned.length;
  $('#next-step').textContent = me.tenant.status !== 'approved' ? 'Complete your legal business profile. Our team will review it and guide your number registration.' : me.tenant.billing_status !== 'active' ? 'Choose your subscription, then search for an available number.' : owned.length ? 'Set up call forwarding and start managing your conversations.' : 'Search for your UK number and submit an activation request.';
  for (const key of ['legal_name', 'address', 'registration_number']) $('#profile-form').elements[key].value = me.tenant[key] || '';
  renderNumbers(); await loadOrders(); show('overview');
}
$('#profile-form').onsubmit = e => { e.preventDefault(); act(async () => { await api('/api/profile', 'PUT', fields(e.target)); await load(); notice('Business details submitted for review.'); }); };
$('#search-form').onsubmit = e => { e.preventDefault(); act(async () => {
  const results = await api('/api/numbers/search?' + new URLSearchParams(fields(e.target))); $('#search-results').replaceChildren();
  for (const n of results) { const card = element('article'); card.append(element('h2', n.phone), element('p', [n.voice && 'Voice', n.sms && 'SMS'].filter(Boolean).join(' · ') || 'No advertised capabilities'), button('Request activation', async () => { await api('/api/orders', 'POST', {phone:n.phone,type:n.type,request_key:crypto.randomUUID()}); await loadOrders(); notice('Activation requested. Follow its status below.'); })); $('#search-results').append(card); }
  if (!results.length) notice('No available numbers match these digits.');
}); };
function renderNumbers() {
  $('#owned-numbers').replaceChildren(); $('#send-number').replaceChildren();
  for (const n of owned) {
    if (n.sms) { const option = element('option', n.phone); option.value = n.id; $('#send-number').append(option); }
    const card = element('article', undefined, 'owned'); card.append(element('h3', n.phone), element('p', `WhatsApp: ${n.whatsapp} · RCS: ${n.rcs}`));
    if (n.voice) { const label = element('label', 'UK call forwarding destination'); const input = element('input'); input.type='tel'; input.value=n.forwarding; input.placeholder='+447700900000'; label.append(input); card.append(label, button('Save forwarding', async () => { await api(`/api/numbers/${n.id}/forwarding`, 'PUT', {destination:input.value}); notice('Call forwarding updated.'); })); }
    $('#owned-numbers').append(card);
  }
  if (!owned.length) $('#owned-numbers').append(element('p', 'Your activated numbers will appear here.'));
}
async function loadOrders() { const orders = await api('/api/orders'); $('#orders').replaceChildren(); for (const o of orders) { const row = element('article', undefined, 'owned'); row.append(element('h3', o.phone), element('p', `${o.status}${o.error ? ' · ' + o.error : ''}`)); $('#orders').append(row); } }
$('#billing-form').onsubmit = e => { e.preventDefault(); act(async () => { const value = await api('/api/billing/checkout', 'POST', fields(e.target)); location.assign(value.url); }); };
$('#billing-portal').onclick = () => act(async () => { const value = await api('/api/billing/portal', 'POST'); location.assign(value.url); });
async function loadThreads() {
  const threads = await api('/api/threads'); $('#threads').replaceChildren();
  for (const t of threads) { const b = button(t.peer, async () => { selected=t; $('#thread-title').textContent=t.peer; $('#send-form').elements.peer.value=t.peer; $('#send-number').value=t.number_id; await loadMessages(); }, true); b.classList.add('thread'); b.append(element('small', t.preview)); $('#threads').append(b); }
  if (!threads.length) $('#threads').append(element('p', 'No conversations yet. Start one using the message form.'));
}
async function loadMessages() {
  if (!selected) return;
  const messages = await api('/api/messages?' + new URLSearchParams({number_id:selected.number_id,peer:selected.peer})); $('#messages').replaceChildren();
  for (const m of messages) { const bubble = element('div', undefined, 'bubble '+m.direction); bubble.append(element('span',m.body), element('small',`${m.status} · ${new Date(m.at).toLocaleString()}`)); $('#messages').append(bubble); }
  $('#messages').scrollTop = $('#messages').scrollHeight;
}
$('#send-form').onsubmit = e => { e.preventDefault(); act(async () => { const data=fields(e.target); data.consent_confirmed=Boolean(data.consent_confirmed); data.request_key=crypto.randomUUID(); await api('/api/messages','POST',data); selected={number_id:data.number_id,peer:data.peer}; e.target.elements.body.value=''; await loadThreads(); await loadMessages(); notice('Message queued for delivery.'); }); };
async function loadAdmin() {
  const tenants=await api('/api/admin/tenants'); $('#admin-customers').replaceChildren();
  for (const t of tenants) { const card=element('article',undefined,'customer'); card.append(element('h2',t.name),element('p',`${t.legal_name} · ${t.address} · ${t.status} · billing ${t.billing_status}`)); if (!t.connected) card.append(button('Connect Twilio subaccount',async()=>{await api(`/api/admin/tenants/${t.id}/connect`,'POST');await loadAdmin();}));
    const form=element('form'); const bundle=element('input'); bundle.placeholder='Approved BU bundle SID'; bundle.required=true; const address=element('input'); address.placeholder='AD address SID (where required)'; const type=element('select'); for (const v of ['Local','Mobile','TollFree']) type.append(element('option',v)); const submit=element('button','Verify bundle and approve'); submit.type='submit'; form.append(bundle,address,type,submit); form.onsubmit=e=>{e.preventDefault();act(async()=>{await api(`/api/admin/tenants/${t.id}/approve`,'POST',{bundle_sid:bundle.value,address_sid:address.value,type:type.value});await loadAdmin();});}; card.append(form); $('#admin-customers').append(card); }
}
api('/api/me').then(load).catch(()=>{});
setInterval(()=>{if(me&&!$('#inbox').hidden)act(async()=>{await loadThreads();await loadMessages();});},15000);
