let currentAccountId = null;
let ws = null;
let allAccounts = [];
let refreshSessionAccountId = null;

// ===== SIDEBAR MOBILE =====
function openSidebar() {
  document.querySelector('.sidebar').classList.add('open');
  document.getElementById('sidebarBackdrop').classList.add('open');
}
function closeSidebar() {
  document.querySelector('.sidebar').classList.remove('open');
  document.getElementById('sidebarBackdrop').classList.remove('open');
}

// ===== INIT =====
document.addEventListener('DOMContentLoaded', async () => {
  const res = await fetch('/api/auth/me');
  if (res.status === 401) { window.location.href = '/login'; return; }
  loadAccounts();
  connectWS();
  _autoReconnectPoll();
  loadNotifications();
  // Poll for new notifications every 5 minutes
  _notifPollTimer = setInterval(loadNotifications, 5 * 60_000);
});

async function _autoReconnectPoll() {
  // Після старту опитуємо кожні 4с протягом 2хв поки всі акаунти не підключаться
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 4000));
    const res = await fetch('/api/accounts').catch(() => null);
    if (!res || !res.ok) continue;
    const accounts = await res.json();
    const total = accounts.length;
    const connected = accounts.filter(a => a.is_connected).length;
    _updateAccountListQuiet(accounts);
    if (total > 0 && connected === total) break;
  }
}

function _updateAccountListQuiet(accounts) {
  allAccounts = accounts;
  accounts.forEach(a => {
    const item = document.querySelector(`.acc-item[data-id="${a.id}"]`);
    if (!item) return;
    const dot = item.querySelector('.acc-status-dot');
    if (dot) { dot.className = 'acc-status-dot ' + (a.is_connected ? 'on' : 'off'); }
  });
}

// ===== WEBSOCKET =====
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'new_code') {
      // Update sidebar code badge if needed
      if (data.account_id === currentAccountId) {
        prependCode(data);
      }
      // Flash sidebar item
      const item = document.querySelector(`.acc-item[data-id="${data.account_id}"]`);
      if (item) {
        item.style.background = '#1a8cbf22';
        setTimeout(() => item.style.background = '', 2000);
      }
      // Toast
      const name = allAccounts.find(a => a.id === data.account_id);
      const label = name ? (name.first_name || name.phone || `#${data.account_id}`) : `#${data.account_id}`;
      showToast(`📨 Код для ${label}: ${data.code}`, 'success');
    } else if (data.type === 'invite_log') {
      appendInviteLog(data);
    } else if (data.type === 'invite_stats') {
      updateInviteStats(data);
      if (!data.running) {
        document.getElementById('inviteStopBtn').style.display = 'none';
        document.getElementById('inviteStartBtn').style.display = 'inline-flex';
        document.getElementById('inviteStartBtn').disabled = false;
      }
    } else if (data.type === 'comment_log') {
      appendCommentLog(data);
    } else if (data.type === 'comment_stats') {
      updateCommentStats(data);
      if (!data.running) {
        document.getElementById('commentStopBtn').style.display = 'none';
        document.getElementById('commentStartBtn').style.display = 'inline-flex';
        document.getElementById('commentStartBtn').disabled = false;
      }
    } else if (data.type === 'react_log') {
      appendReactLog(data);
    } else if (data.type === 'react_stats') {
      updateReactStats(data);
      if (!data.running) {
        document.getElementById('reactStopBtn').style.display = 'none';
        document.getElementById('reactStartBtn').style.display = 'inline-flex';
        document.getElementById('reactStartBtn').disabled = false;
      }
    } else if (data.type === 'comment_react_log') {
      appendCommentReactLog(data);
    } else if (data.type === 'comment_react_stats') {
      updateCommentReactStats(data);
      if (!data.running) {
        document.getElementById('commentReactStopBtn').style.display = 'none';
        document.getElementById('commentReactStartBtn').style.display = 'inline-flex';
        document.getElementById('commentReactStartBtn').disabled = false;
      }
    } else if (data.type === 'invite_parse_progress') {
      _parsedCount = data.count;
      const el = document.getElementById('parsedCount');
      if (el) el.textContent = data.count;
    } else if (data.type === 'invite_parse_done') {
      _parsedCount = data.count;
      const el = document.getElementById('parsedCount');
      if (el) el.textContent = data.count;
      document.getElementById('parseResult').style.display = 'block';
      document.getElementById('parseStopBtn').style.display = 'none';
      document.getElementById('parseStartBtn').style.display = 'inline-flex';
      document.getElementById('parseStartBtn').disabled = false;
      document.getElementById('parseStartBtn').textContent = '🔍 Зібрати учасників';
      updateParsedCountLabel();
    }
  };

  ws.onclose = () => {
    setTimeout(connectWS, 3000);
    const ind = document.getElementById('wsIndicator');
    if (ind) ind.innerHTML = '<span style="color:var(--danger)">● Відключено</span>';
  };

  ws.onopen = () => {
    const ind = document.getElementById('wsIndicator');
    if (ind) ind.innerHTML = '<span class="pulse"></span><span>Очікування кодів...</span>';
  };
}

// ===== ACCOUNTS LIST =====
async function loadAccounts() {
  try {
    const res = await fetch('/api/accounts');
    allAccounts = await res.json();
    renderSidebar(allAccounts);
  } catch (e) {
    document.getElementById('accountList').innerHTML =
      '<div class="sidebar-loading">Помилка завантаження</div>';
  }
}

function renderSidebar(accounts) {
  const list = document.getElementById('accountList');
  if (!accounts.length) {
    list.innerHTML = '<div class="sidebar-loading">Акаунтів немає</div>';
    return;
  }
  list.innerHTML = accounts.map(a => `
    <div class="acc-item${a.id === currentAccountId ? ' active' : ''}" data-id="${a.id}" onclick="selectAccount(${a.id})">
      <div class="acc-avatar">
        <img src="/api/accounts/${a.id}/photo" alt="" onerror="this.style.display='none'" style="position:absolute;inset:0;width:100%;height:100%;border-radius:50%;object-fit:cover">
        ${avatarLetter(a)}
        ${a.is_connected ? '<div class="acc-avatar-dot"></div>' : ''}
      </div>
      <div class="acc-info">
        <div class="acc-name">${fullName(a)}</div>
        <div class="acc-sub">${a.username ? '@' + a.username : (a.phone ? '+' + a.phone : '—')}</div>
      </div>
      <div class="acc-status-dot ${a.is_connected ? 'on' : 'off'}"></div>
    </div>
  `).join('');
}

function filterAccounts() {
  const q = document.getElementById('searchInput').value.toLowerCase();
  const filtered = allAccounts.filter(a =>
    fullName(a).toLowerCase().includes(q) ||
    (a.username || '').toLowerCase().includes(q) ||
    (a.phone || '').includes(q)
  );
  renderSidebar(filtered);
}

// ===== SELECT ACCOUNT =====
async function selectAccount(id) {
  currentAccountId = id;
  closeSidebar();

  // Update sidebar active
  document.querySelectorAll('.acc-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id == id);
  });

  clearFloodTimer();
  document.getElementById('welcome').style.display = 'none';
  const detail = document.getElementById('accountDetail');
  detail.style.display = 'flex';

  // Clear previous
  setProfileUI(null);
  document.getElementById('codesList').innerHTML = '<div class="no-codes">Завантаження...</div>';

  try {
    const [infoRes, codesRes] = await Promise.all([
      fetch(`/api/accounts/${id}`),
      fetch(`/api/accounts/${id}/codes`),
    ]);
    const info = await infoRes.json();
    const codes = await codesRes.json();

    setProfileUI(info);
    renderCodes(codes);
    loadChannels();
    loadFloodStatus();

    // Load photo
    const img = document.getElementById('avatarImg');
    img.style.display = 'none';
    img.src = `/api/accounts/${id}/photo?t=${Date.now()}`;
    document.getElementById('avatarPlaceholder').style.display = 'flex';
    document.getElementById('avatarPlaceholder').textContent =
      (info.first_name || info.phone || '?')[0].toUpperCase();

  } catch (e) {
    showToast('Помилка завантаження акаунту', 'error');
  }
}

function setProfileUI(info) {
  if (!info) {
    document.getElementById('profileName').textContent = 'Завантаження...';
    ['profileUsername', 'profileStatus', 'fPhone', 'fTgId', 'fUsername', 'fName', 'fBio', 'fStatus', 'fCreated']
      .forEach(id => document.getElementById(id).textContent = '—');
    return;
  }

  const name = [info.first_name, info.last_name].filter(Boolean).join(' ') || info.phone || '—';
  document.getElementById('profileName').textContent = name;
  document.getElementById('avatarPlaceholder').textContent = (name[0] || '?').toUpperCase();

  document.getElementById('profileUsername').textContent = info.username ? '@' + info.username : '';
  document.getElementById('profileStatus').textContent = info.status || '';

  document.getElementById('fPhone').textContent = info.phone ? '+' + info.phone : '—';
  document.getElementById('fTgId').textContent = info.tg_id || '—';
  document.getElementById('fUsername').textContent = info.username ? '@' + info.username : '—';
  document.getElementById('fName').textContent = name;
  document.getElementById('fBio').textContent = info.bio || '—';
  document.getElementById('fStatus').textContent = info.status || '—';
  document.getElementById('fTgCreated').textContent = info.tg_created ? `~${info.tg_created}` : '—';
  document.getElementById('fCreated').textContent = info.created_at ? formatDate(info.created_at) : '—';

  // Credentials
  document.getElementById('cApiId').textContent = info.api_id || '—';
  document.getElementById('cApiHash').textContent = info.api_hash || '—';
  const sessEl = document.getElementById('cSession');
  sessEl.dataset.value = info.session_string || '';
  sessEl.textContent = '••••••••••••••••••••••••••••••';
  sessEl.classList.add('session-masked');

  const pyroEl = document.getElementById('cPyrogram');
  pyroEl.dataset.value = info.pyrogram_session || '';
  pyroEl.textContent = '••••••••••••••••••••••••••••••';
  pyroEl.classList.add('session-masked');

  // 2FA
  const twofaEl = document.getElementById('cTwofa');
  twofaEl.dataset.value = info.twofa_password || '';
  if (info.twofa_password) {
    twofaEl.textContent = '••••••••';
    twofaEl.classList.add('twofa-masked');
    document.getElementById('cTwofaToggle').textContent = 'Показати';
  } else {
    twofaEl.textContent = '—';
    twofaEl.classList.remove('twofa-masked');
    document.getElementById('cTwofaToggle').textContent = 'Показати';
  }
  cancelTwofa();

  // Badges
  document.getElementById('premiumBadge').style.display = info.premium ? 'inline-block' : 'none';
  document.getElementById('connBadge').style.display = info.is_connected ? 'inline-block' : 'none';
  document.getElementById('disconnBadge').style.display = info.is_connected ? 'none' : 'inline-block';
  const showDisc = info.is_connected ? 'none' : 'block';
  document.getElementById('btnReconnect').style.display = showDisc;
  document.getElementById('btnRefreshSession').style.display = showDisc;
  document.getElementById('onlineDot').style.display = info.is_online ? 'block' : 'none';
}

// ===== CODES =====
function renderCodes(codes) {
  const list = document.getElementById('codesList');
  if (!codes.length) {
    list.innerHTML = '<div class="no-codes">Кодів поки немає</div>';
    return;
  }
  list.innerHTML = codes.map(c => codeHTML(c)).join('');
}

function prependCode(data) {
  const list = document.getElementById('codesList');
  const noEl = list.querySelector('.no-codes');
  if (noEl) noEl.remove();

  const div = document.createElement('div');
  div.innerHTML = codeHTML({
    id: Date.now(),
    code: data.code,
    code_type: data.code_type || 'login',
    message: data.message,
    received_at: data.received_at,
  });
  const item = div.firstElementChild;
  item.classList.add('new-flash');
  list.prepend(item);
  setTimeout(() => item.classList.remove('new-flash'), 3000);
}

function codeHTML(c) {
  const is2fa = c.code_type === '2fa';
  const typeBadge = is2fa
    ? '<span class="code-type-badge badge-2fa">2FA</span>'
    : '<span class="code-type-badge badge-login">Вхід</span>';
  return `
    <div class="code-item${is2fa ? ' code-2fa' : ''}" id="code-${c.id}">
      <div class="code-top">
        <div style="display:flex;align-items:center;gap:10px">
          <div class="code-number">${c.code || '—'}</div>
          ${typeBadge}
        </div>
        <div class="code-actions">
          <button class="btn-copy" onclick="copyCode('${c.code}', this)">Копіювати</button>
        </div>
      </div>
      <div class="code-msg">${escHtml(c.message || '')}</div>
      <div class="code-time">${c.received_at ? formatDate(c.received_at) : ''}</div>
    </div>
  `;
}

function toggleTwofa() {
  const el = document.getElementById('cTwofa');
  const btn = document.getElementById('cTwofaToggle');
  const val = el.dataset.value;
  if (!val) return;
  if (el.classList.contains('twofa-masked')) {
    el.textContent = val;
    el.classList.remove('twofa-masked');
    btn.textContent = 'Сховати';
  } else {
    el.textContent = '••••••••';
    el.classList.add('twofa-masked');
    btn.textContent = 'Показати';
  }
}

function editTwofa() {
  const box = document.getElementById('twofaEditBox');
  box.style.display = 'flex';
  document.getElementById('twofaInput').value = document.getElementById('cTwofa').dataset.value;
  document.getElementById('twofaInput').focus();
}

function cancelTwofa() {
  const box = document.getElementById('twofaEditBox');
  box.style.display = 'none';
  document.getElementById('twofaInput').value = '';
}

async function saveTwofa() {
  if (!currentAccountId) return;
  const pwd = document.getElementById('twofaInput').value.trim();
  const resp = await fetch(`/api/accounts/${currentAccountId}/2fa`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ twofa_password: pwd }),
  });
  if (!resp.ok) { showToast('Помилка збереження', 'error'); return; }
  const el = document.getElementById('cTwofa');
  el.dataset.value = pwd;
  if (pwd) {
    el.textContent = '••••••••';
    el.classList.add('twofa-masked');
    document.getElementById('cTwofaToggle').textContent = 'Показати';
  } else {
    el.textContent = '—';
    el.classList.remove('twofa-masked');
  }
  cancelTwofa();
  showToast('2FA пароль збережено', 'success');
}

async function cloneSession() {
  if (!currentAccountId) return;
  document.getElementById('cloneModal').style.display = 'flex';
  document.getElementById('cloneSpinner').textContent = 'Генерація нової сесії...';
  document.getElementById('cloneBody').innerHTML = `
    <div class="modal-body" style="text-align:center;padding:30px 20px">
      <div style="font-size:15px;color:var(--muted)">Генерація нової сесії...</div>
    </div>`;

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30000);
    const res = await fetch(`/api/accounts/${currentAccountId}/clone-session`, {
      method: 'POST',
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('cloneBody').innerHTML = `
        <div class="modal-body">
          <div class="form-error" style="display:block">${data.detail || 'Помилка'}</div>
        </div>
        <div class="modal-foot"><button class="btn-secondary" onclick="closeCloneModal()">Закрити</button></div>`;
      return;
    }
    document.getElementById('cloneBody').innerHTML = `
      <div class="modal-body">
        <p style="color:var(--muted);font-size:13px;margin-bottom:16px">
          Нова сесія згенерована — використовуй її в боті. Вона незалежна від тієї що в системі.
        </p>
        <div class="form-group">
          <label>Pyrogram сесія (для бота)</label>
          <textarea readonly rows="4" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--bg1);color:var(--text);font-size:12px;resize:none;word-break:break-all">${data.pyrogram_session}</textarea>
        </div>
        <div class="form-group">
          <label>Telethon сесія</label>
          <textarea readonly rows="4" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--bg1);color:var(--text);font-size:12px;resize:none;word-break:break-all">${data.session_string}</textarea>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn-primary" onclick="copyText('${data.pyrogram_session}')">Копіювати Pyrogram</button>
        <button class="btn-secondary" onclick="closeCloneModal()">Закрити</button>
      </div>`;
  } catch {
    document.getElementById('cloneBody').innerHTML = `
      <div class="modal-body">
        <div class="form-error" style="display:block">Помилка з'єднання з сервером</div>
      </div>
      <div class="modal-foot"><button class="btn-secondary" onclick="closeCloneModal()">Закрити</button></div>`;
  }
}

function closeCloneModal() {
  document.getElementById('cloneModal').style.display = 'none';
}

function togglePyrogram() {
  const el = document.getElementById('cPyrogram');
  const btn = event.target;
  if (el.classList.contains('session-masked')) {
    el.textContent = el.dataset.value;
    el.classList.remove('session-masked');
    btn.textContent = 'Сховати';
  } else {
    el.textContent = '••••••••••••••••••••••••••••••';
    el.classList.add('session-masked');
    btn.textContent = 'Показати';
  }
}

function toggleSession() {
  const el = document.getElementById('cSession');
  const btn = event.target;
  if (el.classList.contains('session-masked')) {
    el.textContent = el.dataset.value;
    el.classList.remove('session-masked');
    btn.textContent = 'Сховати';
  } else {
    el.textContent = '••••••••••••••••••••••••••••••';
    el.classList.add('session-masked');
    btn.textContent = 'Показати';
  }
}

async function copyText(text) {
  if (!text || text === '—') return;
  try {
    await navigator.clipboard.writeText(text);
    showToast('Скопійовано', 'success');
  } catch {
    showToast('Не вдалося скопіювати', 'error');
  }
}

async function copyCode(code, btn) {
  try {
    await navigator.clipboard.writeText(code);
    const orig = btn.textContent;
    btn.textContent = '✓ Скопійовано';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = orig;
      btn.classList.remove('copied');
    }, 2000);
  } catch {
    showToast('Не вдалося скопіювати', 'error');
  }
}

// ===== ADD MODAL =====
let genTempId = null;
let genCodeValue = null;

function openAddModal() {
  refreshSessionAccountId = null;
  document.getElementById('addModal').style.display = 'flex';
  switchTab('paste');
  resetGenFlow();
  apiReset();
  setTimeout(() => document.getElementById('inputSession').focus(), 50);
}

async function reconnectAccount() {
  if (!currentAccountId) return;
  const btn = document.getElementById('btnReconnect');
  btn.disabled = true;
  btn.textContent = 'Підключення...';
  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/reconnect`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Не вдалось перепідключитись', 'error');
    } else {
      showToast('Підключено', 'success');
      await loadAccounts();
      selectAccount(currentAccountId);
    }
  } catch {
    showToast('Помилка з\'єднання', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔌 Перепідключити';
  }
}

function openRefreshSession() {
  if (!currentAccountId) return;
  refreshSessionAccountId = currentAccountId;
  document.getElementById('addModal').style.display = 'flex';
  switchTab('gen');
  resetGenFlow();
}

function closeAddModal() {
  if (genTempId) {
    fetch('/api/auth/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ temp_id: genTempId }),
    }).catch(() => {});
    genTempId = null;
  }
  document.getElementById('addModal').style.display = 'none';
}

function overlayClick(e) {
  if (e.target === e.currentTarget) closeAddModal();
}

function switchTab(tab) {
  document.getElementById('panePaste').style.display = tab === 'paste' ? 'block' : 'none';
  document.getElementById('paneGen').style.display   = tab === 'gen'   ? 'block' : 'none';
  document.getElementById('paneApi').style.display   = tab === 'api'   ? 'block' : 'none';
  document.getElementById('tabPaste').classList.toggle('active', tab === 'paste');
  document.getElementById('tabGen').classList.toggle('active', tab === 'gen');
  document.getElementById('tabApi').classList.toggle('active', tab === 'api');
}

function resetGenFlow() {
  ['genStep1', 'genStep2', 'genStep3'].forEach((id, i) => {
    document.getElementById(id).style.display = i === 0 ? 'block' : 'none';
  });
  ['genPhone', 'genApiId', 'genApiHash', 'genCode', 'gen2fa'].forEach(id => {
    document.getElementById(id).value = '';
  });
  ['genError1', 'genError2', 'genError3', 'addError'].forEach(id => {
    document.getElementById(id).style.display = 'none';
  });
  genTempId = null;
  genCodeValue = null;
}

// --- Tab 3: Get API credentials ---
let apiTempId = null;
let _apiId = '', _apiHash = '';

function apiReset() {
  apiTempId = null;
  ['apiStep1','apiStep2','apiStep3'].forEach((id,i) => {
    document.getElementById(id).style.display = i === 0 ? 'block' : 'none';
  });
  ['apiPhone','apiCode'].forEach(id => document.getElementById(id).value = '');
  ['apiErr1','apiErr2'].forEach(id => document.getElementById(id).style.display = 'none');
}

async function apiSendCode() {
  const phone = document.getElementById('apiPhone').value.trim();
  const errEl = document.getElementById('apiErr1');
  const btn   = document.getElementById('apiBtn1');
  if (!phone) { errEl.textContent = 'Введи номер телефону'; errEl.style.display = 'block'; return; }

  btn.disabled = true; btn.textContent = 'Відправка...'; errEl.style.display = 'none';
  try {
    const res  = await fetch('/api/tg-app/send-code', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ phone }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }
    apiTempId = data.temp_id;
    document.getElementById('apiPhoneDisplay').textContent = phone;
    document.getElementById('apiStep1').style.display = 'none';
    document.getElementById('apiStep2').style.display = 'block';
    setTimeout(() => document.getElementById('apiCode').focus(), 50);
  } catch { errEl.textContent = 'Помилка з\'єднання'; errEl.style.display = 'block'; }
  finally { btn.disabled = false; btn.textContent = 'Отримати код'; }
}

function apiBack() {
  if (apiTempId) {
    fetch('/api/tg-app/cancel', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ temp_id: apiTempId }),
    }).catch(()=>{});
    apiTempId = null;
  }
  document.getElementById('apiStep2').style.display = 'none';
  document.getElementById('apiStep1').style.display = 'block';
}

async function apiVerify() {
  const code  = document.getElementById('apiCode').value.trim();
  const errEl = document.getElementById('apiErr2');
  const btn   = document.getElementById('apiBtn2');
  if (!code) { errEl.textContent = 'Введи код'; errEl.style.display = 'block'; return; }

  btn.disabled = true; btn.textContent = 'Перевірка...'; errEl.style.display = 'none';
  try {
    const res  = await fetch('/api/tg-app/verify', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ temp_id: apiTempId, code }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }

    _apiId   = data.api_id;
    _apiHash = data.api_hash;
    document.getElementById('apiResultId').textContent   = _apiId;
    document.getElementById('apiResultHash').textContent = _apiHash;
    document.getElementById('apiStep2').style.display = 'none';
    document.getElementById('apiStep3').style.display = 'block';
    apiTempId = null;
  } catch { errEl.textContent = 'Помилка з\'єднання'; errEl.style.display = 'block'; }
  finally { btn.disabled = false; btn.textContent = 'Підтвердити'; }
}

function apiUseCredentials() {
  // Вставляємо у вкладку "Вставити сесію"
  document.getElementById('inputApiId').value   = _apiId;
  document.getElementById('inputApiHash').value = _apiHash;
  switchTab('paste');
  setTimeout(() => document.getElementById('inputSession').focus(), 50);
}

// --- Tab 1: Paste session ---
async function addAccount() {
  const session = document.getElementById('inputSession').value.trim();
  const apiId = document.getElementById('inputApiId').value.trim();
  const apiHash = document.getElementById('inputApiHash').value.trim();
  const errEl = document.getElementById('addError');
  const btn = document.getElementById('addBtn');

  if (!session || !apiId || !apiHash) {
    errEl.textContent = 'Заповни всі поля';
    errEl.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Підключення...';
  errEl.style.display = 'none';

  try {
    const res = await fetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_string: session, api_id: apiId, api_hash: apiHash }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка підключення'; errEl.style.display = 'block'; return; }
    closeAddModal();
    showToast('Акаунт підключено', 'success');
    await loadAccounts();
    selectAccount(data.id);
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Підключити';
  }
}

// --- Tab 2: Generate session ---
async function genSendCode() {
  const phone = document.getElementById('genPhone').value.trim();
  const apiId = document.getElementById('genApiId').value.trim();
  const apiHash = document.getElementById('genApiHash').value.trim();
  const errEl = document.getElementById('genError1');
  const btn = document.getElementById('genBtn1');

  if (!phone || !apiId || !apiHash) {
    errEl.textContent = 'Заповни всі поля';
    errEl.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Відправка...';
  errEl.style.display = 'none';

  try {
    const res = await fetch('/api/auth/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, api_id: apiId, api_hash: apiHash, account_id: refreshSessionAccountId }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }

    genTempId = data.temp_id;
    document.getElementById('genPhoneDisplay').textContent = phone;
    document.getElementById('genStep1').style.display = 'none';
    document.getElementById('genStep2').style.display = 'block';
    setTimeout(() => document.getElementById('genCode').focus(), 50);
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Отримати код';
  }
}

function genBack() {
  if (genTempId) {
    fetch('/api/auth/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ temp_id: genTempId }),
    }).catch(() => {});
    genTempId = null;
  }
  document.getElementById('genStep2').style.display = 'none';
  document.getElementById('genStep1').style.display = 'block';
}

async function genVerifyCode() {
  const code = document.getElementById('genCode').value.trim();
  const errEl = document.getElementById('genError2');
  const btn = document.getElementById('genBtn2');

  if (!code) { errEl.textContent = 'Введи код'; errEl.style.display = 'block'; return; }

  btn.disabled = true;
  btn.textContent = 'Перевірка...';
  errEl.style.display = 'none';
  genCodeValue = code;

  try {
    const res = await fetch('/api/auth/verify-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ temp_id: genTempId, code }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Невірний код'; errEl.style.display = 'block'; return; }

    if (data.needs_2fa) {
      document.getElementById('genStep2').style.display = 'none';
      document.getElementById('genStep3').style.display = 'block';
      setTimeout(() => document.getElementById('gen2fa').focus(), 50);
    } else {
      await genDone(data.id);
    }
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Підтвердити';
  }
}

async function genVerify2fa() {
  const password = document.getElementById('gen2fa').value;
  const errEl = document.getElementById('genError3');
  const btn = document.getElementById('genBtn3');

  if (!password) { errEl.textContent = 'Введи пароль'; errEl.style.display = 'block'; return; }

  btn.disabled = true;
  btn.textContent = 'Перевірка...';
  errEl.style.display = 'none';

  try {
    const res = await fetch('/api/auth/verify-2fa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ temp_id: genTempId, password }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Невірний пароль'; errEl.style.display = 'block'; return; }
    await genDone(data.id);
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Підтвердити';
  }
}

async function genDone(accountId) {
  genTempId = null;
  const wasRefresh = !!refreshSessionAccountId;
  refreshSessionAccountId = null;
  closeAddModal();
  showToast(wasRefresh ? 'Сесію оновлено' : 'Акаунт авторизовано і збережено', 'success');
  await loadAccounts();
  selectAccount(accountId);
}

// ===== EDIT PROFILE =====
function openEditModal() {
  const nameParts = document.getElementById('fName').textContent.split(' ');
  document.getElementById('editFirstName').value = nameParts[0] !== '—' ? nameParts[0] : '';
  document.getElementById('editLastName').value = nameParts.slice(1).join(' ') || '';
  document.getElementById('editUsername').value = (document.getElementById('fUsername').textContent || '').replace('@', '').replace('—','');
  const bio = document.getElementById('fBio').textContent;
  document.getElementById('editBio').value = bio === '—' ? '' : bio;
  document.getElementById('editError').style.display = 'none';

  // Sync avatar in edit modal
  const src = document.getElementById('avatarImg').src;
  const editImg = document.getElementById('editAvatarImg');
  const editPh = document.getElementById('editAvatarPh');
  if (src && !src.endsWith('/') && document.getElementById('avatarImg').style.display !== 'none') {
    editImg.src = src;
    editImg.style.display = 'block';
    editPh.style.display = 'none';
  } else {
    editImg.style.display = 'none';
    editPh.style.display = 'flex';
    editPh.textContent = document.getElementById('avatarPlaceholder').textContent;
  }

  updateBioCount();
  document.getElementById('editBio').addEventListener('input', updateBioCount);
  document.getElementById('editModal').style.display = 'flex';
}

function updateBioCount() {
  const len = document.getElementById('editBio').value.length;
  const el = document.getElementById('bioCount');
  el.textContent = `${len} / 70`;
  el.style.color = len > 65 ? 'var(--danger)' : 'var(--text-muted)';
}

function closeEditModal() {
  document.getElementById('editModal').style.display = 'none';
}

async function saveProfile() {
  const btn = document.getElementById('editBtn');
  const errEl = document.getElementById('editError');
  btn.disabled = true;
  btn.textContent = 'Збереження...';
  errEl.style.display = 'none';

  const body = {
    first_name: document.getElementById('editFirstName').value.trim(),
    last_name: document.getElementById('editLastName').value.trim(),
    bio: document.getElementById('editBio').value.trim(),
    username: document.getElementById('editUsername').value.trim() || null,
  };

  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/profile`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }

    closeEditModal();
    showToast('Профіль оновлено', 'success');
    // Оновлюємо відображення
    selectAccount(currentAccountId);
    loadAccounts();
  } catch {
    errEl.textContent = 'Помилка з\'єднання';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Зберегти';
  }
}

// ===== PHOTO UPLOAD =====
function triggerPhotoUpload() {
  document.getElementById('photoFileInput').click();
}

async function uploadPhoto(input) {
  if (!input.files.length) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append('file', file);

  showToast('Завантаження фото...', '');
  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/photo`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) { showToast('Помилка завантаження фото', 'error'); return; }
    showToast('Фото оновлено', 'success');
    // Перезавантажуємо фото
    const img = document.getElementById('avatarImg');
    img.src = `/api/accounts/${currentAccountId}/photo?t=${Date.now()}`;
  } catch {
    showToast('Помилка з\'єднання', 'error');
  }
  input.value = '';
}

// ===== CREATE CHANNEL =====
let chType = 'public';
let chPhotoFile = null;

function openChannelModal() {
  document.getElementById('chTitle').value = '';
  document.getElementById('chAbout').value = '';
  document.getElementById('chUsername').value = '';
  document.getElementById('chError1').style.display = 'none';
  document.getElementById('chError2').style.display = 'none';
  document.getElementById('chResult').style.display = 'none';
  document.getElementById('chCreateBtn').style.display = 'inline-block';
  document.getElementById('chStep1').style.display = 'block';
  document.getElementById('chStep2').style.display = 'none';
  document.getElementById('chPhotoPreview').style.display = 'none';
  document.getElementById('chPhotoIcon').style.display = 'block';
  chPhotoFile = null;
  selectChType('public');
  document.getElementById('channelModal').style.display = 'flex';
  setTimeout(() => document.getElementById('chTitle').focus(), 50);
}

function closeChannelModal() {
  document.getElementById('channelModal').style.display = 'none';
}

function chGoStep2() {
  const title = document.getElementById('chTitle').value.trim();
  const errEl = document.getElementById('chError1');
  if (!title) { errEl.textContent = 'Введи назву каналу'; errEl.style.display = 'block'; return; }
  errEl.style.display = 'none';
  document.getElementById('chStep1').style.display = 'none';
  document.getElementById('chStep2').style.display = 'block';
}

function chGoStep1() {
  document.getElementById('chStep2').style.display = 'none';
  document.getElementById('chStep1').style.display = 'block';
}

function selectChType(type) {
  chType = type;
  document.getElementById('chOptPublic').classList.toggle('selected', type === 'public');
  document.getElementById('chOptPrivate').classList.toggle('selected', type === 'private');
  document.getElementById('chRadioPublic').classList.toggle('active', type === 'public');
  document.getElementById('chRadioPrivate').classList.toggle('active', type === 'private');
  document.getElementById('chUsernameWrap').style.display = type === 'public' ? 'block' : 'none';
}

function previewChPhoto(input) {
  if (!input.files.length) return;
  chPhotoFile = input.files[0];
  const reader = new FileReader();
  reader.onload = e => {
    document.getElementById('chPhotoPreview').src = e.target.result;
    document.getElementById('chPhotoPreview').style.display = 'block';
    document.getElementById('chPhotoIcon').style.display = 'none';
  };
  reader.readAsDataURL(chPhotoFile);
}

async function createChannel() {
  const title = document.getElementById('chTitle').value.trim();
  const about = document.getElementById('chAbout').value.trim();
  const username = chType === 'public' ? document.getElementById('chUsername').value.trim() : '';
  const errEl = document.getElementById('chError2');
  const btn = document.getElementById('chCreateBtn');

  if (chType === 'public' && !username) {
    errEl.textContent = 'Введи username для публічного каналу';
    errEl.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Створення...';
  errEl.style.display = 'none';

  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/channel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, about, username, megagroup: false }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }

    // Завантажуємо фото якщо вибрано
    if (chPhotoFile && data.id) {
      const fd = new FormData();
      fd.append('file', chPhotoFile);
      fd.append('access_hash', data.access_hash || '0');
      await fetch(`/api/accounts/${currentAccountId}/channels/${data.id}/photo`, {
        method: 'POST', body: fd,
      }).catch(() => {});
    }

    document.getElementById('chResultTitle').textContent = data.title;
    const linkEl = document.getElementById('chResultLink');
    if (data.link) { linkEl.textContent = data.link; linkEl.href = data.link; linkEl.style.display = 'block'; }
    else { linkEl.style.display = 'none'; }
    document.getElementById('chResult').style.display = 'block';
    btn.style.display = 'none';
    showToast(`Канал "${data.title}" створено`, 'success');
    loadChannels(); // оновлюємо список
  } catch {
    errEl.textContent = 'Помилка з\'єднання';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Створити канал';
  }
}

// ===== FLOOD STATUS =====
let floodInterval = null;
let floodEndsAt = null;
let floodTotal = 0;

async function loadFloodStatus() {
  if (!currentAccountId) return;
  document.getElementById('floodLoading').style.display = 'block';
  document.getElementById('floodOk').style.display = 'none';
  document.getElementById('floodWarn').style.display = 'none';
  document.getElementById('floodRestricted').style.display = 'none';

  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/status`);
    const data = await res.json();
    renderFloodStatus(data);
  } catch {
    document.getElementById('floodLoading').textContent = 'Помилка перевірки';
  }
}

function refreshFloodStatus() {
  clearFloodTimer();
  loadFloodStatus();
}

function renderFloodStatus(data) {
  document.getElementById('floodLoading').style.display = 'none';
  const badge = document.getElementById('floodBadge');

  if (!data.connected) {
    badge.textContent = '● Відключено';
    badge.className = 'flood-badge badge-off';
    badge.style.display = 'inline-block';
    return;
  }

  if (data.restricted) {
    document.getElementById('floodRestricted').style.display = 'flex';
    document.getElementById('floodReason').textContent = data.restriction_reason || 'Причина невідома';
    badge.textContent = '🚫 Обмежено';
    badge.className = 'flood-badge badge-restricted';
    badge.style.display = 'inline-block';
    return;
  }

  if (data.flood && data.flood.active) {
    document.getElementById('floodWarn').style.display = 'block';
    badge.textContent = '⛔ Флуд';
    badge.className = 'flood-badge badge-flood';
    badge.style.display = 'inline-block';

    floodTotal = data.flood.total_seconds;
    floodEndsAt = new Date(data.flood.expires_at);
    startFloodTimer();

    const exp = new Date(data.flood.expires_at);
    document.getElementById('floodExpires').textContent =
      'Закінчиться: ' + exp.toLocaleString('uk-UA', { timeZone: 'Europe/Kiev', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    return;
  }

  document.getElementById('floodOk').style.display = 'flex';
  badge.textContent = '✓ Норма';
  badge.className = 'flood-badge badge-ok';
  badge.style.display = 'inline-block';
}

function startFloodTimer() {
  clearFloodTimer();
  updateFloodTimer();
  floodInterval = setInterval(updateFloodTimer, 1000);
}

function clearFloodTimer() {
  if (floodInterval) { clearInterval(floodInterval); floodInterval = null; }
  floodEndsAt = null;
}

function updateFloodTimer() {
  if (!floodEndsAt) return;
  const now = new Date();
  const diff = Math.max(0, Math.floor((floodEndsAt - now) / 1000));

  if (diff === 0) {
    clearFloodTimer();
    document.getElementById('floodWarn').style.display = 'none';
    document.getElementById('floodOk').style.display = 'flex';
    const badge = document.getElementById('floodBadge');
    badge.textContent = '✓ Норма';
    badge.className = 'flood-badge badge-ok';
    return;
  }

  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = diff % 60;
  document.getElementById('floodTimer').textContent =
    `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;

  const pct = floodTotal > 0 ? Math.round((diff / floodTotal) * 100) : 0;
  document.getElementById('floodProgressBar').style.width = pct + '%';
}

// ===== CHANNELS LIST =====
async function loadChannels() {
  const section = document.getElementById('channelsSection');
  const list = document.getElementById('channelsList');
  if (!currentAccountId || !section) return;
  list.innerHTML = '<div class="no-codes">Завантаження...</div>';
  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/channels`);
    const channels = await res.json();
    if (!channels.length) {
      list.innerHTML = '<div class="no-codes">Каналів немає</div>';
      return;
    }
    list.innerHTML = channels.map(ch => channelItemHTML(ch)).join('');
  } catch {
    list.innerHTML = '<div class="no-codes">Помилка завантаження</div>';
  }
}

function channelItemHTML(ch) {
  const link = ch.link ? `<a href="${ch.link}" target="_blank" class="ch-link">${ch.link}</a>` : '<span class="ch-link-private">Приватний</span>';
  const subs = ch.subscribers ? `${ch.subscribers.toLocaleString('uk')} підп.` : '';
  return `
    <div class="ch-item" id="chi-${ch.id}">
      <div class="ch-item-info">
        <div class="ch-item-title">${escHtml(ch.title)}</div>
        <div class="ch-item-meta">
          ${link}${subs ? ' · ' + subs : ''}
          ${ch.is_creator ? ' · <span style="color:var(--accent)">Власник</span>' : ''}
        </div>
      </div>
      <div class="ch-item-actions">
        <button class="btn-copy-sm" onclick="openPostModal(${ch.id}, '${escHtml(ch.title).replace(/'/g,"\\'")}')">✏️ Пост</button>
        <button class="btn-copy-sm" onclick="setPersonalChannel(${ch.id})">⭐ В профіль</button>
      </div>
    </div>
  `;
}

// ===== POST MODAL =====
let postChannelId = null;
let postPhotoFile = null;

function openPostModal(channelId, channelTitle) {
  postChannelId = channelId;
  postPhotoFile = null;
  document.getElementById('postChannelName').textContent = channelTitle;
  document.getElementById('postText').value = '';
  document.getElementById('postPhotoPreview').style.display = 'none';
  document.getElementById('postError').style.display = 'none';
  document.getElementById('postBtn').style.display = 'inline-block';
  document.getElementById('postModal').style.display = 'flex';
  setTimeout(() => document.getElementById('postText').focus(), 50);
}

function closePostModal() {
  document.getElementById('postModal').style.display = 'none';
}

function pickPostPhoto() {
  document.getElementById('postPhotoInput').click();
}

function previewPostPhoto(input) {
  if (!input.files.length) return;
  postPhotoFile = input.files[0];
  const reader = new FileReader();
  reader.onload = e => {
    const img = document.getElementById('postPhotoPreview');
    img.src = e.target.result;
    img.style.display = 'block';
  };
  reader.readAsDataURL(postPhotoFile);
}

function removePostPhoto() {
  postPhotoFile = null;
  document.getElementById('postPhotoPreview').style.display = 'none';
  document.getElementById('postPhotoInput').value = '';
}

async function publishPost() {
  const text = document.getElementById('postText').value.trim();
  const errEl = document.getElementById('postError');
  const btn = document.getElementById('postBtn');

  if (!text && !postPhotoFile) {
    errEl.textContent = 'Додай текст або фото';
    errEl.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Публікація...';
  errEl.style.display = 'none';

  try {
    let res;
    if (postPhotoFile) {
      const fd = new FormData();
      fd.append('text', text);
      fd.append('file', postPhotoFile);
      res = await fetch(`/api/accounts/${currentAccountId}/channels/${postChannelId}/post-with-photo`, {
        method: 'POST', body: fd,
      });
    } else {
      res = await fetch(`/api/accounts/${currentAccountId}/channels/${postChannelId}/post`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
    }
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }
    closePostModal();
    showToast('Пост опубліковано', 'success');
  } catch {
    errEl.textContent = 'Помилка з\'єднання';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Опублікувати';
  }
}

// ===== SET PERSONAL CHANNEL =====
async function setPersonalChannel(channelId) {
  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/channels/${channelId}/personal`, {
      method: 'PATCH',
    });
    const data = await res.json();
    if (!res.ok) { showToast(data.detail || 'Помилка', 'error'); return; }
    showToast('Канал додано в профіль', 'success');
  } catch {
    showToast('Помилка з\'єднання', 'error');
  }
}

// ===== JOIN CHANNEL =====
async function joinChannel() {
  const link = document.getElementById('joinInput').value.trim();
  const btn = document.getElementById('joinBtn');
  const errEl = document.getElementById('joinError');
  if (!link) return;

  btn.disabled = true;
  btn.textContent = '...';
  errEl.style.display = 'none';

  try {
    const res = await fetch(`/api/accounts/${currentAccountId}/join`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ link }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Помилка'; errEl.style.display = 'block'; return; }
    document.getElementById('joinInput').value = '';
    showToast(`Підписались на "${data.title}"`, 'success');
    loadChannels();
  } catch {
    errEl.textContent = 'Помилка з\'єднання';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Підписатись';
  }
}

// ===== DELETE ACCOUNT =====
async function deleteCurrentAccount() {
  if (!currentAccountId) return;
  if (!confirm('Видалити акаунт? Це незворотно.')) return;

  try {
    await fetch(`/api/accounts/${currentAccountId}`, { method: 'DELETE' });
    currentAccountId = null;
    document.getElementById('welcome').style.display = 'flex';
    document.getElementById('accountDetail').style.display = 'none';
    showToast('Акаунт видалено', 'success');
    await loadAccounts();
  } catch {
    showToast('Помилка видалення', 'error');
  }
}

// ===== HELPERS =====
function fullName(a) {
  return [a.first_name, a.last_name].filter(Boolean).join(' ') || a.phone || `#${a.id}`;
}

function avatarLetter(a) {
  const letter = (a.first_name || a.phone || '?')[0].toUpperCase();
  return `<span style="position:relative;z-index:1">${letter}</span>`;
}

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString('uk-UA', {
    timeZone: 'Europe/Kiev',
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit'
  });
}

function escHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

let toastTimer;
function showToast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = 'toast', 3500);
}

// ===== BULK =====
let bulkCreatedChannels = []; // [{account_id, channel_id, access_hash, username, link}]

const _TOOL_MODAL_IDS = [
  'bulkModal','inviteModal','commentModal','reactModal',
  'commentReactModal','myChannelsModal','broadcastModal','inboxModal','viewsModal'
];
function _closeAllToolModals() {
  _TOOL_MODAL_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  // reset inbox mobile pane state
  const inboxDialogs = document.getElementById('inboxDialogs');
  const inboxChat = document.getElementById('inboxChat');
  if (inboxDialogs) inboxDialogs.classList.remove('mob-hidden');
  if (inboxChat) inboxChat.classList.remove('mob-active');
}

function openBulkModal() {
  _closeAllToolModals();
  bulkCreatedChannels = [];
  document.getElementById('bulkModal').style.display = 'flex';
  switchBulkTab('channel');
  renderBulkAccounts();
}

function closeBulkModal() {
  document.getElementById('bulkModal').style.display = 'none';
}

function switchBulkTab(tab) {
  ['channel','post','join'].forEach(t => {
    document.getElementById('bulkTab' + t.charAt(0).toUpperCase() + t.slice(1)).classList.toggle('active', t === tab);
    document.getElementById('bulkPane' + t.charAt(0).toUpperCase() + t.slice(1)).style.display = t === tab ? 'block' : 'none';
  });
  document.getElementById('bulkResults').style.display = 'none';
  if (tab === 'post') renderBulkChannels();
  if (tab === 'join') renderBulkJoinAccounts();
}

function bulkUnameToggle() {
  const mode = document.querySelector('input[name="bulkUnameMode"]:checked').value;
  document.getElementById('bulkUnameVal').style.display = mode === 'manual' ? 'block' : 'none';
}

function renderBulkAccounts() {
  const list = document.getElementById('bulkAccountList');
  list.innerHTML = allAccounts.filter(a => a.is_connected).map(a => `
    <label class="bulk-acc-row">
      <input type="checkbox" class="bulk-acc-check" value="${a.id}" checked style="width:15px;height:15px">
      <span>${a.first_name || ''} ${a.last_name || ''}</span>
      <span style="color:var(--muted)">@${a.username || a.phone || a.id}</span>
    </label>
  `).join('') || '<div style="color:var(--muted);font-size:13px">Немає підключених акаунтів</div>';
}

let allBulkChannels = []; // [{account_id, channel_id, access_hash, title, link}]

function renderBulkChannels(channels) {
  const src = channels || (bulkCreatedChannels.length ? bulkCreatedChannels : allBulkChannels);
  const list = document.getElementById('bulkChannelList');
  if (!src.length) {
    list.innerHTML = '<div style="color:var(--muted);font-size:13px">Натисни "Завантажити всі" або спочатку створи канали</div>';
    return;
  }

  // Групуємо по акаунту
  const byAcc = {};
  src.forEach(c => {
    if (!byAcc[c.account_id]) byAcc[c.account_id] = [];
    byAcc[c.account_id].push(c);
  });

  list.innerHTML = Object.entries(byAcc).map(([aid, chs]) => {
    const acc = allAccounts.find(a => a.id == aid);
    const accName = acc ? `${acc.first_name || ''} @${acc.username || acc.id}`.trim() : `#${aid}`;
    const rows = chs.map(c => `
      <label class="bulk-ch-row" style="padding-left:20px">
        <input type="checkbox" class="bulk-ch-check" value="${c.account_id}|${c.channel_id}|${c.access_hash}|${c.username||''}" checked style="width:14px;height:14px">
        <span style="flex:1">${c.title || c.channel_id}</span>
        ${c.link ? `<a href="${c.link}" target="_blank" style="color:var(--accent);font-size:11px">${c.link}</a>` : ''}
      </label>`).join('');
    return `<div style="font-size:12px;color:var(--muted);padding:4px 2px;font-weight:600">${accName}</div>${rows}`;
  }).join('');
}

async function bulkLoadAllChannels() {
  const btn = document.getElementById('bulkLoadChBtn');
  btn.disabled = true; btn.textContent = 'Завантаження...';
  try {
    const res = await fetch('/api/bulk/channels');
    allBulkChannels = await res.json();
    renderBulkChannels(allBulkChannels);
  } catch {
    showToast('Не вдалось завантажити канали', 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Завантажити всі';
  }
}

function bulkSelectAllChannels(checked) {
  document.querySelectorAll('.bulk-ch-check').forEach(el => el.checked = checked);
}

async function bulkCreateChannels() {
  const title = document.getElementById('bulkTitle').value.trim();
  const errEl = document.getElementById('bulkChErr');
  if (!title) { errEl.textContent = 'Введи назву каналу'; errEl.style.display = 'block'; return; }
  errEl.style.display = 'none';

  const checkedIds = [...document.querySelectorAll('.bulk-acc-check:checked')].map(el => +el.value);
  if (!checkedIds.length) { errEl.textContent = 'Вибери хоча б один акаунт'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('bulkChBtn');
  btn.disabled = true; btn.textContent = 'Створення...';

  const about = document.getElementById('bulkAbout').value.trim();
  const mode = document.querySelector('input[name="bulkUnameMode"]:checked').value;
  const uval = document.getElementById('bulkUnameVal').value.trim().replace('@', '');
  const addToProfile = document.getElementById('bulkAddToProfile').checked;
  const photoFile = document.getElementById('bulkPhoto').files[0];

  const fd = new FormData();
  fd.append('title', title);
  fd.append('about', about);
  fd.append('username_mode', mode);
  fd.append('username_val', uval);
  fd.append('add_to_profile', addToProfile ? 'true' : 'false');
  fd.append('account_ids', JSON.stringify(checkedIds));
  if (photoFile) fd.append('photo', photoFile);

  try {
    const res = await fetch('/api/bulk/create-channel', { method: 'POST', body: fd });
    const data = await res.json();
    bulkCreatedChannels = data.results.filter(r => r.success);
    showBulkResults(data.results, 'channel');
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером'; errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Створити на всіх';
  }
}

async function bulkPost() {
  const text = document.getElementById('bulkPostText').value.trim();
  const errEl = document.getElementById('bulkPostErr');
  errEl.style.display = 'none';

  const checked = [...document.querySelectorAll('.bulk-ch-check:checked')].map(el => {
    const [aid, cid, hash, uname] = el.value.split('|');
    return { account_id: +aid, channel_id: +cid, access_hash: +hash, username: uname || null };
  });
  if (!checked.length) { errEl.textContent = 'Вибери хоча б один канал'; errEl.style.display = 'block'; return; }
  if (!text && !document.getElementById('bulkPostPhoto').files[0]) {
    errEl.textContent = 'Введи текст або вибери фото'; errEl.style.display = 'block'; return;
  }

  const btn = document.getElementById('bulkPostBtn');
  btn.disabled = true; btn.textContent = 'Публікація...';

  const fd = new FormData();
  fd.append('text', text);
  fd.append('channels', JSON.stringify(checked));
  const photoFile = document.getElementById('bulkPostPhoto').files[0];
  if (photoFile) fd.append('photo', photoFile);

  try {
    const res = await fetch('/api/bulk/post', { method: 'POST', body: fd });
    const data = await res.json();
    showBulkResults(data.results, 'post');
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером'; errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Опублікувати';
  }
}

function renderBulkJoinAccounts() {
  const list = document.getElementById('bulkJoinAccountList');
  list.innerHTML = allAccounts.filter(a => a.is_connected).map(a => `
    <label class="bulk-acc-row">
      <input type="checkbox" class="bulk-join-check" value="${a.id}" checked style="width:15px;height:15px">
      <span>${a.first_name || ''} ${a.last_name || ''}</span>
      <span style="color:var(--muted)">@${a.username || a.phone || a.id}</span>
    </label>
  `).join('') || '<div style="color:var(--muted);font-size:13px">Немає підключених акаунтів</div>';
}

async function bulkJoin() {
  const link = document.getElementById('bulkJoinLink').value.trim();
  const errEl = document.getElementById('bulkJoinErr');
  errEl.style.display = 'none';
  if (!link) { errEl.textContent = 'Введи канал або посилання'; errEl.style.display = 'block'; return; }

  const ids = [...document.querySelectorAll('.bulk-join-check:checked')].map(el => +el.value);
  if (!ids.length) { errEl.textContent = 'Вибери хоча б один акаунт'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('bulkJoinBtn');
  btn.disabled = true; btn.textContent = 'Підписка...';

  const fd = new FormData();
  fd.append('link', link);
  fd.append('account_ids', JSON.stringify(ids));

  try {
    const res = await fetch('/api/bulk/join', { method: 'POST', body: fd });
    const data = await res.json();
    showBulkResults(data.results, 'join');
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером'; errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Підписатись';
  }
}

function showBulkResults(results, type) {
  document.getElementById('bulkPaneChannel').style.display = 'none';
  document.getElementById('bulkPanePost').style.display = 'none';
  document.getElementById('bulkResults').style.display = 'block';
  document.getElementById('bulkGoPostBtn').style.display = type === 'channel' && bulkCreatedChannels.length ? 'block' : 'none';

  const list = document.getElementById('bulkResultsList');
  list.innerHTML = results.map(r => {
    const acc = allAccounts.find(a => a.id === r.account_id);
    const name = acc ? `${acc.first_name || ''} @${acc.username || acc.id}` : `#${r.account_id}`;
    if (r.success) {
      const detail = r.link ? `<a href="${r.link}" target="_blank" style="color:var(--accent)">${r.link}</a>` :
                     r.message_id ? `Пост #${r.message_id}` :
                     r.note ? `<span style="color:var(--muted)">${r.note}</span>` : 'OK';
      return `<div class="bulk-result-row ok"><span style="color:var(--online)">✓</span><span>${name}</span><span style="margin-left:auto">${detail}</span></div>`;
    } else {
      return `<div class="bulk-result-row err"><span style="color:var(--danger)">✕</span><span>${name}</span><span style="margin-left:auto;color:var(--danger);font-size:12px">${r.error || 'Помилка'}</span></div>`;
    }
  }).join('');
}

// ===== INVITE =====

let _parsedCount = 0;

function openInviteModal() {
  _closeAllToolModals();
  renderInviteAccounts();
  renderParseAccountSelect();
  document.getElementById('inviteModal').style.display = 'flex';
  switchInviteTab('parse');

  fetch('/api/invite/status').then(r => r.json()).then(s => {
    updateInviteStats(s);
    _parsedCount = s.parse_count || 0;
    updateParsedCountLabel();
    if (s.log && s.log.length) {
      const term = document.getElementById('inviteTerminal');
      term.innerHTML = '';
      s.log.forEach(entry => appendInviteLog(entry, false));
      term.scrollTop = term.scrollHeight;
    }
    // restore invite button state
    if (s.running) {
      document.getElementById('inviteStartBtn').style.display = 'none';
      document.getElementById('inviteStopBtn').style.display = 'inline-flex';
    } else {
      document.getElementById('inviteStartBtn').style.display = 'inline-flex';
      document.getElementById('inviteStartBtn').disabled = false;
      document.getElementById('inviteStopBtn').style.display = 'none';
    }
    // restore parse button state
    if (s.parse_running) {
      document.getElementById('parseStartBtn').style.display = 'none';
      document.getElementById('parseStopBtn').style.display = 'inline-flex';
    }
    if (s.parse_count > 0) {
      document.getElementById('parsedCount').textContent = s.parse_count;
      document.getElementById('parseResult').style.display = 'block';
    }
  }).catch(() => {});
}

function closeInviteModal() {
  document.getElementById('inviteModal').style.display = 'none';
}

function switchInviteTab(tab) {
  document.getElementById('invitePaneParse').style.display = tab === 'parse' ? 'block' : 'none';
  document.getElementById('invitePaneInvite').style.display = tab === 'invite' ? 'block' : 'none';
  document.getElementById('inviteTabParse').classList.toggle('active', tab === 'parse');
  document.getElementById('inviteTabInvite').classList.toggle('active', tab === 'invite');
  if (tab === 'invite') updateParsedCountLabel();
}

function renderParseAccountSelect() {
  const sel = document.getElementById('parseAccountId');
  sel.innerHTML = allAccounts.filter(a => a.is_connected).map(a =>
    `<option value="${a.id}">${a.first_name || ''} ${a.last_name || ''} (@${a.username || a.id})</option>`
  ).join('') || '<option value="">Немає підключених акаунтів</option>';
}

function renderInviteAccounts() {
  const list = document.getElementById('inviteAccountList');
  list.innerHTML = allAccounts.filter(a => a.is_connected).map(a => `
    <label class="bulk-acc-row">
      <input type="checkbox" class="invite-acc-check" value="${a.id}" checked style="width:15px;height:15px;flex-shrink:0">
      <span>${a.first_name || ''} ${a.last_name || ''}</span>
      <span style="color:var(--muted);font-size:12px;margin-left:auto">@${a.username || a.id}</span>
    </label>
  `).join('') || '<div style="color:var(--muted);font-size:13px">Немає підключених акаунтів</div>';
}

function inviteSelectAll(v) {
  document.querySelectorAll('.invite-acc-check').forEach(c => c.checked = v);
}

function toggleInviteSource() {
  const useParsed = document.getElementById('inviteUseParsed').checked;
  document.getElementById('inviteCsvWrap').style.display = useParsed ? 'none' : 'block';
}

function updateParsedCountLabel() {
  const lbl = document.getElementById('parsedCountLabel');
  if (lbl) lbl.textContent = _parsedCount > 0 ? `(${_parsedCount} учасників)` : '(немає)';
  // auto-select parsed if available
  if (_parsedCount > 0) {
    document.getElementById('inviteUseParsed').checked = true;
    toggleInviteSource();
  }
}

function appendInviteLog(entry, scroll = true) {
  const term = document.getElementById('inviteTerminal');
  if (!term) return;
  const div = document.createElement('div');
  div.className = `log-${entry.level}`;
  div.textContent = entry.msg;
  term.appendChild(div);
  if (scroll) term.scrollTop = term.scrollHeight;
}

function updateInviteStats(s) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val ?? 0; };
  set('iTotal', s.total);
  set('iDone', s.done);
  set('iSuccess', s.success);
  set('iFailed', s.failed);
  set('iSkipped', s.skipped);
  const lbl = document.getElementById('iRunningLabel');
  if (lbl) {
    lbl.textContent = s.running ? '● ВИКОНУЄТЬСЯ' : (s.done > 0 ? '■ ЗАВЕРШЕНО' : '');
    lbl.style.color = s.running ? '#00e676' : '#888';
  }
}

// --- Join all accounts to source group ---

async function joinAllToSource() {
  const source = document.getElementById('parseSource').value.trim();
  if (!source) {
    document.getElementById('parseErr').textContent = 'Введи джерело';
    document.getElementById('parseErr').style.display = 'block';
    return;
  }
  document.getElementById('parseErr').style.display = 'none';

  const btn = document.getElementById('joinAllBtn');
  btn.disabled = true;
  btn.textContent = 'Підписка...';

  const resultEl = document.getElementById('joinAllResult');
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<span style="color:#40c4ff">Підписуємо всі акаунти...</span>';

  const ids = allAccounts.filter(a => a.is_connected).map(a => a.id);

  const fd = new FormData();
  fd.append('link', source);
  fd.append('account_ids', JSON.stringify(ids));

  try {
    const res = await fetch('/api/bulk/join', { method: 'POST', body: fd });
    const data = await res.json();
    resultEl.innerHTML = data.results.map(r => {
      const acc = allAccounts.find(a => a.id === r.account_id);
      const name = acc ? (acc.first_name || acc.username || `#${r.account_id}`) : `#${r.account_id}`;
      if (r.success) {
        const note = r.note ? ` (${r.note})` : ' ✓';
        return `<span style="color:#00e676">${name}${note}</span>`;
      } else {
        return `<span style="color:#ff5252">${name}: ${r.error || 'помилка'}</span>`;
      }
    }).join('<br>');
  } catch {
    resultEl.innerHTML = '<span style="color:#ff5252">Помилка з\'єднання</span>';
  }

  btn.disabled = false;
  btn.textContent = '👥 Підписати всіх';
}

// --- Parse ---

async function startParse() {
  const source = document.getElementById('parseSource').value.trim();
  const accountId = document.getElementById('parseAccountId').value;
  const errEl = document.getElementById('parseErr');
  errEl.style.display = 'none';

  if (!source) { errEl.textContent = 'Введи джерело (групу)'; errEl.style.display = 'block'; return; }
  if (!accountId) { errEl.textContent = 'Немає акаунтів'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('parseStartBtn');
  btn.disabled = true; btn.textContent = 'Збір...';

  document.getElementById('parseResult').style.display = 'none';
  document.getElementById('inviteTerminal').innerHTML = '';

  const fd = new FormData();
  fd.append('source', source);
  fd.append('account_id', accountId);

  try {
    const res = await fetch('/api/invite/parse', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) {
      errEl.textContent = data.error; errEl.style.display = 'block';
      btn.disabled = false; btn.textContent = '🔍 Зібрати учасників';
      return;
    }
    btn.style.display = 'none';
    document.getElementById('parseStopBtn').style.display = 'inline-flex';
  } catch {
    errEl.textContent = 'Помилка з\'єднання'; errEl.style.display = 'block';
    btn.disabled = false; btn.textContent = '🔍 Зібрати учасників';
  }
}

async function stopParse() {
  await fetch('/api/invite/stop', { method: 'POST' });
  document.getElementById('parseStopBtn').style.display = 'none';
  document.getElementById('parseStartBtn').style.display = 'inline-flex';
  document.getElementById('parseStartBtn').disabled = false;
  document.getElementById('parseStartBtn').textContent = '🔍 Зібрати учасників';
}

// --- Invite ---

async function startInvite() {
  const useParsed = document.getElementById('inviteUseParsed').checked;
  const channel = document.getElementById('inviteChannel').value.trim();
  const interval = document.getElementById('inviteInterval').value;
  const errEl = document.getElementById('inviteErr');
  errEl.style.display = 'none';

  if (!channel) { errEl.textContent = 'Введи канал'; errEl.style.display = 'block'; return; }

  const ids = [...document.querySelectorAll('.invite-acc-check:checked')].map(el => +el.value);
  if (!ids.length) { errEl.textContent = 'Вибери хоча б один акаунт'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('inviteStartBtn');
  btn.disabled = true; btn.textContent = 'Запуск...';

  const fd = new FormData();
  fd.append('channel', channel);
  fd.append('account_ids', JSON.stringify(ids));
  fd.append('interval', interval);
  fd.append('use_parsed', useParsed ? 'true' : 'false');

  if (!useParsed) {
    const csvFile = document.getElementById('inviteCsv').files[0];
    if (!csvFile) {
      errEl.textContent = 'Вибери CSV файл або використай зібраних учасників';
      errEl.style.display = 'block';
      btn.disabled = false; btn.textContent = '▶ Запустити';
      return;
    }
    fd.append('csv_file', csvFile);
  }

  try {
    const res = await fetch('/api/invite/start', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) {
      errEl.textContent = data.error; errEl.style.display = 'block';
      btn.disabled = false; btn.textContent = '▶ Запустити';
      return;
    }
    document.getElementById('inviteTerminal').innerHTML = '';
    btn.style.display = 'none';
    document.getElementById('inviteStopBtn').style.display = 'inline-flex';
  } catch {
    errEl.textContent = 'Помилка з\'єднання з сервером';
    errEl.style.display = 'block';
    btn.disabled = false; btn.textContent = '▶ Запустити';
  }
}

async function stopInvite() {
  await fetch('/api/invite/stop', { method: 'POST' });
  document.getElementById('inviteStopBtn').style.display = 'none';
  document.getElementById('inviteStartBtn').style.display = 'inline-flex';
  document.getElementById('inviteStartBtn').disabled = false;
  document.getElementById('inviteStartBtn').textContent = '▶ Запустити';
}

// ===== COMMENT =====

async function openCommentModal() {
  _closeAllToolModals();
  document.getElementById('commentModal').style.display = 'flex';
  await loadCommentChannels();
  fetch('/api/comment/status').then(r => r.json()).then(s => {
    updateCommentStats(s);
    if (s.log && s.log.length) {
      const term = document.getElementById('commentTerminal');
      term.innerHTML = '';
      s.log.forEach(e => appendCommentLog(e, false));
      term.scrollTop = term.scrollHeight;
    }
    if (s.running) {
      document.getElementById('commentStartBtn').style.display = 'none';
      document.getElementById('commentStopBtn').style.display = 'inline-flex';
    } else {
      document.getElementById('commentStartBtn').style.display = 'inline-flex';
      document.getElementById('commentStartBtn').disabled = false;
      document.getElementById('commentStopBtn').style.display = 'none';
    }
  }).catch(() => {});
}

function closeCommentModal() {
  document.getElementById('commentModal').style.display = 'none';
}

async function loadCommentChannels() {
  const list = document.getElementById('commentChannelList');
  try {
    const res = await fetch('/api/comment/channels');
    const channels = await res.json();
    if (!channels.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:13px">Немає каналів — додай нижче</div>';
      return;
    }
    list.innerHTML = channels.map(ch => {
      const acc = allAccounts.find(a => a.id === ch.account_id);
      const accName = acc ? (acc.first_name || acc.username || `#${ch.account_id}`) : `#${ch.account_id}`;
      const link = ch.username ? `https://t.me/${ch.username}` : '';
      return `
        <div class="comment-ch-row">
          <span class="comment-ch-toggle" onclick="toggleCommentChannel(${ch.id})" title="${ch.enabled ? 'Вимкнути' : 'Увімкнути'}"
                style="color:${ch.enabled ? '#00e676' : '#888'}">●</span>
          <span style="font-weight:500">${ch.title}</span>
          ${link ? `<a href="${link}" target="_blank" style="color:var(--muted);font-size:12px">@${ch.username}</a>` : ''}
          <span style="color:var(--muted);font-size:11px;margin-left:4px">[${accName}]</span>
          <span style="margin-left:auto;color:var(--muted);font-size:11px;font-family:monospace">пост #${ch.last_msg_id}</span>
          <button class="comment-ch-del" onclick="deleteCommentChannel(${ch.id})" title="Видалити">✕</button>
        </div>`;
    }).join('');
  } catch {
    list.innerHTML = '<div style="color:var(--danger);font-size:13px">Помилка завантаження</div>';
  }
}

async function toggleCommentChannel(id) {
  await fetch(`/api/comment/channels/${id}`, { method: 'PATCH' });
  await loadCommentChannels();
}

async function deleteCommentChannel(id) {
  await fetch(`/api/comment/channels/${id}`, { method: 'DELETE' });
  await loadCommentChannels();
}

function switchCommentAddTab(tab) {
  document.getElementById('commentAddPaneText').style.display = tab === 'text' ? 'block' : 'none';
  document.getElementById('commentAddPaneMy').style.display = tab === 'my' ? 'block' : 'none';
  document.getElementById('commentAddTabText').classList.toggle('active', tab === 'text');
  document.getElementById('commentAddTabMy').classList.toggle('active', tab === 'my');
}

async function addCommentChannelsBulk() {
  const raw = document.getElementById('commentAddLinks').value;
  const links = raw.split('\n').map(l => l.trim()).filter(Boolean);
  const errEl = document.getElementById('commentAddErr');
  const progress = document.getElementById('commentAddProgress');
  const resultsEl = document.getElementById('commentAddResults');
  errEl.style.display = 'none';
  resultsEl.style.display = 'none';
  if (!links.length) { errEl.textContent = 'Вставте хоча б один канал'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('commentAddBtn');
  btn.disabled = true; btn.textContent = '...';
  progress.textContent = `Додаємо ${links.length} каналів...`;

  try {
    const res = await fetch('/api/comment/channels/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ links }),
    });
    const data = await res.json();
    if (data.detail) { errEl.textContent = data.detail; errEl.style.display = 'block'; }
    else {
      const ok = data.results.filter(r => r.ok).length;
      const fail = data.results.length - ok;
      progress.textContent = `✓ ${ok} додано${fail ? `, ✕ ${fail} помилок` : ''}`;
      resultsEl.style.display = 'block';
      resultsEl.innerHTML = data.results.map(r =>
        r.ok
          ? `<span style="color:#00e676">✓ ${r.title}</span>`
          : `<span style="color:#ff5252">✕ ${r.link}: ${r.error}</span>`
      ).join('<br>');
      document.getElementById('commentAddLinks').value = '';
      await loadCommentChannels();
    }
  } catch {
    errEl.textContent = 'Помилка з\'єднання'; errEl.style.display = 'block';
  }
  btn.disabled = false; btn.textContent = '+ Додати';
}

async function loadMyChannelsForComment() {
  const list = document.getElementById('commentMyChannelList');
  list.innerHTML = '<div style="color:var(--muted);font-size:13px">Завантаження...</div>';
  try {
    const res = await fetch('/api/bulk/channels');
    const channels = await res.json();
    if (!channels.length) { list.innerHTML = '<div style="color:var(--muted);font-size:13px">Немає каналів</div>'; return; }
    // group by account
    const byAcc = {};
    channels.forEach(ch => { (byAcc[ch.account_id] = byAcc[ch.account_id] || []).push(ch); });
    list.innerHTML = Object.entries(byAcc).map(([aid, chs]) => {
      const acc = allAccounts.find(a => a.id == aid);
      const accName = acc ? (acc.first_name || acc.username || `#${aid}`) : `#${aid}`;
      return `<div style="color:var(--muted);font-size:11px;margin-top:4px;padding:2px 0">${accName}</div>` +
        chs.map(ch => `
          <label class="bulk-acc-row" style="padding:4px 6px">
            <input type="checkbox" class="comment-my-ch-check" value="${ch.username || ch.channel_id}" style="width:14px;height:14px;flex-shrink:0">
            <span style="font-size:13px">${ch.title}</span>
            <span style="color:var(--muted);font-size:11px;margin-left:auto">${ch.link ? '@' + ch.username : ''}</span>
          </label>`).join('');
    }).join('');
  } catch {
    list.innerHTML = '<div style="color:var(--danger);font-size:13px">Помилка</div>';
  }
}

function commentMySelectAll(v) {
  document.querySelectorAll('.comment-my-ch-check').forEach(c => c.checked = v);
}

async function addSelectedMyChannels() {
  const links = [...document.querySelectorAll('.comment-my-ch-check:checked')].map(el => el.value);
  const errEl = document.getElementById('commentAddErr');
  const progress = document.getElementById('commentAddProgress');
  const resultsEl = document.getElementById('commentAddResults');
  errEl.style.display = 'none';
  resultsEl.style.display = 'none';
  if (!links.length) { errEl.textContent = 'Вибери хоча б один канал'; errEl.style.display = 'block'; return; }

  const btn = document.getElementById('commentAddMyBtn');
  btn.disabled = true; btn.textContent = '...';
  progress.textContent = `Додаємо ${links.length} каналів...`;

  try {
    const res = await fetch('/api/comment/channels/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ links }),
    });
    const data = await res.json();
    const ok = data.results.filter(r => r.ok).length;
    const fail = data.results.length - ok;
    progress.textContent = `✓ ${ok} додано${fail ? `, ✕ ${fail} помилок` : ''}`;
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = data.results.map(r =>
      r.ok
        ? `<span style="color:#00e676">✓ ${r.title}</span>`
        : `<span style="color:#ff5252">✕ ${r.link}: ${r.error}</span>`
    ).join('<br>');
    await loadCommentChannels();
  } catch {
    errEl.textContent = 'Помилка з\'єднання'; errEl.style.display = 'block';
  }
  btn.disabled = false; btn.textContent = '+ Додати вибрані';
}

function appendCommentLog(entry, scroll = true) {
  const term = document.getElementById('commentTerminal');
  if (!term) return;
  const div = document.createElement('div');
  div.className = `log-${entry.level || 'info'}`;
  const escaped = (entry.msg || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
  if (entry.link) {
    div.innerHTML = escaped + ` <a href="${entry.link}" target="_blank" style="color:#80d8ff;font-size:11px;text-decoration:underline">→ відкрити</a>`;
  } else {
    div.innerHTML = escaped;
  }
  term.appendChild(div);
  if (scroll) term.scrollTop = term.scrollHeight;
}

function updateCommentStats(s) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val ?? 0; };
  set('cComments', s.total_comments);
  set('cSkipped', s.total_skipped);
  set('cErrors', s.total_errors);
  const lbl = document.getElementById('cRunningLabel');
  if (lbl) {
    lbl.textContent = s.running ? '● АКТИВНИЙ' : (s.total_comments > 0 ? '■ ЗУПИНЕНО' : '');
    lbl.style.color = s.running ? '#00e676' : '#888';
  }
}

async function startComment() {
  const btn = document.getElementById('commentStartBtn');
  btn.disabled = true; btn.textContent = 'Запуск...';
  try {
    const res = await fetch('/api/comment/start', { method: 'POST' });
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); btn.disabled = false; btn.textContent = '▶ Запустити'; return; }
    document.getElementById('commentTerminal').innerHTML = '';
    btn.style.display = 'none';
    document.getElementById('commentStopBtn').style.display = 'inline-flex';
  } catch {
    showToast('Помилка з\'єднання', 'error');
    btn.disabled = false; btn.textContent = '▶ Запустити';
  }
}

async function stopComment() {
  await fetch('/api/comment/stop', { method: 'POST' });
  document.getElementById('commentStopBtn').style.display = 'none';
  document.getElementById('commentStartBtn').style.display = 'inline-flex';
  document.getElementById('commentStartBtn').disabled = false;
  document.getElementById('commentStartBtn').textContent = '▶ Запустити';
}

// ===== REACT MODAL =====
let _reactSelectedAccounts = new Set();

async function openReactModal() {
  _closeAllToolModals();
  document.getElementById('reactModal').style.display = 'flex';
  await Promise.all([loadReactChannels(), loadReactStatus(), loadReactAccounts()]);
}

function closeReactModal() {
  document.getElementById('reactModal').style.display = 'none';
}

async function loadReactChannels() {
  const el = document.getElementById('reactChannelList');
  el.innerHTML = '<div style="color:var(--muted);font-size:13px">Завантаження...</div>';
  try {
    const res = await fetch('/api/react/channels');
    const channels = await res.json();
    if (!channels.length) {
      el.innerHTML = '<div style="color:var(--muted);font-size:13px">Немає каналів. Додай вище.</div>';
      return;
    }
    el.innerHTML = channels.map(ch => `
      <div class="comment-ch-row">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;flex:1;min-width:0">
          <input type="checkbox" class="comment-ch-toggle" ${ch.enabled ? 'checked' : ''}
            onchange="toggleReactChannel(${ch.id}, this.checked)">
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${ch.title}">${ch.title}</span>
          <span style="font-size:16px;cursor:pointer;padding:0 4px" title="Клік щоб змінити" onclick="changeReactEmoji(${ch.id}, this)">${ch.reaction}</span>
        </label>
        <button class="comment-ch-del" onclick="deleteReactChannel(${ch.id})">✕</button>
      </div>
    `).join('');
  } catch {
    el.innerHTML = '<div style="color:var(--danger);font-size:13px">Помилка</div>';
  }
}

async function loadReactAccounts() {
  const el = document.getElementById('reactAccountList');
  const accounts = allAccounts.filter(a => a.is_connected);
  if (!accounts.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px">Немає підключених акаунтів</div>';
    return;
  }
  el.innerHTML = accounts.map(a => {
    const label = a.first_name || a.username || a.phone || `#${a.id}`;
    const checked = _reactSelectedAccounts.has(a.id) ? 'checked' : '';
    return `<label style="display:flex;align-items:center;gap:4px;font-size:12px;cursor:pointer;padding:3px 6px;background:var(--bg3);border-radius:6px">
      <input type="checkbox" ${checked} onchange="toggleReactAccount(${a.id}, this.checked)"> ${label}
    </label>`;
  }).join('');
}

function toggleReactAccount(id, checked) {
  if (checked) _reactSelectedAccounts.add(id);
  else _reactSelectedAccounts.delete(id);
}

async function loadReactStatus() {
  try {
    const res = await fetch('/api/react/status');
    const data = await res.json();
    updateReactStats(data);
    appendReactLog({ level: null });
    data.log.forEach(e => appendReactLog(e));
    if (data.running) {
      document.getElementById('reactStartBtn').style.display = 'none';
      document.getElementById('reactStopBtn').style.display = 'inline-flex';
    }
  } catch {}
}

async function toggleReactChannel(id, enabled) {
  await fetch(`/api/react/channels/${id}`, { method: 'PATCH' });
}

async function deleteReactChannel(id) {
  await fetch(`/api/react/channels/${id}`, { method: 'DELETE' });
  await loadReactChannels();
}

async function changeReactEmoji(id, span) {
  const emoji = prompt('Введи емодзі для реакції:', span.textContent.trim());
  if (!emoji) return;
  const res = await fetch(`/api/react/channels/${id}/reaction?reaction=${encodeURIComponent(emoji)}`, { method: 'PATCH' });
  if (res.ok) { span.textContent = emoji; showToast('Реакцію оновлено', 'success'); }
  else showToast('Помилка', 'error');
}

async function addReactChannels() {
  const raw = document.getElementById('reactAddLinks').value.trim();
  if (!raw) return;
  const links = raw.split('\n').map(l => l.trim()).filter(Boolean);
  const btn = document.getElementById('reactAddBtn');
  const prog = document.getElementById('reactAddProgress');
  const err = document.getElementById('reactAddErr');
  const results = document.getElementById('reactAddResults');
  btn.disabled = true;
  err.style.display = 'none';
  results.style.display = 'none';
  prog.textContent = 'Додавання...';
  try {
    const res = await fetch('/api/react/channels/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ links }),
    });
    const data = await res.json();
    if (!res.ok) {
      err.textContent = data.detail || `Помилка ${res.status}`;
      err.style.display = 'block';
      prog.textContent = '';
      btn.disabled = false;
      return;
    }
    const lines = (data.results || []).map(r =>
      r.ok ? `<span style="color:var(--success)">✓ ${r.title || r.link}</span>`
            : `<span style="color:var(--danger)">✕ ${r.link}: ${r.error}</span>`
    );
    results.innerHTML = lines.join('<br>') || '<span style="color:var(--muted)">Немає результатів</span>';
    results.style.display = 'block';
    prog.textContent = '';
    document.getElementById('reactAddLinks').value = '';
    await loadReactChannels();
  } catch (e) {
    err.textContent = 'Помилка з\'єднання: ' + e.message;
    err.style.display = 'block';
    prog.textContent = '';
  }
  btn.disabled = false;
}

function appendReactLog(entry) {
  if (!entry.msg) return;
  const el = document.getElementById('reactTerminal');
  if (!el) return;
  const placeholder = el.querySelector('.log-info');
  if (placeholder) placeholder.remove();
  const div = document.createElement('div');
  div.className = entry.level === 'ok' ? 'log-ok' : entry.level === 'err' ? 'log-err' : entry.level === 'warn' ? 'log-warn' : 'log-info';
  div.textContent = entry.msg;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function updateReactStats(s) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val ?? 0; };
  set('rReacted', s.total_reacted);
  set('rViewed', s.total_viewed);
  set('rErrors', s.total_errors);
  const lbl = document.getElementById('rRunningLabel');
  if (lbl) {
    lbl.textContent = s.running ? '● АКТИВНИЙ' : (s.total_reacted > 0 ? '■ ЗУПИНЕНО' : '');
    lbl.style.color = s.running ? '#00e676' : '#888';
  }
}

async function doLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  window.location.href = '/login';
}

function openImportModal() { document.getElementById('importModal').style.display = 'flex'; }
function closeImportModal() { document.getElementById('importModal').style.display = 'none'; }

async function importDbSelected() {
  const file = document.getElementById('importDbFile').files[0];
  const status = document.getElementById('importDbStatus');
  if (!file) return;
  status.textContent = 'Завантаження...';
  status.style.color = 'var(--muted)';
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/admin/import-db', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) { status.textContent = data.detail || 'Помилка'; status.style.color = 'var(--danger)'; return; }
    status.innerHTML = `<span style="color:var(--success)">✓ Імпортовано: акаунтів ${data.accounts}, каналів реакцій ${data.react_channels}, каналів коментарів ${data.monitored_channels}</span>`;
    setTimeout(() => { closeImportModal(); loadAccounts(); }, 2000);
  } catch {
    status.textContent = 'Помилка з\'єднання'; status.style.color = 'var(--danger)';
  }
}

async function startReact() {
  const btn = document.getElementById('reactStartBtn');
  btn.disabled = true; btn.textContent = 'Запуск...';
  try {
    const account_ids = Array.from(_reactSelectedAccounts);
    const catchup = document.getElementById('reactCatchup')?.checked || false;
    const res = await fetch('/api/react/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids, catchup }),
    });
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); btn.disabled = false; btn.textContent = '▶ Запустити'; return; }
    document.getElementById('reactTerminal').innerHTML = '';
    btn.style.display = 'none';
    document.getElementById('reactStopBtn').style.display = 'inline-flex';
  } catch {
    showToast('Помилка з\'єднання', 'error');
    btn.disabled = false; btn.textContent = '▶ Запустити';
  }
}

async function stopReact() {
  await fetch('/api/react/stop', { method: 'POST' });
  document.getElementById('reactStopBtn').style.display = 'none';
  document.getElementById('reactStartBtn').style.display = 'inline-flex';
  document.getElementById('reactStartBtn').disabled = false;
  document.getElementById('reactStartBtn').textContent = '▶ Запустити';
}

// ===== COMMENT REACT =====
let _commentReactSelectedAccounts = new Set();

async function openCommentReactModal() {
  _closeAllToolModals();
  document.getElementById('commentReactModal').style.display = 'flex';
  await Promise.all([loadCommentReactChannels(), loadCommentReactAccounts()]);
  fetch('/api/comment-react/status').then(r => r.json()).then(s => {
    updateCommentReactStats(s);
    if (s.log && s.log.length) {
      const term = document.getElementById('commentReactTerminal');
      term.innerHTML = '';
      s.log.forEach(e => appendCommentReactLog(e));
      term.scrollTop = term.scrollHeight;
    }
    if (s.running) {
      document.getElementById('commentReactStartBtn').style.display = 'none';
      document.getElementById('commentReactStopBtn').style.display = 'inline-flex';
    }
  }).catch(() => {});
}

function closeCommentReactModal() {
  document.getElementById('commentReactModal').style.display = 'none';
}

async function loadCommentReactChannels() {
  const el = document.getElementById('commentReactChannelList');
  el.innerHTML = '<div style="color:var(--muted);font-size:13px">Завантаження...</div>';
  try {
    const res = await fetch('/api/comment-react/channels');
    const channels = await res.json();
    if (!channels.length) {
      el.innerHTML = '<div style="color:var(--muted);font-size:13px">Немає каналів. Додай вище.</div>';
      return;
    }
    el.innerHTML = channels.map(ch => {
      const disc = ch.has_discussion
        ? '<span style="color:var(--success);font-size:10px">💬 коментарі</span>'
        : '<span style="color:var(--danger);font-size:10px">⚠ без обговорень</span>';
      return `
        <div class="comment-ch-row">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;flex:1;min-width:0">
            <input type="checkbox" class="comment-ch-toggle" ${ch.enabled ? 'checked' : ''}
              onchange="toggleCommentReactChannel(${ch.id}, this.checked)">
            <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${ch.title}">${ch.title}</span>
            <span style="font-size:16px;cursor:pointer;padding:0 4px" onclick="changeCommentReactEmoji(${ch.id}, this)">${ch.reaction}</span>
            ${disc}
          </label>
          <button class="comment-ch-del" onclick="deleteCommentReactChannel(${ch.id})">✕</button>
        </div>`;
    }).join('');
  } catch {
    el.innerHTML = '<div style="color:var(--danger);font-size:13px">Помилка</div>';
  }
}

async function loadCommentReactAccounts() {
  const el = document.getElementById('commentReactAccountList');
  const accounts = allAccounts.filter(a => a.is_connected);
  if (!accounts.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px">Немає підключених акаунтів</div>';
    return;
  }
  el.innerHTML = accounts.map(a => {
    const label = a.first_name || a.username || a.phone || `#${a.id}`;
    const checked = _commentReactSelectedAccounts.has(a.id) ? 'checked' : '';
    return `<label style="display:flex;align-items:center;gap:4px;font-size:12px;cursor:pointer;padding:3px 6px;background:var(--bg3);border-radius:6px">
      <input type="checkbox" ${checked} onchange="toggleCommentReactAccount(${a.id}, this.checked)"> ${label}
    </label>`;
  }).join('');
}

function toggleCommentReactAccount(id, checked) {
  if (checked) _commentReactSelectedAccounts.add(id);
  else _commentReactSelectedAccounts.delete(id);
}

async function toggleCommentReactChannel(id) {
  await fetch(`/api/comment-react/channels/${id}`, { method: 'PATCH' });
  await loadCommentReactChannels();
}

async function deleteCommentReactChannel(id) {
  await fetch(`/api/comment-react/channels/${id}`, { method: 'DELETE' });
  await loadCommentReactChannels();
}

async function changeCommentReactEmoji(id, span) {
  const emoji = prompt('Введи емодзі для реакції:', span.textContent.trim());
  if (!emoji) return;
  const res = await fetch(`/api/comment-react/channels/${id}/reaction?reaction=${encodeURIComponent(emoji)}`, { method: 'PATCH' });
  if (res.ok) { span.textContent = emoji; showToast('Реакцію оновлено', 'success'); }
  else showToast('Помилка', 'error');
}

async function addCommentReactChannels() {
  const raw = document.getElementById('commentReactAddLinks').value.trim();
  if (!raw) return;
  const links = raw.split('\n').map(l => l.trim()).filter(Boolean);
  const btn = document.getElementById('commentReactAddBtn');
  const prog = document.getElementById('commentReactAddProgress');
  const err = document.getElementById('commentReactAddErr');
  const results = document.getElementById('commentReactAddResults');
  btn.disabled = true;
  err.style.display = 'none';
  results.style.display = 'none';
  prog.textContent = 'Додавання...';
  try {
    const res = await fetch('/api/comment-react/channels/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ links }),
    });
    const data = await res.json();
    if (!res.ok) {
      err.textContent = data.detail || `Помилка ${res.status}`;
      err.style.display = 'block';
      prog.textContent = '';
    } else {
      const ok = (data.results || []).filter(r => r.ok).length;
      const fail = (data.results || []).length - ok;
      prog.textContent = `✓ ${ok} додано${fail ? `, ✕ ${fail} помилок` : ''}`;
      results.style.display = 'block';
      results.innerHTML = (data.results || []).map(r =>
        r.ok
          ? `<span style="color:var(--success)">✓ ${r.title}${r.has_discussion ? ' 💬' : ' ⚠ без обговорень'}</span>`
          : `<span style="color:var(--danger)">✕ ${r.link}: ${r.error}</span>`
      ).join('<br>');
      document.getElementById('commentReactAddLinks').value = '';
      await loadCommentReactChannels();
    }
  } catch (e) {
    err.textContent = 'Помилка: ' + e.message;
    err.style.display = 'block';
    prog.textContent = '';
  }
  btn.disabled = false;
}

function appendCommentReactLog(entry) {
  if (!entry.msg) return;
  const el = document.getElementById('commentReactTerminal');
  if (!el) return;
  const placeholder = el.querySelector('.log-info');
  if (placeholder) placeholder.remove();
  const div = document.createElement('div');
  div.className = entry.level === 'ok' ? 'log-ok' : entry.level === 'err' ? 'log-err' : entry.level === 'warn' ? 'log-warn' : 'log-info';
  div.textContent = entry.msg;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function updateCommentReactStats(s) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val ?? 0; };
  set('crReacted', s.total_reacted);
  set('crErrors', s.total_errors);
  const lbl = document.getElementById('crRunningLabel');
  if (lbl) {
    lbl.textContent = s.running ? '● АКТИВНИЙ' : (s.total_reacted > 0 ? '■ ЗУПИНЕНО' : '');
    lbl.style.color = s.running ? '#00e676' : '#888';
  }
}

async function startCommentReact() {
  const btn = document.getElementById('commentReactStartBtn');
  btn.disabled = true; btn.textContent = 'Запуск...';
  try {
    const account_ids = Array.from(_commentReactSelectedAccounts);
    const res = await fetch('/api/comment-react/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids }),
    });
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); btn.disabled = false; btn.textContent = '▶ Запустити'; return; }
    document.getElementById('commentReactTerminal').innerHTML = '';
    btn.style.display = 'none';
    document.getElementById('commentReactStopBtn').style.display = 'inline-flex';
  } catch {
    showToast('Помилка з\'єднання', 'error');
    btn.disabled = false; btn.textContent = '▶ Запустити';
  }
}

async function stopCommentReact() {
  await fetch('/api/comment-react/stop', { method: 'POST' });
  document.getElementById('commentReactStopBtn').style.display = 'none';
  document.getElementById('commentReactStartBtn').style.display = 'inline-flex';
  document.getElementById('commentReactStartBtn').disabled = false;
  document.getElementById('commentReactStartBtn').textContent = '▶ Запустити';
}

// ===== MY CHANNELS =====
let _myChSelected = null;
let _myChStats = null;
let _myChSubStats = null;
let _myChMainTab = 'content';
let _chStatsLoading = false;
let _currentChartMetric = 'grouped';
let _currentSortBy = 'date';
let _currentSortDir = 'desc';

function openMyChannelsModal() {
  _closeAllToolModals();
  document.getElementById('myChannelsModal').style.display = 'flex';
  loadMyChannelsList();
}

function closeMyChannelsModal() {
  document.getElementById('myChannelsModal').style.display = 'none';
}

async function loadMyChannelsList() {
  const list = document.getElementById('myChannelsList');
  list.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px">Завантаження...</div>';
  try {
    const res = await fetch('/api/mychannels');
    const channels = await res.json();
    if (!channels.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px">Немає каналів де ти адмін</div>';
      return;
    }
    const byAcc = {};
    channels.forEach(ch => {
      const key = ch.account_name || `#${ch.account_id}`;
      (byAcc[key] = byAcc[key] || []).push(ch);
    });
    list.innerHTML = Object.entries(byAcc).map(([accName, chs]) => `
      <div style="font-size:11px;color:var(--muted);padding:4px 6px 2px;font-weight:600;text-transform:uppercase;letter-spacing:.4px">${accName}</div>
      ${chs.map(ch => {
        const members = ch.members_count ? `${_fmtNum(ch.members_count)} підп.` : '';
        const isActive = _myChSelected && _myChSelected.channel_id === ch.channel_id && _myChSelected.account_id === ch.account_id;
        const safeUn = ch.username ? `'${ch.username}'` : 'null';
        return `<div class="mych-ch-item${isActive ? ' active' : ''}" onclick="selectMyChannel(${ch.channel_id}, ${ch.account_id}, '${ch.title.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}', ${safeUn})">
          <div class="mych-ch-title">${ch.title}</div>
          <div class="mych-ch-meta">${ch.username ? '@' + ch.username : '🔒 приватний'}${members ? ' · ' + members : ''}</div>
        </div>`;
      }).join('')}
    `).join('');
  } catch (e) {
    list.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:8px">Помилка: ${e.message}</div>`;
  }
}

function _fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n;
}

let _chPeriodOffset = 0;
let _chPeriodBaseData = {}; // cache offset=0 data per period for comparison

const _UA_MONTHS_NOM_JS = ['','Січень','Лютий','Березень','Квітень','Травень','Червень',
  'Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'];

function _chPeriodLabel(period, offset) {
  const kyivNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'Europe/Kyiv' }));
  const today = new Date(kyivNow); today.setHours(0, 0, 0, 0);
  const fmt = d => `${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}`;

  if (period === 'day') {
    const t = new Date(today); t.setDate(t.getDate() + offset);
    if (offset === 0) return 'Сьогодні';
    if (offset === -1) return 'Вчора';
    return fmt(t) + '.' + t.getFullYear();
  }
  if (period === 'week') {
    const mon = new Date(today);
    mon.setDate(mon.getDate() - ((mon.getDay() + 6) % 7) + offset * 7);
    const sun = new Date(mon); sun.setDate(sun.getDate() + 6);
    if (offset === 0) return 'Цей тиждень';
    if (offset === -1) return 'Минулий тиждень';
    return `${fmt(mon)} – ${fmt(sun)}`;
  }
  if (period === 'month') {
    const t = new Date(today.getFullYear(), today.getMonth() + offset, 1);
    if (offset === 0) return 'Цей місяць';
    return _UA_MONTHS_NOM_JS[t.getMonth() + 1] + ' ' + t.getFullYear();
  }
  if (period === 'year') return String(today.getFullYear() + offset);
  if (period === 'weekend') {
    const dow = today.getDay(); // 0=Sun,6=Sat
    const daysToSat = (dow === 0 ? 6 : dow === 6 ? 0 : 6 - dow + (7 - dow));
    // days since last Saturday
    const daysSinceSat = dow === 6 ? 0 : (dow === 0 ? 1 : dow + 1);
    const sat = new Date(today); sat.setDate(sat.getDate() - daysSinceSat + offset * 7);
    const sun = new Date(sat); sun.setDate(sun.getDate() + 1);
    if (offset === 0) return `Цей вихідний (${fmt(sat)}–${fmt(sun)})`;
    if (offset === -1) return `Мин. вихідний (${fmt(sat)}–${fmt(sun)})`;
    return `${fmt(sat)}–${fmt(sun)}`;
  }
  return '';
}

function _canNavNext(period, offset) {
  return offset < 0; // can't go further than current
}

function selectMyChannel(channel_id, account_id, title, username) {
  _myChSelected = { channel_id, account_id, title, username: username || null };
  document.querySelectorAll('.mych-ch-item').forEach(el => el.classList.remove('active'));
  event.currentTarget.classList.add('active');
  _chPeriodOffset = 0;
  _chPeriodBaseData = {};
  _loadChStats({ channel_id, account_id, title, username: username || null }, 'week', false, 0);
}

async function _loadChStats({ channel_id, account_id, title, username }, period, background = false, offset = _chPeriodOffset) {
  if (background && _chStatsLoading) return;
  _chStatsLoading = true;
  period = period || 'week';
  _myChSelected = { channel_id, account_id, title, username: username || null };
  const right = document.getElementById('myChannelsRight');
  if (!background) {
    right.innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center;padding:30px">⏳ Завантаження статистики...</div>';
  }
  try {
    const [contentRes, subRes] = await Promise.all([
      fetch(`/api/mychannels/stats?account_id=${account_id}&channel_id=${channel_id}&period=${period}&offset=${offset}`),
      fetch(`/api/mychannels/subscriber-stats?account_id=${account_id}&channel_id=${channel_id}&period=${period}`),
    ]);
    if (!contentRes.ok) {
      const err = await contentRes.json().catch(() => ({}));
      if (!background) right.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:16px">Помилка: ${err.detail || contentRes.status}</div>`;
      return;
    }
    const data = await contentRes.json();
    const subData = subRes.ok ? await subRes.json().catch(() => null) : null;
    // Cache the current (offset=0) data for comparison
    if (offset === 0) _chPeriodBaseData[period] = data;
    _myChStats = { data, account_id, channel_id, title, username: username || null, period, offset };
    _myChSubStats = subData;
    if (background) {
      _updateChStatsInPlace(data);
      _updateSubStatsInPlace(subData);
    } else {
      _currentChartMetric = 'grouped';
      _currentSortBy = 'date';
      _currentSortDir = 'desc';
      _renderChStats(right, title, data, subData, account_id, channel_id, period, offset);
    }
  } catch (e) {
    if (!background) right.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:16px">Помилка: ${e.message}</div>`;
  } finally {
    _chStatsLoading = false;
  }
}

function _updateChStatsInPlace(data) {
  const ts = document.getElementById('chLastUpdate');
  if (ts) ts.textContent = new Date().toLocaleTimeString('uk-UA', { timeZone: 'Europe/Kyiv' });
  const avgViews = data.total_posts ? Math.round(data.total_views / data.total_posts) : 0;
  const erRate = data.total_views ? ((data.total_reactions / data.total_views) * 100).toFixed(2) : '0.00';
  const cards = document.getElementById('chSummaryCards');
  if (cards) cards.innerHTML = `
    <div class="ch-card"><div class="ch-card-icon">👁</div><div class="ch-card-val">${_fmtNum(data.total_views)}</div><div class="ch-card-lbl">Перегляди</div></div>
    <div class="ch-card"><div class="ch-card-icon">📊</div><div class="ch-card-val">${_fmtNum(avgViews)}</div><div class="ch-card-lbl">Сер. охоп.</div></div>
    <div class="ch-card"><div class="ch-card-icon">❤️</div><div class="ch-card-val">${_fmtNum(data.total_reactions)}</div><div class="ch-card-lbl">Реакції</div></div>
    <div class="ch-card"><div class="ch-card-icon">📤</div><div class="ch-card-val">${_fmtNum(data.total_forwards)}</div><div class="ch-card-lbl">Репости</div></div>
    <div class="ch-card"><div class="ch-card-icon">📝</div><div class="ch-card-val">${data.total_posts}</div><div class="ch-card-lbl">Публікацій</div></div>
    <div class="ch-card"><div class="ch-card-icon">📈</div><div class="ch-card-val">${erRate}%</div><div class="ch-card-lbl">ER</div></div>`;
  const chartEl = document.getElementById('chBarChart');
  if (chartEl) chartEl.innerHTML = _renderBarChart(data.chart, _currentChartMetric);
  const topEl = document.getElementById('chTopList');
  if (topEl) topEl.innerHTML = _renderTopPosts(data.posts, _currentSortBy, _currentSortDir);
}

function _updateSubStatsInPlace(subData) {
  if (!subData) return;
  const el = document.getElementById('chSubStatsBody');
  if (el) el.innerHTML = _renderSubStatsBody(subData, _myChStats ? _myChStats.period : 'week');
}

function _renderChStats(container, title, data, subData, account_id, channel_id, period, offset = 0) {
  const periods = [
    { key: 'day', label: 'День' },
    { key: 'week', label: 'Тиждень' },
    { key: 'month', label: 'Місяць' },
    { key: 'weekend', label: '🏖 Вихідні' },
    { key: 'year', label: 'Рік' },
    { key: 'all', label: 'Весь час' },
  ];
  const hasNav = ['day', 'week', 'month', 'weekend'].includes(period);
  const periodTabs = periods.map(p =>
    `<button class="ch-period-btn${p.key === period ? ' active' : ''}" onclick="_switchChPeriod('${p.key}')">${p.label}</button>`
  ).join('');

  const navLabel = hasNav ? _chPeriodLabel(period, offset) : '';
  const navRow = hasNav ? `
    <div class="ch-nav-row">
      <button class="ch-nav-btn" onclick="_chNavPrev()">←</button>
      <span class="ch-nav-label">${navLabel}</span>
      <button class="ch-nav-btn${offset >= 0 ? ' disabled' : ''}" onclick="_chNavNext()" ${offset >= 0 ? 'disabled' : ''}>→</button>
    </div>` : '';

  const avgViews = data.total_posts ? Math.round(data.total_views / data.total_posts) : 0;
  const erRate = data.total_views ? ((data.total_reactions / data.total_views) * 100).toFixed(2) : '0.00';

  container.innerHTML = `
    <div class="ch-stats-wrap">
      <div class="ch-stats-header">
        <span class="ch-stats-title">${title}</span>
        <div style="display:flex;gap:6px;align-items:center">
          <span class="ch-live-dot"></span>
          <span id="chLastUpdate" style="font-size:10px;color:var(--muted)">${new Date().toLocaleTimeString('uk-UA', { timeZone: 'Europe/Kyiv' })}</span>
          <button class="btn-copy-sm" onclick="_switchChPeriod('${period}')">↻</button>
        </div>
      </div>

      <div class="ch-period-tabs">${periodTabs}</div>
      ${navRow}

      <div class="ch-main-tabs">
        <button class="ch-main-tab${_myChMainTab === 'content' ? ' active' : ''}" onclick="_switchMainTab('content')">📊 Контент</button>
        <button class="ch-main-tab${_myChMainTab === 'subs' ? ' active' : ''}" onclick="_switchMainTab('subs')">👥 Підписники</button>
      </div>

      <div id="chTabContent">
        ${_myChMainTab === 'content' ? _renderContentTab(data, avgViews, erRate, period, offset) : _renderSubStatsBody(subData, period)}
      </div>
    </div>`;
}

function _chDelta(val, base) {
  if (base == null || base === 0) return '';
  const pct = ((val - base) / base * 100).toFixed(1);
  const up = val >= base;
  return `<span class="ch-delta ${up ? 'up' : 'down'}">${up ? '▲' : '▼'}${Math.abs(pct)}%</span>`;
}

function _renderContentTab(data, avgViews, erRate, period, offset = 0) {
  const base = (offset !== 0) ? _chPeriodBaseData[period] : null;
  const baseAvg = base && base.total_posts ? Math.round(base.total_views / base.total_posts) : null;
  return `
    <div class="ch-summary-cards" id="chSummaryCards">
      <div class="ch-card"><div class="ch-card-icon">👁</div><div class="ch-card-val">${_fmtNum(data.total_views)}${base ? _chDelta(data.total_views, base.total_views) : ''}</div><div class="ch-card-lbl">Перегляди</div></div>
      <div class="ch-card"><div class="ch-card-icon">📊</div><div class="ch-card-val">${_fmtNum(avgViews)}${base ? _chDelta(avgViews, baseAvg) : ''}</div><div class="ch-card-lbl">Сер. охоп.</div></div>
      <div class="ch-card"><div class="ch-card-icon">❤️</div><div class="ch-card-val">${_fmtNum(data.total_reactions)}${base ? _chDelta(data.total_reactions, base.total_reactions) : ''}</div><div class="ch-card-lbl">Реакції</div></div>
      <div class="ch-card"><div class="ch-card-icon">📤</div><div class="ch-card-val">${_fmtNum(data.total_forwards)}${base ? _chDelta(data.total_forwards, base.total_forwards) : ''}</div><div class="ch-card-lbl">Репости</div></div>
      <div class="ch-card"><div class="ch-card-icon">📝</div><div class="ch-card-val">${data.total_posts}${base ? _chDelta(data.total_posts, base.total_posts) : ''}</div><div class="ch-card-lbl">Публікацій</div></div>
      <div class="ch-card"><div class="ch-card-icon">📈</div><div class="ch-card-val">${erRate}%</div><div class="ch-card-lbl">ER</div></div>
    </div>

    ${data.chart.length > 1 ? `
    <div class="ch-chart-section">
      <div class="ch-chart-header">
        <span class="ch-chart-title">Перегляди</span>
        <div class="ch-chart-switch">
          <button class="ch-chart-btn active" onclick="_switchChartMetric(this,'grouped')">📊 Всі</button>
          <button class="ch-chart-btn" onclick="_switchChartMetric(this,'views')">👁 Перегляди</button>
          <button class="ch-chart-btn" onclick="_switchChartMetric(this,'reactions')">❤️ Реакції</button>
          <button class="ch-chart-btn" onclick="_switchChartMetric(this,'forwards')">📤 Репости</button>
          <button class="ch-chart-btn" onclick="_switchChartMetric(this,'posts')">📝 Пости</button>
        </div>
      </div>
      <div id="chBarChart">${_renderBarChart(data.chart, 'grouped')}</div>
    </div>` : ''}

    ${data.posts.length > 0 ? `
    <div class="ch-top-section">
      <div class="ch-top-header">
        <span style="font-weight:600;font-size:13px">📋 Пости (${data.posts.length})</span>
        <div class="ch-sort-tabs">
          <button class="ch-sort-btn active" onclick="_sortChPosts(this,'date')">🕐 Дата <span class="sort-dir">↓</span></button>
          <button class="ch-sort-btn" onclick="_sortChPosts(this,'views')">👁 Перегляди <span class="sort-dir"></span></button>
          <button class="ch-sort-btn" onclick="_sortChPosts(this,'reactions_total')">❤️ Реакції <span class="sort-dir"></span></button>
          <button class="ch-sort-btn" onclick="_sortChPosts(this,'forwards')">📤 Репости <span class="sort-dir"></span></button>
          <button class="ch-sort-btn" onclick="_sortChPosts(this,'comments')">💬 Коментарі <span class="sort-dir"></span></button>
        </div>
      </div>
      <div id="chTopList">${_renderTopPosts(data.posts, 'date', 'desc')}</div>
    </div>` : '<div style="color:var(--muted);font-size:13px;text-align:center;padding:20px">Постів не знайдено за цей період</div>'}
  `;
}

function _switchMainTab(tab) {
  _myChMainTab = tab;
  const el = document.getElementById('chTabContent');
  if (!el || !_myChStats) return;
  const data = _myChStats.data;
  const period = _myChStats.period;
  document.querySelectorAll('.ch-main-tab').forEach(b => b.classList.toggle('active', b.textContent.includes(tab === 'content' ? 'Контент' : 'Підписники')));
  if (tab === 'content') {
    const avgViews = data.total_posts ? Math.round(data.total_views / data.total_posts) : 0;
    const erRate = data.total_views ? ((data.total_reactions / data.total_views) * 100).toFixed(2) : '0.00';
    el.innerHTML = _renderContentTab(data, avgViews, erRate, period);
  } else {
    el.innerHTML = _renderSubStatsBody(_myChSubStats, period);
  }
}

function _renderSubStatsBody(sub, period) {
  if (!sub) {
    return '<div style="color:var(--muted);font-size:13px;text-align:center;padding:30px">⏳ Завантаження...</div>';
  }

  const growSign = sub.period_growth > 0 ? '+' : '';
  const growColor = sub.period_growth > 0 ? 'var(--success)' : sub.period_growth < 0 ? 'var(--danger)' : 'var(--muted)';
  const pctStr = (sub.period_growth > 0 ? '+' : '') + sub.growth_pct + '%';

  const cards = `
    <div class="ch-summary-cards" id="chSubCards">
      <div class="ch-card">
        <div class="ch-card-icon">👥</div>
        <div class="ch-card-val">${sub.current_members != null ? _fmtNum(sub.current_members) : '—'}</div>
        <div class="ch-card-lbl">Підписників</div>
      </div>
      <div class="ch-card">
        <div class="ch-card-icon">📈</div>
        <div class="ch-card-val" style="color:${growColor}">${growSign}${_fmtNum(sub.period_growth)}</div>
        <div class="ch-card-lbl">Ріст (${pctStr})</div>
      </div>
      <div class="ch-card">
        <div class="ch-card-icon">✅</div>
        <div class="ch-card-val" style="color:var(--success)">+${_fmtNum(sub.period_joined)}</div>
        <div class="ch-card-lbl">Підписались</div>
      </div>
      <div class="ch-card">
        <div class="ch-card-icon">❌</div>
        <div class="ch-card-val" style="color:var(--danger)">-${_fmtNum(sub.period_left)}</div>
        <div class="ch-card-lbl">Відписались</div>
      </div>
    </div>`;

  const totalPoints = sub.total_history_points || 0;
  const archiveNote = !sub.tg_stats_ok ? (() => {
    if (totalPoints === 0) {
      return `<div class="ch-sub-note ch-sub-note-warn">
        📡 <strong>Збір архіву запущено.</strong> Система автоматично зберігає snapshot підписників кожні 30 хвилин.
        Перший графік з'явиться після накопичення хоча б 2 точок (~30 хв).
        ${sub.tg_error ? `<br><span style="font-size:10px;opacity:.7">Telegram Stats API: ${sub.tg_error}</span>` : ''}
      </div>`;
    }
    if (totalPoints < 5) {
      return `<div class="ch-sub-note">
        ⏳ Архів накопичується: <strong>${totalPoints} точок</strong>. Графіки стануть точнішими з часом (кожні 30 хв — нова точка).
        ${sub.tg_error ? `<br><span style="font-size:10px;opacity:.7">Telegram Stats API недоступний: ${sub.tg_error}</span>` : ''}
      </div>`;
    }
    return `<div class="ch-sub-note">
      💾 Локальний архів: <strong>${totalPoints} точок</strong> | Telegram Stats API недоступний.
      ${sub.tg_error ? `<br><span style="font-size:10px;opacity:.8;word-break:break-all">⚠️ ${sub.tg_error}</span>` : ''}
    </div>`;
  })() : '';

  // Growth line chart
  const lineSection = sub.growth_chart && sub.growth_chart.length > 1 ? `
    <div class="ch-chart-section">
      <div class="ch-chart-header">
        <span class="ch-chart-title">📈 Динаміка підписників</span>
      </div>
      ${_renderLineChart(sub.growth_chart)}
    </div>` : (totalPoints < 2 && !sub.tg_stats_ok ? '' : '');

  // Joined / Left bar chart
  const fol = sub.followers_chart || [];
  const folSection = fol.length > 1 ? `
    <div class="ch-chart-section">
      <div class="ch-chart-header">
        <span class="ch-chart-title">👥 Підписки та відписки</span>
      </div>
      ${_renderFollowersBarChart(fol)}
      <div class="ch-bar-legend" style="margin-top:8px">
        <span><span class="ch-leg-dot" style="background:var(--success)"></span>Підписались</span>
        <span><span class="ch-leg-dot" style="background:var(--danger)"></span>Відписались</span>
      </div>
    </div>` : '';

  // Sources
  const srcSection = sub.sources && sub.sources.length > 0 ? `
    <div class="ch-chart-section">
      <div class="ch-chart-header">
        <span class="ch-chart-title">🔍 Звідки підписники</span>
      </div>
      ${_renderSourceBars(sub.sources)}
    </div>` : (sub.tg_stats_ok ? '<div style="color:var(--muted);font-size:12px;text-align:center;padding:10px">Немає даних про джерела за цей період</div>' : '');

  return `<div id="chSubStatsBody">${cards}${archiveNote}${lineSection}${folSection}${srcSection}</div>`;
}

function _renderLineChart(chartData) {
  if (!chartData || chartData.length < 2) return '';
  const display = chartData.slice(-60);
  const vals = display.map(d => d.members);
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const range = maxV - minV || 1;
  const W = 100, H = 80;
  const pts = display.map((d, i) => {
    const x = (i / (display.length - 1)) * W;
    const y = H - ((d.members - minV) / range) * (H - 10) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const areaBottom = `${W},${H} 0,${H}`;
  const labelsStep = Math.max(1, Math.floor(display.length / 6));
  const labelHtml = display.map((d, i) => {
    if (i % labelsStep !== 0 && i !== display.length - 1) return '';
    const x = (i / (display.length - 1)) * 100;
    return `<div class="ch-line-lbl" style="left:${x.toFixed(1)}%">${d.label}</div>`;
  }).join('');
  const tooltipDots = display.map((d, i) => {
    const x = (i / (display.length - 1)) * 100;
    const y = H - ((d.members - minV) / range) * (H - 10) - 2;
    return `<circle class="ch-line-dot" cx="${x.toFixed(1)}%" cy="${((y / H) * 100).toFixed(1)}%" r="3" title="${d.label}: ${_fmtNum(d.members)} підп."/>`;
  }).join('');
  return `
    <div class="ch-line-wrap">
      <div class="ch-line-y-labels">
        <span>${_fmtNum(maxV)}</span>
        <span>${_fmtNum(Math.round((maxV + minV) / 2))}</span>
        <span>${_fmtNum(minV)}</span>
      </div>
      <div class="ch-line-chart-area">
        <svg viewBox="0 0 100 ${H}" preserveAspectRatio="none" class="ch-line-svg">
          <defs>
            <linearGradient id="lineGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.3"/>
              <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <polygon points="${pts} ${areaBottom}" fill="url(#lineGrad)"/>
          <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
          ${tooltipDots}
        </svg>
        <div class="ch-line-labels">${labelHtml}</div>
      </div>
    </div>`;
}

function _renderFollowersBarChart(fol) {
  if (!fol || !fol.length) return '';
  const display = fol.slice(-40);
  const maxVal = Math.max(...display.flatMap(d => [d.joined, d.left]), 1);
  return `<div class="ch-bar-chart ch-bar-grouped">${display.map(d => {
    const pJ = Math.max(Math.round((d.joined / maxVal) * 100), d.joined > 0 ? 2 : 0);
    const pL = Math.max(Math.round((d.left / maxVal) * 100), d.left > 0 ? 2 : 0);
    return `<div class="ch-bar-group-wrap">
      <div class="ch-bar-group">
        <div class="ch-bar" style="height:${pJ}%;background:var(--success)" title="✅ +${d.joined}"></div>
        <div class="ch-bar" style="height:${pL}%;background:var(--danger)" title="❌ -${d.left}"></div>
      </div>
      <div class="ch-bar-lbl">${d.label}</div>
    </div>`;
  }).join('')}</div>`;
}

function _renderSourceBars(sources) {
  if (!sources || !sources.length) return '';
  const total = sources.reduce((s, x) => s + x.count, 0) || 1;
  return `<div class="ch-source-list">${sources.map(s => {
    const pct = Math.round(s.count / total * 100);
    return `<div class="ch-source-row">
      <div class="ch-source-name">${s.source}</div>
      <div class="ch-source-bar-wrap">
        <div class="ch-source-bar" style="width:${pct}%"></div>
      </div>
      <div class="ch-source-val">${_fmtNum(s.count)} <span class="ch-source-pct">${pct}%</span></div>
    </div>`;
  }).join('')}</div>`;
}

function _renderBarChart(chartData, metric) {
  if (!chartData || !chartData.length) return '<div style="color:var(--muted);font-size:12px;padding:8px">Немає даних для графіка</div>';
  const display = chartData.slice(-40);

  if (metric === 'grouped') {
    const maxVal = Math.max(...display.flatMap(d => [d.views, d.reactions, d.forwards]), 1);
    return `<div class="ch-bar-chart ch-bar-grouped">${display.map(d => {
      const pV = Math.max(Math.round((d.views / maxVal) * 100), 1);
      const pR = Math.max(Math.round((d.reactions / maxVal) * 100), 1);
      const pF = Math.max(Math.round((d.forwards / maxVal) * 100), 1);
      return `<div class="ch-bar-group-wrap">
        <div class="ch-bar-group">
          <div class="ch-bar ch-bar-v" style="height:${pV}%" title="👁 ${_fmtNum(d.views)}"></div>
          <div class="ch-bar ch-bar-r" style="height:${pR}%" title="❤️ ${_fmtNum(d.reactions)}"></div>
          <div class="ch-bar ch-bar-f" style="height:${pF}%" title="📤 ${_fmtNum(d.forwards)}"></div>
        </div>
        <div class="ch-bar-lbl">${d.label}</div>
      </div>`;
    }).join('')}</div>
    <div class="ch-bar-legend">
      <span><span class="ch-leg-dot ch-leg-v"></span>Перегляди</span>
      <span><span class="ch-leg-dot ch-leg-r"></span>Реакції</span>
      <span><span class="ch-leg-dot ch-leg-f"></span>Репости</span>
    </div>`;
  }

  const values = display.map(d => d[metric]);
  const maxVal = Math.max(...values, 1);
  return `<div class="ch-bar-chart">${display.map(d => {
    const pct = Math.max(Math.round((d[metric] / maxVal) * 100), 1);
    return `<div class="ch-bar-wrap" data-tooltip="${d.label}: ${_fmtNum(d[metric])}"><div class="ch-bar" style="height:${pct}%"></div><div class="ch-bar-lbl">${d.label}</div></div>`;
  }).join('')}</div>`;
}

function _renderTopPosts(posts, sortBy, dir = 'desc') {
  if (!posts || !posts.length) return '<div style="color:var(--muted);font-size:12px;padding:8px">Немає постів</div>';
  let sorted;
  if (sortBy === 'date') {
    sorted = [...posts].sort((a, b) => dir === 'desc'
      ? new Date(b.date) - new Date(a.date)
      : new Date(a.date) - new Date(b.date));
  } else {
    sorted = [...posts].sort((a, b) => dir === 'desc' ? (b[sortBy] || 0) - (a[sortBy] || 0) : (a[sortBy] || 0) - (b[sortBy] || 0));
  }
  return sorted.map((p, i) => {
    const date = p.date ? new Date(p.date).toLocaleDateString('uk-UA', { day: '2-digit', month: '2-digit', timeZone: 'Europe/Kyiv' }) : '';
    const time = p.date ? new Date(p.date).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Kyiv' }) : '';
    const text = (p.text ? p.text.replace(/</g, '&lt;').replace(/>/g, '&gt;') : (p.has_media ? '📎 медіа' : '—'));
    const reactBadges = p.reactions.slice(0, 4).map(r => `<span class="ch-react-mini">${r.emoji} ${r.count}</span>`).join('');
    return `<div class="ch-top-post ch-top-post-click" onclick="_openPostDetailById(${p.id})">
      <span class="ch-top-num">${i + 1}</span>
      <div class="ch-top-content">
        <div class="ch-top-text">${text}</div>
        <div class="ch-top-meta">
          <span>👁 <strong>${_fmtNum(p.views)}</strong></span>
          ${reactBadges}
          <span>📤 <strong>${p.forwards}</strong></span>
          ${p.comments ? `<span>💬 <strong>${p.comments}</strong></span>` : ''}
          <span class="ch-top-date">${date} ${time}</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

function _switchChPeriod(period) {
  if (!_myChStats) return;
  _chPeriodOffset = 0;
  _loadChStats({
    channel_id: _myChStats.channel_id,
    account_id: _myChStats.account_id,
    title: _myChStats.title,
    username: _myChStats.username
  }, period, false, 0);
}

function _chNavPrev() {
  if (!_myChStats) return;
  _chPeriodOffset -= 1;
  _loadChStats({
    channel_id: _myChStats.channel_id,
    account_id: _myChStats.account_id,
    title: _myChStats.title,
    username: _myChStats.username
  }, _myChStats.period, false, _chPeriodOffset);
}

function _chNavNext() {
  if (!_myChStats || _chPeriodOffset >= 0) return;
  _chPeriodOffset += 1;
  _loadChStats({
    channel_id: _myChStats.channel_id,
    account_id: _myChStats.account_id,
    title: _myChStats.title,
    username: _myChStats.username
  }, _myChStats.period, false, _chPeriodOffset);
}

function _openPostDetailById(postId) {
  if (!_myChStats) return;
  const post = _myChStats.data.posts.find(p => p.id === postId);
  if (!post) return;
  const date = post.date ? new Date(post.date).toLocaleString('uk-UA', {
    timeZone: 'Europe/Kyiv', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit'
  }) : '';
  const text = (post.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
  const ch = _myChSelected;
  const postLink = ch && ch.username
    ? `<a href="https://t.me/${ch.username}/${post.id}" target="_blank" class="btn-copy-sm" style="text-decoration:none;display:inline-block">🔗 Відкрити в Telegram</a>`
    : '';
  const reactRows = post.reactions.map(r =>
    `<span class="ch-react-mini" style="font-size:14px;padding:4px 10px;background:var(--bg3);border-radius:8px">${r.emoji} ${r.count}</span>`
  ).join(' ');
  document.getElementById('postDetailTitle').textContent = `Пост #${post.id} · ${date}`;
  document.getElementById('postDetailBody').innerHTML = `
    <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
      <div class="ch-card" style="min-width:90px"><div class="ch-card-icon">👁</div><div class="ch-card-val">${_fmtNum(post.views)}</div><div class="ch-card-lbl">Перегляди</div></div>
      <div class="ch-card" style="min-width:90px"><div class="ch-card-icon">❤️</div><div class="ch-card-val">${_fmtNum(post.reactions_total)}</div><div class="ch-card-lbl">Реакції</div></div>
      <div class="ch-card" style="min-width:90px"><div class="ch-card-icon">📤</div><div class="ch-card-val">${post.forwards}</div><div class="ch-card-lbl">Репости</div></div>
      ${post.comments != null ? `<div class="ch-card" style="min-width:90px"><div class="ch-card-icon">💬</div><div class="ch-card-val">${post.comments}</div><div class="ch-card-lbl">Коментарі</div></div>` : ''}
    </div>
    ${reactRows ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">${reactRows}</div>` : ''}
    ${post.has_media ? '<div style="color:var(--muted);font-size:13px;margin-bottom:10px">📎 Є медіафайл</div>' : ''}
    ${text
      ? `<div style="background:var(--bg3);border-radius:10px;padding:14px;font-size:13px;line-height:1.6;max-height:50vh;overflow-y:auto">${text}</div>`
      : '<div style="color:var(--muted);font-size:13px;font-style:italic">— без тексту —</div>'}
    ${postLink ? `<div style="margin-top:14px">${postLink}</div>` : ''}
  `;
  document.getElementById('postDetailModal').style.display = 'flex';
}

function closePostDetailModal() {
  document.getElementById('postDetailModal').style.display = 'none';
}

function _switchChartMetric(btn, metric) {
  document.querySelectorAll('.ch-chart-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _currentChartMetric = metric;
  const labels = { grouped: 'Перегляди / Реакції / Репости', views: 'Перегляди', reactions: 'Реакції', forwards: 'Репости', posts: 'Публікації' };
  document.querySelector('.ch-chart-title').textContent = labels[metric] || '';
  document.getElementById('chBarChart').innerHTML = _renderBarChart(_myChStats.data.chart, metric);
}

function _sortChPosts(btn, sortBy) {
  if (_currentSortBy === sortBy) {
    _currentSortDir = _currentSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    _currentSortBy = sortBy;
    _currentSortDir = 'desc';
  }
  document.querySelectorAll('.ch-sort-btn').forEach(b => {
    b.classList.remove('active');
    const s = b.querySelector('.sort-dir');
    if (s) s.textContent = '';
  });
  btn.classList.add('active');
  const s = btn.querySelector('.sort-dir');
  if (s) s.textContent = _currentSortDir === 'desc' ? '↓' : '↑';
  document.getElementById('chTopList').innerHTML = _renderTopPosts(_myChStats.data.posts, _currentSortBy, _currentSortDir);
}

// ===== BROADCAST =====
let _bcPollTimer = null;

function openBroadcastModal() {
  _closeAllToolModals();
  document.getElementById('broadcastModal').style.display = 'flex';
  _populateBcAccounts();
  fetch('/api/broadcast/status').then(r => r.json()).then(d => {
    if (d.status === 'running' || d.status === 'paused') {
      document.getElementById('bcStartBtn').style.display = 'none';
      document.getElementById('bcPauseBtn').style.display = d.status === 'running' ? '' : 'none';
      document.getElementById('bcResumeBtn').style.display = d.status === 'paused' ? '' : 'none';
      document.getElementById('bcStopBtn').style.display = '';
      document.getElementById('bcProgressWrap').style.display = '';
      _pollBroadcastStatus();
    }
    _updateBcUI(d.status, d.total, d.sent, d.failed, d.log);
  }).catch(() => {});
}

function closeBroadcastModal() {
  document.getElementById('broadcastModal').style.display = 'none';
  if (_bcPollTimer) { clearInterval(_bcPollTimer); _bcPollTimer = null; }
}

function _populateBcAccounts() {
  const connected = allAccounts.filter(a => a.is_connected);
  const accHtml = connected.map(a =>
    `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
      <input type="checkbox" class="bc-acc-check" value="${a.id}" checked>
      ${a.first_name || a.username || '#' + a.id}
    </label>`
  ).join('') || '<div style="color:var(--muted);font-size:12px">Немає підключених акаунтів</div>';

  const bcList = document.getElementById('bcAccountsList');
  if (bcList) bcList.innerHTML = accHtml;

  const viewsAccList = document.getElementById('viewsAccountsList');
  if (viewsAccList) viewsAccList.innerHTML = connected.map(a =>
    `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
      <input type="checkbox" class="views-acc-check" value="${a.id}" checked>
      ${a.first_name || a.username || '#' + a.id}
    </label>`
  ).join('') || '<div style="color:var(--muted);font-size:12px">Немає підключених акаунтів</div>';

  const sel = document.getElementById('inboxAccountSel');
  if (sel) {
    sel.innerHTML = '<option value="">Виберіть акаунт</option>' +
      connected.map(a => `<option value="${a.id}">${a.first_name || a.username || '#' + a.id}</option>`).join('');
  }
}

function toggleBcAllAccounts(cb) {
  document.querySelectorAll('.bc-acc-check').forEach(c => c.checked = cb.checked);
}

function _getBcSelectedAccounts() {
  return [...document.querySelectorAll('.bc-acc-check:checked')].map(c => parseInt(c.value));
}

async function startBroadcast() {
  const contacts = document.getElementById('bcContacts').value.split('\n').filter(l => l.trim());
  const message = document.getElementById('bcMessage').value.trim();
  const account_ids = _getBcSelectedAccounts();
  const interval = parseInt(document.getElementById('bcInterval').value) || 5;
  const limitVal = document.getElementById('bcLimit').value;
  const limit_per_account = limitVal ? parseInt(limitVal) : null;
  if (!contacts.length) { alert('Список контактів порожній'); return; }
  if (!message) { alert('Введіть повідомлення'); return; }
  if (!account_ids.length) { alert('Виберіть хоча б один акаунт'); return; }
  try {
    const res = await fetch('/api/broadcast/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contacts, message, account_ids, interval, limit_per_account })
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); alert(e.detail || 'Помилка'); return; }
    document.getElementById('bcStartBtn').style.display = 'none';
    document.getElementById('bcPauseBtn').style.display = '';
    document.getElementById('bcStopBtn').style.display = '';
    document.getElementById('bcProgressWrap').style.display = '';
    _pollBroadcastStatus();
  } catch (e) { alert('Помилка: ' + e.message); }
}

async function pauseBroadcast() {
  await fetch('/api/broadcast/pause', { method: 'POST' });
  document.getElementById('bcPauseBtn').style.display = 'none';
  document.getElementById('bcResumeBtn').style.display = '';
}

async function resumeBroadcast() {
  await fetch('/api/broadcast/resume', { method: 'POST' });
  document.getElementById('bcResumeBtn').style.display = 'none';
  document.getElementById('bcPauseBtn').style.display = '';
}

async function stopBroadcast() {
  if (!confirm('Зупинити розсилку?')) return;
  await fetch('/api/broadcast/stop', { method: 'POST' });
  if (_bcPollTimer) { clearInterval(_bcPollTimer); _bcPollTimer = null; }
  document.getElementById('bcStartBtn').style.display = '';
  document.getElementById('bcPauseBtn').style.display = 'none';
  document.getElementById('bcResumeBtn').style.display = 'none';
  document.getElementById('bcStopBtn').style.display = 'none';
}

function _pollBroadcastStatus() {
  if (_bcPollTimer) clearInterval(_bcPollTimer);
  _bcPollTimer = setInterval(async () => {
    try {
      const d = await fetch('/api/broadcast/status').then(r => r.json());
      _updateBcUI(d.status, d.total, d.sent, d.failed, d.log);
      if (d.status === 'done' || d.status === 'stopped' || d.status === 'idle') {
        clearInterval(_bcPollTimer); _bcPollTimer = null;
        document.getElementById('bcStartBtn').style.display = '';
        document.getElementById('bcPauseBtn').style.display = 'none';
        document.getElementById('bcResumeBtn').style.display = 'none';
        document.getElementById('bcStopBtn').style.display = 'none';
      }
    } catch (e) {}
  }, 2000);
}

function _updateBcUI(status, total, sent, failed, log) {
  const labels = { idle: 'Очікування', running: '⚡ Відправка...', paused: '⏸ Пауза', done: '✅ Завершено', stopped: '⏹ Зупинено' };
  document.getElementById('bcStatusText').textContent = labels[status] || status;
  document.getElementById('bcStatusDot').className = 'bc-status-dot ' + status;
  if (total > 0) {
    document.getElementById('bcSent').textContent = sent;
    document.getElementById('bcFailed').textContent = failed;
    document.getElementById('bcTotal').textContent = total;
    document.getElementById('bcProgressFill').style.width = Math.round((sent + failed) / total * 100) + '%';
    document.getElementById('bcProgressWrap').style.display = '';
  }
  if (log && log.length) {
    document.getElementById('bcLog').innerHTML = [...log].reverse().map(l =>
      `<div class="bc-log-row ${l.ok === true ? 'ok' : l.ok === false ? 'err' : 'wait'}">
        <span class="bc-log-ts">${l.ts}</span>
        <span class="bc-log-acc">${l.acc}</span>
        <span class="bc-log-contact">${l.contact}</span>
        <span class="bc-log-status">${l.ok ? '✓' : '✗ ' + (l.err || '')}</span>
      </div>`
    ).join('');
  }
}

// ===== INBOX =====
let _inboxPeerId = null;
let _inboxPeerName = '';

function openInboxModal() {
  _closeAllToolModals();
  document.getElementById('inboxModal').style.display = 'flex';
  _populateBcAccounts();
}

function closeInboxModal() {
  document.getElementById('inboxModal').style.display = 'none';
  _inboxPeerId = null;
  document.getElementById('inboxDialogs').classList.remove('mob-hidden');
  document.getElementById('inboxChat').classList.remove('mob-active');
}

async function loadInboxDialogs() {
  const accountId = document.getElementById('inboxAccountSel').value;
  if (!accountId) return;
  const list = document.getElementById('inboxDialogs');
  list.innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center;padding:20px">⏳ Завантаження...</div>';
  try {
    const res = await fetch(`/api/inbox/dialogs?account_id=${accountId}`);
    if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || res.status);
    const dialogs = await res.json();
    if (!dialogs.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center;padding:20px">Немає діалогів</div>';
      return;
    }
    list.innerHTML = dialogs.map(d => {
      const safeName = (d.name || 'Невідомий').replace(/</g, '&lt;');
      const safeNameJs = (d.name || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      const lastMsg = (d.last_message || '').replace(/</g, '&lt;');
      return `<div class="inbox-dialog-item" onclick="openInboxChat(${d.id}, '${safeNameJs}', this)">
        <div class="inbox-dialog-name">${safeName}</div>
        <div class="inbox-dialog-last">${lastMsg}</div>
        ${d.unread_count ? `<span class="inbox-unread">${d.unread_count}</span>` : ''}
      </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:12px">Помилка: ${e.message}</div>`;
  }
}

async function openInboxChat(peerId, peerName, el) {
  _inboxPeerId = peerId;
  _inboxPeerName = peerName;
  document.querySelectorAll('.inbox-dialog-item').forEach(x => x.classList.remove('active'));
  el.classList.add('active');
  if (window.innerWidth <= 768) {
    document.getElementById('inboxDialogs').classList.add('mob-hidden');
    document.getElementById('inboxChat').classList.add('mob-active');
  }
  await _loadInboxMessages();
}

function inboxGoBack() {
  document.getElementById('inboxDialogs').classList.remove('mob-hidden');
  document.getElementById('inboxChat').classList.remove('mob-active');
  document.querySelectorAll('.inbox-dialog-item').forEach(x => x.classList.remove('active'));
  _inboxPeerId = null;
}

async function _loadInboxMessages() {
  const accountId = document.getElementById('inboxAccountSel').value;
  if (!accountId || !_inboxPeerId) return;
  const chat = document.getElementById('inboxChat');
  const safeName = _inboxPeerName.replace(/</g, '&lt;');
  const safeNameJs = _inboxPeerName.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  try {
    const res = await fetch(`/api/inbox/messages?account_id=${accountId}&peer_id=${_inboxPeerId}`);
    if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || res.status);
    const msgs = await res.json();
    chat.innerHTML = `
      <div class="inbox-chat-header">
        <button class="inbox-back-btn" onclick="inboxGoBack()">← Назад</button>
        <strong style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${safeName}</strong>
        <button class="btn-copy-sm" onclick="_loadInboxMessages()">↻ Оновити</button>
      </div>
      <div class="inbox-messages" id="inboxMessages">
        ${msgs.map(m => {
          const date = m.date ? new Date(m.date).toLocaleString('uk-UA', { timeZone: 'Europe/Kyiv', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
          const text = (m.text || (m.has_media ? '📎 медіа' : '—')).replace(/</g, '&lt;').replace(/>/g, '&gt;');
          return `<div class="inbox-msg ${m.out ? 'out' : 'in'}">
            <div class="inbox-bubble">${text}</div>
            <div class="inbox-msg-time">${date}</div>
          </div>`;
        }).join('')}
      </div>
      <div class="inbox-reply-wrap">
        <textarea id="inboxReplyText" class="inbox-reply-input" placeholder="Написати повідомлення..." rows="2"
          onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendInboxReply()}"></textarea>
        <button class="btn-primary inbox-send-btn" onclick="sendInboxReply()">Надіслати</button>
      </div>`;
    const msgEl = document.getElementById('inboxMessages');
    if (msgEl) msgEl.scrollTop = msgEl.scrollHeight;
  } catch (e) {
    chat.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:16px">Помилка: ${e.message}</div>`;
  }
}

async function sendInboxReply() {
  const accountId = parseInt(document.getElementById('inboxAccountSel').value);
  const text = document.getElementById('inboxReplyText').value.trim();
  if (!text || !_inboxPeerId) return;
  const btn = document.querySelector('.inbox-send-btn');
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/inbox/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: accountId, peer_id: _inboxPeerId, text })
    });
    if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || res.status);
    document.getElementById('inboxReplyText').value = '';
    await _loadInboxMessages();
  } catch (e) {
    alert('Помилка: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ===== VIEWS =====
let _viewsPollTimer = null;

function openViewsModal() {
  _closeAllToolModals();
  document.getElementById('viewsModal').style.display = 'flex';
  _populateBcAccounts();
  loadViewsChannels();
  fetch('/api/views/status').then(r => r.json()).then(d => {
    _updateViewsUI(d.running, d.log);
    if (d.running) {
      document.getElementById('viewsStartBtn').style.display = 'none';
      document.getElementById('viewsStopBtn').style.display = '';
      _pollViewsStatus();
    }
  }).catch(() => {});
}

function closeViewsModal() {
  document.getElementById('viewsModal').style.display = 'none';
  if (_viewsPollTimer) { clearInterval(_viewsPollTimer); _viewsPollTimer = null; }
}

async function loadViewsChannels() {
  const list = document.getElementById('viewsChannelsList');
  list.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:4px">Завантаження...</div>';
  try {
    const res = await fetch('/api/mychannels');
    const channels = await res.json();
    list.innerHTML = channels.length
      ? channels.map(ch =>
          `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
            <input type="checkbox" class="views-ch-check"
              value="${ch.channel_id}"
              data-title="${(ch.title || '').replace(/"/g, '&quot;')}"
              data-username="${ch.username || ''}"
              data-ah="${ch.access_hash || 0}">
            ${ch.title}${ch.username ? ` <span style="color:var(--muted);font-size:11px">@${ch.username}</span>` : ''}
          </label>`
        ).join('')
      : '<div style="color:var(--muted);font-size:12px">Немає каналів</div>';
  } catch (e) {
    list.innerHTML = `<div style="color:var(--danger);font-size:12px">Помилка: ${e.message}</div>`;
  }
}

function toggleViewsAllAccounts(cb) {
  document.querySelectorAll('.views-acc-check').forEach(c => c.checked = cb.checked);
}

async function startViews() {
  const channels = [...document.querySelectorAll('.views-ch-check:checked')].map(c => ({
    channel_id: parseInt(c.value),
    access_hash: parseInt(c.dataset.ah) || null,
    title: c.dataset.title,
    username: c.dataset.username || null,
  }));
  const account_ids = [...document.querySelectorAll('.views-acc-check:checked')].map(c => parseInt(c.value));
  if (!channels.length) { alert('Виберіть хоча б один канал'); return; }
  if (!account_ids.length) { alert('Виберіть хоча б один акаунт'); return; }
  try {
    const res = await fetch('/api/views/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channels, account_ids })
    });
    if (!res.ok) { alert(((await res.json().catch(() => ({}))).detail) || 'Помилка'); return; }
    document.getElementById('viewsStartBtn').style.display = 'none';
    document.getElementById('viewsStopBtn').style.display = '';
    _pollViewsStatus();
  } catch (e) { alert('Помилка: ' + e.message); }
}

async function stopViews() {
  await fetch('/api/views/stop', { method: 'POST' });
  if (_viewsPollTimer) { clearInterval(_viewsPollTimer); _viewsPollTimer = null; }
  document.getElementById('viewsStartBtn').style.display = '';
  document.getElementById('viewsStopBtn').style.display = 'none';
  _updateViewsUI(false, []);
}

function _pollViewsStatus() {
  if (_viewsPollTimer) clearInterval(_viewsPollTimer);
  _viewsPollTimer = setInterval(async () => {
    try {
      const d = await fetch('/api/views/status').then(r => r.json());
      _updateViewsUI(d.running, d.log);
    } catch (e) {}
  }, 5000);
}

function _updateViewsUI(running, log) {
  const dot = document.getElementById('viewsStatusDot');
  const txt = document.getElementById('viewsStatusText');
  if (dot) dot.className = 'bc-status-dot ' + (running ? 'running' : 'idle');
  if (txt) txt.textContent = running ? '⚡ Моніторинг активний' : 'Зупинено';
  const logEl = document.getElementById('viewsLog');
  if (logEl && log && log.length) {
    logEl.innerHTML = [...log].reverse().map(l =>
      `<div class="bc-log-row ${l.ok ? 'ok' : 'err'}">
        <span class="bc-log-ts">${l.ts}</span>
        <span class="bc-log-acc">${l.channel}</span>
        <span class="bc-log-contact">пост #${l.msg_id}</span>
        <span class="bc-log-status">${l.ok ? '✓' : '✗ ' + (l.err || '')}</span>
      </div>`
    ).join('');
  }
}

// ===== NOTIFICATIONS =====

let _notifData = [];
let _notifPollTimer = null;

function _notifFmt(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'М';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'К';
  return String(n);
}

async function loadNotifications() {
  try {
    const res = await fetch('/api/notifications');
    if (!res.ok) return;
    _notifData = await res.json();
    _renderNotifBell();
    if (document.getElementById('notifPanel').classList.contains('open')) {
      _renderNotifList();
    }
  } catch (e) {}
}

function _renderNotifBell() {
  const unread = _notifData.filter(n => !n.is_read).length;
  const badge = document.getElementById('notifBadge');
  const btn = document.getElementById('notifBellBtn');
  if (unread > 0) {
    badge.textContent = unread > 99 ? '99+' : unread;
    badge.style.display = 'flex';
    btn.classList.add('has-unread');
  } else {
    badge.style.display = 'none';
    btn.classList.remove('has-unread');
  }
}

function openNotifPanel() {
  document.getElementById('notifPanel').classList.add('open');
  document.getElementById('notifBackdrop').classList.add('open');
  _renderNotifList();
  if (_notifPollTimer) clearInterval(_notifPollTimer);
  _notifPollTimer = setInterval(loadNotifications, 30_000);
}

function closeNotifPanel() {
  document.getElementById('notifPanel').classList.remove('open');
  document.getElementById('notifBackdrop').classList.remove('open');
  if (_notifPollTimer) clearInterval(_notifPollTimer);
  _notifPollTimer = setInterval(loadNotifications, 5 * 60_000);
}

function _renderNotifList() {
  const list = document.getElementById('notifList');
  const empty = document.getElementById('notifEmpty');
  const countEl = document.getElementById('notifPanelCount');
  const readAllBtn = document.getElementById('notifReadAllBtn');
  const delAllBtn = document.getElementById('notifDelAllBtn');

  const total = _notifData.length;
  const unread = _notifData.filter(n => !n.is_read).length;

  countEl.textContent = total ? `${total}${unread ? `, ${unread} нових` : ''}` : '';
  readAllBtn.style.display = unread > 0 ? 'block' : 'none';
  if (delAllBtn) delAllBtn.style.display = total > 0 ? 'inline-flex' : 'none';

  if (!total) {
    empty.style.display = 'block';
    [...list.children].forEach(c => { if (c.id !== 'notifEmpty') c.remove(); });
    return;
  }
  empty.style.display = 'none';
  list.innerHTML = '';
  _notifData.forEach(n => {
    const card = document.createElement('div');
    card.className = `notif-card ${n.is_read ? 'read' : 'unread'}`;
    card.id = `notif-card-${n.id}`;
    card.innerHTML = _buildNotifCardHTML(n);
    list.appendChild(card);
  });
}

function _typeLabel(type) {
  return {day: 'Денний', week: 'Тижневий', month: 'Місячний', inbox: 'Повідомлення'}[type] || type;
}
function _typePillClass(type) {
  return {day: '', week: 'week', month: 'month', inbox: 'inbox'}[type] || '';
}

function _buildNotifCardHTML(n) {
  const d = n.report_data;
  const dateStr = new Date(n.created_at).toLocaleDateString('uk-UA', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'});

  const delBtn = `<button class="notif-del-btn" onclick="event.stopPropagation();deleteNotif(${n.id})" title="Видалити">✕</button>`;

  if (n.report_type === 'inbox') {
    const preview = _escHtml((d.message_text || '').replace(/\n/g, ' ').substring(0, 80));
    return `
      <div class="notif-card-head" onclick="_toggleNotifBody(${n.id})">
        <div class="notif-card-row1">
          <span class="notif-type-pill ${_typePillClass(n.report_type)}">${_typeLabel(n.report_type)}</span>
          <span class="notif-card-channel">${_escHtml(n.channel_title)}</span>
          ${!n.is_read ? '<span class="notif-unread-dot"></span>' : ''}
          ${delBtn}
        </div>
        <div class="notif-card-date">📱 ${_escHtml(d.account_name || '')} • ${dateStr}</div>
        <div class="notif-card-preview notif-inbox-preview">${preview || '<i>медіа-повідомлення</i>'}</div>
      </div>
      <div class="notif-card-body" id="notif-body-${n.id}" style="display:none">
        ${_buildNotifBodyHTML(n)}
      </div>`;
  }

  const growth = d.growth;
  const growthClass = growth === null ? 'neutral' : growth > 0 ? 'positive' : growth < 0 ? 'negative' : 'neutral';
  const growthStr = growth === null ? '—' : (growth > 0 ? `+${growth}` : String(growth));

  return `
    <div class="notif-card-head" onclick="_toggleNotifBody(${n.id})">
      <div class="notif-card-row1">
        <span class="notif-type-pill ${_typePillClass(n.report_type)}">${_typeLabel(n.report_type)}</span>
        <span class="notif-card-channel">${_escHtml(n.channel_title)}</span>
        ${!n.is_read ? '<span class="notif-unread-dot"></span>' : ''}
        ${delBtn}
      </div>
      <div class="notif-card-date">${_escHtml(d.period_str || '')} • отримано ${dateStr}</div>
      <div class="notif-card-preview">
        <span class="notif-stat ${growthClass}">👥 ${growthStr}</span>
        <span class="notif-stat">👁 ${_notifFmt(d.total_views || 0)}</span>
        <span class="notif-stat">ER ${d.er || 0}%</span>
        <span class="notif-stat">✉️ ${d.total_posts || 0} постів</span>
      </div>
    </div>
    <div class="notif-card-body" id="notif-body-${n.id}" style="display:none">
      ${_buildNotifBodyHTML(n)}
    </div>`;
}

function _buildNotifBodyHTML(n) {
  const d = n.report_data;

  if (n.report_type === 'inbox') {
    const senderLink = d.sender_username
      ? `<a class="notif-post-link" href="https://t.me/${_escHtml(d.sender_username)}" target="_blank">@${_escHtml(d.sender_username)}</a>`
      : '';
    const msgText = _escHtml(d.message_text || '').replace(/\n/g, '<br>');
    const readBtn = n.is_read
      ? `<div class="notif-already-read">✓ Прочитано</div>`
      : `<button class="notif-mark-read-btn" onclick="markNotifRead(${n.id})">✓ Позначити як прочитане</button>`;
    return `
      <div class="notif-section">
        <div class="notif-section-title">👤 Відправник</div>
        <div class="notif-inbox-sender">
          <strong>${_escHtml(d.sender_name || n.channel_title)}</strong>
          ${senderLink}
        </div>
      </div>
      <div class="notif-section">
        <div class="notif-section-title">📱 На акаунт</div>
        <div class="notif-inbox-account">${_escHtml(d.account_name || '')}</div>
      </div>
      <div class="notif-section">
        <div class="notif-section-title">💬 Повідомлення</div>
        <div class="notif-inbox-text">${msgText || '<i>медіа-повідомлення</i>'}</div>
      </div>
      ${readBtn}`;
  }

  const growth = d.growth;
  const growthClass = growth === null ? 'neutral' : growth > 0 ? 'positive' : growth < 0 ? 'negative' : 'neutral';
  const growthStr = growth === null ? '—' : (growth > 0 ? `▲ +${growth}` : growth < 0 ? `▼ ${growth}` : '➡️ 0');
  const growthPctStr = d.growth_pct != null ? ` (${d.growth_pct > 0 ? '+' : ''}${d.growth_pct}%)` : '';

  let html = '';

  html += `<div class="notif-section">
    <div class="notif-section-title">👥 Підписники</div>
    <div class="notif-growth-row">
      <span class="notif-growth-val ${growthClass}">${growthStr}${growthPctStr}</span>
      ${d.members_count ? `<span class="notif-growth-sub">Загалом: ${_notifFmt(d.members_count)}</span>` : ''}
    </div>
  </div>`;

  html += `<div class="notif-section">
    <div class="notif-section-title">📊 Контент за ${_escHtml(d.period_str || 'період')}</div>
    <div class="notif-metrics">
      <div class="notif-metric"><div class="notif-metric-val">${_notifFmt(d.total_views || 0)}</div><div class="notif-metric-lbl">Переглядів</div></div>
      <div class="notif-metric"><div class="notif-metric-val">${_notifFmt(d.avg_views || 0)}</div><div class="notif-metric-lbl">Сер. перегляд</div></div>
      <div class="notif-metric"><div class="notif-metric-val">${d.er || 0}%</div><div class="notif-metric-lbl">ER</div></div>
      <div class="notif-metric"><div class="notif-metric-val">${d.total_posts || 0}</div><div class="notif-metric-lbl">Постів</div></div>
      <div class="notif-metric"><div class="notif-metric-val">${_notifFmt(d.total_reactions || 0)}</div><div class="notif-metric-lbl">Реакцій</div></div>
      <div class="notif-metric"><div class="notif-metric-val">${_notifFmt(d.total_forwards || 0)}</div><div class="notif-metric-lbl">Репостів</div></div>
    </div>
  </div>`;

  if (d.compare) {
    const c = d.compare;
    const _cmpRow = (label, cur, prev) => {
      if (prev == null || prev === 0) return '';
      const delta = cur - prev;
      const pct = Math.round(Math.abs(delta) / prev * 100);
      const cls = delta > 0 ? 'positive' : delta < 0 ? 'negative' : 'neutral';
      const arrow = delta > 0 ? '↑' : delta < 0 ? '↓' : '→';
      return `<div class="notif-compare-row"><span class="notif-compare-label">${label}</span><span class="notif-compare-val ${cls}">${arrow} ${pct}%</span></div>`;
    };
    const _cmpRowAbs = (label, cur, prev, unit='') => {
      if (prev == null) return '';
      const delta = Math.round((cur - prev) * 10) / 10;
      const cls = delta > 0 ? 'positive' : delta < 0 ? 'negative' : 'neutral';
      const arrow = delta > 0 ? '↑' : delta < 0 ? '↓' : '→';
      const sign = delta > 0 ? '+' : '';
      return `<div class="notif-compare-row"><span class="notif-compare-label">${label}</span><span class="notif-compare-val ${cls}">${arrow} ${sign}${delta}${unit}</span></div>`;
    };
    let cmpHtml = '';
    const isPeriodCompare = n.report_type !== 'day';
    if (isPeriodCompare) {
      cmpHtml += _cmpRow('👁 Перегляди', d.total_views, c.prev_views);
      cmpHtml += _cmpRowAbs('ER', d.er, c.prev_er, '%');
      if (c.prev_growth != null && d.growth != null) {
        const cls = d.growth > c.prev_growth ? 'positive' : d.growth < c.prev_growth ? 'negative' : 'neutral';
        const arrow = d.growth > c.prev_growth ? '↑' : d.growth < c.prev_growth ? '↓' : '→';
        const diff = d.growth - c.prev_growth;
        cmpHtml += `<div class="notif-compare-row"><span class="notif-compare-label">👥 Ріст підписн.</span><span class="notif-compare-val ${cls}">${arrow} ${diff > 0 ? '+' : ''}${diff}</span></div>`;
      }
    } else {
      if (c.prev_views != null) cmpHtml += _cmpRow('👁 vs вчора', d.total_views, c.prev_views);
      if (c.week_avg_views != null) cmpHtml += _cmpRow('👁 vs 7-денний сер.', d.total_views, c.week_avg_views);
      if (c.month_avg_views != null) cmpHtml += _cmpRow('👁 vs 30-денний сер.', d.total_views, c.month_avg_views);
      if (c.prev_er != null) cmpHtml += _cmpRowAbs('ER vs вчора', d.er, c.prev_er, '%');
      if (c.week_avg_er != null) cmpHtml += _cmpRowAbs('ER vs 7-денний сер.', d.er, c.week_avg_er, '%');
    }
    if (cmpHtml) {
      html += `<div class="notif-section">
        <div class="notif-section-title">📈 Порівняння з попереднім</div>
        <div class="notif-compare-grid">${cmpHtml}</div>
      </div>`;
    }
  }

  if (d.best_post) {
    const p = d.best_post;
    const preview = p.text ? `«${_escHtml(p.text.replace(/\n/g,' ').substring(0,90))}»` : '[медіа без тексту]';
    const link = d.channel_username ? `<a class="notif-post-link" href="https://t.me/${d.channel_username}/${p.id}" target="_blank">Відкрити →</a>` : '';
    html += `<div class="notif-section">
      <div class="notif-section-title">🏆 Кращий пост</div>
      <div class="notif-best-post">
        <div class="notif-best-post-text">${preview}</div>
        <div class="notif-best-post-stats">
          <span>👁 ${_notifFmt(p.views)}</span>
          <span>❤️ ${p.reactions}</span>
          <span>🔄 ${p.forwards}</span>
          ${link}
        </div>
      </div>
    </div>`;
  }

  if (d.top_posts && d.top_posts.length) {
    const medals = ['🥇','🥈','🥉','4.','5.'];
    html += `<div class="notif-section">
      <div class="notif-section-title">🏆 Топ пости</div>
      <div class="notif-top-posts">`;
    d.top_posts.forEach((p, i) => {
      const preview = p.text ? _escHtml(p.text.replace(/\n/g,' ').substring(0,55)) : '[медіа]';
      const link = d.channel_username ? `<a class="notif-post-link" href="https://t.me/${d.channel_username}/${p.id}" target="_blank">→</a>` : '';
      html += `<div class="notif-top-post-row">
        <span class="notif-top-rank">${medals[i]||((i+1)+'.')}</span>
        <span class="notif-top-text">${preview}</span>
        <span class="notif-top-views">👁 ${_notifFmt(p.views)}</span>
        ${link}
      </div>`;
    });
    html += `</div></div>`;
  }

  if (d.tips && d.tips.length) {
    html += `<div class="notif-section">
      <div class="notif-section-title">💡 Рекомендації</div>
      <div class="notif-tips">`;
    d.tips.forEach(tip => { html += `<div class="notif-tip">${_escHtml(tip)}</div>`; });
    html += `</div></div>`;
  }

  if (d.verdict_text) {
    html += `<div class="notif-section">
      <div class="notif-verdict ${d.verdict || 'neutral'}">${_escHtml(d.verdict_text)}</div>
    </div>`;
  }

  if (!n.is_read) {
    html += `<button class="notif-mark-read-btn" onclick="markNotifRead(${n.id})">✓ Позначити як прочитане</button>`;
  } else {
    html += `<div class="notif-already-read">✓ Прочитано</div>`;
  }

  return html;
}

function _escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _toggleNotifBody(id) {
  const body = document.getElementById(`notif-body-${id}`);
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  if (!isOpen) {
    const notif = _notifData.find(n => n.id === id);
    if (notif && !notif.is_read) markNotifRead(id);
  }
}

async function markNotifRead(id) {
  try {
    await fetch(`/api/notifications/${id}/read`, {method: 'POST'});
    const notif = _notifData.find(n => n.id === id);
    if (notif) notif.is_read = true;
    const card = document.getElementById(`notif-card-${id}`);
    if (card) {
      card.classList.remove('unread');
      card.classList.add('read');
      const dot = card.querySelector('.notif-unread-dot');
      if (dot) dot.remove();
      const btn = card.querySelector('.notif-mark-read-btn');
      if (btn) {
        const label = document.createElement('div');
        label.className = 'notif-already-read';
        label.textContent = '✓ Прочитано';
        btn.replaceWith(label);
      }
    }
    _renderNotifBell();
    _updateReadAllBtn();
  } catch (e) {}
}

async function markAllNotifsRead() {
  try {
    await fetch('/api/notifications/read-all', {method: 'POST'});
    _notifData.forEach(n => { n.is_read = true; });
    _renderNotifBell();
    _renderNotifList();
  } catch (e) {}
}

function _updateReadAllBtn() {
  const unread = _notifData.filter(n => !n.is_read).length;
  const btn = document.getElementById('notifReadAllBtn');
  if (btn) btn.style.display = unread > 0 ? 'block' : 'none';
  const delBtn = document.getElementById('notifDelAllBtn');
  if (delBtn) delBtn.style.display = _notifData.length > 0 ? 'inline-flex' : 'none';
  const countEl = document.getElementById('notifPanelCount');
  const total = _notifData.length;
  if (countEl) countEl.textContent = total ? `${total}${unread ? `, ${unread} нових` : ''}` : '';
}

async function deleteNotif(id) {
  try {
    await fetch(`/api/notifications/${id}`, {method: 'DELETE'});
    _notifData = _notifData.filter(n => n.id !== id);
    const card = document.getElementById(`notif-card-${id}`);
    if (card) card.remove();
    _renderNotifBell();
    _updateReadAllBtn();
    if (_notifData.length === 0) {
      const empty = document.getElementById('notifEmpty');
      if (empty) empty.style.display = 'block';
    }
  } catch (e) {}
}

async function deleteAllNotifs() {
  if (!confirm('Видалити всі сповіщення?')) return;
  try {
    await fetch('/api/notifications', {method: 'DELETE'});
    _notifData = [];
    _renderNotifBell();
    _renderNotifList();
  } catch (e) {}
}

let _notifFilterOpen = false;
let _notifFilterData = [];

async function toggleNotifFilter() {
  _notifFilterOpen = !_notifFilterOpen;
  const wrap = document.getElementById('notifFilterWrap');
  const btn = document.getElementById('notifFilterBtn');
  if (_notifFilterOpen) {
    wrap.style.display = 'block';
    btn.classList.add('active');
    await loadNotifFilters();
  } else {
    wrap.style.display = 'none';
    btn.classList.remove('active');
  }
}

async function loadNotifFilters() {
  const list = document.getElementById('notifFilterList');
  list.innerHTML = '<div class="notif-filter-loading">Завантаження...</div>';
  try {
    const res = await fetch('/api/notifications/channel-filters');
    _notifFilterData = await res.json();
    _renderNotifFilters();
  } catch (e) {
    list.innerHTML = '<div class="notif-filter-empty">Помилка завантаження</div>';
  }
}

function _renderNotifFilters() {
  const list = document.getElementById('notifFilterList');
  if (!_notifFilterData.length) {
    list.innerHTML = '<div class="notif-filter-empty">Ще немає каналів зі звітами</div>';
    return;
  }
  list.innerHTML = _notifFilterData.map(ch => `
    <div class="notif-filter-row">
      <span class="notif-filter-channel">${_escHtml(ch.channel_title)}</span>
      <label class="notif-toggle">
        <input type="checkbox" ${ch.enabled ? 'checked' : ''}
               onchange="toggleNotifChannel(${ch.channel_id}, this.checked)">
        <span class="notif-toggle-slider"></span>
      </label>
    </div>
  `).join('');
}

async function toggleNotifChannel(channelId, enabled) {
  const action = enabled ? 'enable' : 'disable';
  await fetch(`/api/notifications/channel-filters/${channelId}/${action}`, {method: 'POST'});
  const ch = _notifFilterData.find(c => c.channel_id === channelId);
  if (ch) ch.enabled = enabled;
}
