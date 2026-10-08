import { API, apiFetch, patientId, setSelectedPatient, getSelectedPatient, formatTs, formatTsFull, emptyState, ROLE_LABEL, escapeHtml, getCurrentUser, setCurrentUser, getDualControlToken, setDualControlToken, appState } from './modules/utils.js';
import { mfaRequired, resetLoginFormState, resetLoginForm, fillCreds, handleLoginSubmit, logout, setup2FA, enable2FA, disable2FA, initAuthListeners, loginWithPasskey, registerPasskey } from './modules/auth.js';
import { updateChainPill, loadDashboard, navigate } from './modules/dashboard.js';
import { allRecords, recordTypes, loadRecordTypes, loadRecords, filterRecords, renderAllRecords, renderRecordCard, renderAttachmentHtml, downloadBase64File, downloadOffchainFile, openRecord, decryptRecord, verifyMerkleProof, viewOriginalVersion, renderCorrectionForm, submitCorrection, closeModal, DYNAMIC_FIELDS, renderDynamicFields, initRecordsListeners } from './modules/records.js';
import { getNotifications, addNotification, updateNotificationsUI, toggleNotifications, closeAllDropdowns, markAsRead, markAllAsRead, clearAllNotifications } from './modules/notifications.js';
import { loadChainStatus } from './modules/blockchain.js';
import { registerActions, initActionDispatch, takePayload } from './modules/actions.js';
import { exportClient, requestErasure, loadErasureRequests, eraseClient, closeErasureRequest, loadSecurityAlerts, acknowledgeAlert } from './modules/kvkk.js';
import { loadAppointments, setAppointmentStatus, startMove, saveMove, cancelMove, bookAppointment } from './modules/appointments.js';
import { loadClients, saveClient, editClient, cancelEdit, inviteSecretary, copyField, openClient, showRedeem, showLogin, redeemInvite, checkInviteLink } from './modules/clients.js';

/* -- Particle Background Canvas ---------------------------------------- */
(function initParticles() {
  const c = document.getElementById('particles-canvas');
  if (!c) return;
  const ctx = c.getContext('2d');
  let W, H, particles = [];
  const resize = () => { W = c.width = window.innerWidth; H = c.height = window.innerHeight; };
  resize(); window.addEventListener('resize', resize);
  for (let i = 0; i < 60; i++) particles.push({
    x: Math.random()*1920, y: Math.random()*1080,
    vx: (Math.random()-.5)*.3, vy: (Math.random()-.5)*.3,
    r: Math.random()*1.5+.5, a: Math.random()*.4+.1
  });
  (function draw() {
    ctx.clearRect(0,0,W,H);
    particles.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      if (p.x<0) p.x=W; if (p.x>W) p.x=0;
      if (p.y<0) p.y=H; if (p.y>H) p.y=0;
      ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle=`rgba(201,168,76,${p.a})`; ctx.fill();
    });
    requestAnimationFrame(draw);
  })();
})();

/* -- Enter App Initialization --------------------------------------- */
window.enterApp = function(options = {}) {
  const currentUser = getCurrentUser();
  if (!currentUser) return;

  const pageLogin = document.getElementById('page-login');
  if (pageLogin) {
    pageLogin.classList.remove('active');
    pageLogin.style.display = 'none';
  }
  const pageApp = document.getElementById('page-app');
  if (pageApp) {
    pageApp.style.display = 'flex';
    pageApp.classList.add('active');
  }

  // Use centralized state manager
  appState.updateUser(currentUser);

  const isSecretary = currentUser.role === 'secretary';
  const isPractitioner = currentUser.role === 'practitioner';

  // Operators type which client to view; a practitioner picks from their
  // client list, and a secretary opens no client file at all.
  const selector = document.getElementById('patient-selector');
  if (selector) selector.hidden = isPractitioner || isSecretary;
  const selInput = document.getElementById('patient-selector-input');
  if (selInput) selInput.value = getSelectedPatient() || '';

  // Pre-fill the "add record" patient field
  const recPatId = document.getElementById('rec-patient-id');
  if (recPatId) recPatId.value = getSelectedPatient() || '';

  // Set today's date
  const recDate = document.getElementById('rec-date');
  if (recDate) recDate.value = new Date().toISOString().split('T')[0];

  updateNotificationsUI();
  addNotification('System Login', `Access granted to user ${currentUser.username}. Device Fingerprint verified.`, 'success');

  if (options.passkeyRequired) {
    addNotification('Passkey required', 'This vault requires a passkey. Enrol one on this page before you continue.', 'warning');
  }
  const landing = options.passkeyRequired ? 'security' : (isSecretary ? 'appointments' : 'dashboard');
  loadRecordTypes().then(() => navigate(landing));
};

/* -- Page-Specific View Handlers (Remaining from Monolith) ----------- */
window.loadDualControl = function() {
  const errEl = document.getElementById('dc-error');
  const succEl = document.getElementById('dc-success');
  if (errEl) errEl.style.display = 'none';
  if (succEl) succEl.style.display = 'none';
  window.renderDualControlToken();
  // Pick up a co-signature granted while this page was closed.
  if (getDualControlToken()) window.refreshDualControlStatus();
};

window.renderDualControlToken = function() {
  const box = document.getElementById('dc-active-token');
  if (!box) return;

  const token = getDualControlToken();
  if (!token) {
    box.innerHTML = `
      <div class="glass" style="padding:16px; border-radius:var(--radius); border:1px solid var(--border);">
        <div style="font-size:13px; color:var(--muted);">No co-signed token held. Client records stay locked until a second privileged principal approves a request.</div>
      </div>`;
    return;
  }

  const approved = token.status === 'APPROVED';
  const expiresIn = token.expires_at ? Math.max(0, Math.round((token.expires_at * 1000 - Date.now()) / 60000)) : null;
  box.innerHTML = `
    <div class="glass" style="padding:16px 20px; border-radius:var(--radius); border:1px solid ${approved ? 'rgba(16,185,129,0.35)' : 'rgba(245,158,11,0.35)'};">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap;">
        <div>
          <div style="font-weight:700; font-size:14px; color:${approved ? '#10b981' : '#f59e0b'};">
            ${approved ? 'CO-SIGNED — raw record access unlocked' : 'PENDING CO-APPROVAL'}
          </div>
          <div style="font-size:12px; color:var(--muted-hi); margin-top:4px;">
            Client <strong>${escapeHtml(token.target_patient_id || '—')}</strong> ·
            Token <code style="font-family:var(--font-mono);">${escapeHtml(token.token_id)}</code>
            ${expiresIn !== null ? ` · expires in ${expiresIn} min` : ''}
          </div>
          ${token.co_signed_by ? `<div style="font-size:12px; color:var(--muted); margin-top:2px;">Co-signed by <strong>${escapeHtml(token.co_signed_by)}</strong></div>` : ''}
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-ghost btn-sm" data-action="dual-control-refresh">Refresh Status</button>
          <button class="btn btn-ghost btn-sm" data-action="dual-control-discard">Discard Token</button>
        </div>
      </div>
    </div>`;
};

window.refreshDualControlStatus = async function() {
  const held = getDualControlToken();
  if (!held) return;
  const errEl = document.getElementById('dc-error');
  try {
    const info = await apiFetch(`/api/security/dual-control/${held.token_id}`);
    setDualControlToken({
      token_id: info.token_id,
      status: info.status,
      target_patient_id: info.target_patient_id,
      expires_at: info.expires_at,
      co_signed_by: info.co_signed_by
    });
    window.renderDualControlToken();
    if (info.status === 'APPROVED') {
      addNotification('Dual-Control Approved', `Token ${info.token_id} was co-signed by ${info.co_signed_by}.`, 'success');
    }
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message;
      errEl.style.display = 'block';
    }
  }
};

window.clearDualControlToken = function() {
  setDualControlToken(null);
  window.renderDualControlToken();
  addNotification('Dual-Control Token Discarded', 'Privileged raw record access has been relinquished.', 'info');
};

window.requestDualControl = async function(event) {
  if (event) event.preventDefault();
  const errEl = document.getElementById('dc-error');
  const succEl = document.getElementById('dc-success');
  errEl.style.display = 'none';
  succEl.style.display = 'none';

  try {
    const res = await apiFetch('/api/security/dual-control/request', {
      method: 'POST',
      body: JSON.stringify({
        request_type: 'DECRYPT_RAW_RECORD',
        target_patient_id: document.getElementById('dc-patient-id').value.trim(),
        reason: document.getElementById('dc-reason').value.trim(),
        validity_minutes: parseInt(document.getElementById('dc-validity').value || 30)
      })
    });

    setDualControlToken({
      token_id: res.token_id,
      status: res.status,
      target_patient_id: document.getElementById('dc-patient-id').value.trim(),
      expires_at: res.expires_at
    });
    window.renderDualControlToken();

    succEl.textContent = `${res.message} Token: ${res.token_id}`;
    succEl.style.display = 'block';
    document.getElementById('dc-cosign-token').value = res.token_id;
    addNotification('Dual-Control Requested', `Access request raised for ${document.getElementById('dc-patient-id').value.trim()}. Awaiting co-signature.`, 'warning');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
};

window.coSignDualControl = async function(event) {
  if (event) event.preventDefault();
  const errEl = document.getElementById('dc-error');
  const succEl = document.getElementById('dc-success');
  errEl.style.display = 'none';
  succEl.style.display = 'none';

  const tokenId = document.getElementById('dc-cosign-token').value.trim();
  try {
    const res = await apiFetch('/api/security/dual-control/co-sign', {
      method: 'POST',
      body: JSON.stringify({ token_id: tokenId })
    });

    // Only the requester benefits from holding the token locally; a co-signer
    // approving someone else's request keeps whatever token they already had.
    const held = getDualControlToken();
    if (held && held.token_id === res.token_id) {
      setDualControlToken({ ...held, status: res.status, co_signed_by: res.co_signed_by });
    }
    window.renderDualControlToken();

    succEl.textContent = res.message;
    succEl.style.display = 'block';
    addNotification('Dual-Control Co-Signed', `Token ${res.token_id} approved for client ${res.target_patient_id}.`, 'success');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
};

window.loadUsers = async function() {
  const container = document.getElementById('users-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Loading...</div>';
  try {
    const d = await apiFetch('/api/admin/users');
    container.innerHTML = d.users.map(u => `
      <div class="user-card glass">
        <div class="user-avatar" style="background:linear-gradient(135deg,#C9A84C,#8B6914)">${escapeHtml(u.full_name.charAt(0))}</div>
        <div style="flex:1">
          <div style="font-weight:600">${escapeHtml(u.full_name)}</div>
          <div style="font-size:12px;color:var(--muted)">@${escapeHtml(u.username)} · ${escapeHtml(u.account_status||'')}</div>
        </div>
        <span class="role-badge badge-${u.role==='admin'?'admin':'practitioner'}">${escapeHtml(ROLE_LABEL[u.role]||u.role)}</span>
      </div>`
    ).join('');
  } catch(e) { container.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`; }
};

window.loadAuditLog = async function() {
  const container = document.getElementById('audit-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Loading...</div>';
  try {
    const pid = patientId();
    const d = await apiFetch(`/api/blockchain/${pid}/audit?limit=100`);
    if (!d.logs || d.logs.length === 0) {
      container.innerHTML = emptyState('No audit records yet');
      return;
    }
    container.innerHTML = d.logs.map(log => {
      const isAlert = log.action.includes('FAILED') || log.action.includes('REVOKE');
      const statusLabel = isAlert ? 'ALERT' : 'OK';
      return `
      <div class="record-card ${isAlert ? 'is-encrypted' : ''}" style="cursor:default">
        <div class="record-type-icon record-type-text">${escapeHtml(log.action.substring(0,3))}</div>
        <div class="record-main">
          <div class="record-title">${escapeHtml(log.action.replace(/_/g, ' '))}</div>
          <div class="record-meta">User: <strong>${escapeHtml(log.username)}</strong> · Block: ${log.block_index !== null ? '#'+log.block_index : '—'}</div>
          <div class="record-meta" style="font-family:monospace;font-size:11px;color:var(--muted)">Device: ${escapeHtml((log.device_id||'?').substring(0,16))}...</div>
        </div>
        <div class="record-right">
          <div class="record-date">${formatTsFull(log.timestamp)}</div>
          <div class="record-hash" style="color:${isAlert?'var(--danger)':'var(--success)'}">${statusLabel}</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
};

window.currentLogTab = 'audit';

window.switchLogTab = function(tab) {
  window.currentLogTab = tab;
  const auditList  = document.getElementById('audit-list');
  const accessList = document.getElementById('access-log-list');
  const btnAudit   = document.getElementById('btn-audit-tab');
  const btnAccess  = document.getElementById('btn-access-tab');

  if (!auditList || !accessList) return;

  if (tab === 'audit') {
    auditList.style.display  = 'block';
    accessList.style.display = 'none';
    if (btnAudit) btnAudit.className = 'btn btn-gold btn-sm';
    if (btnAccess) btnAccess.className = 'btn btn-ghost btn-sm';
    window.loadAuditLog();
  } else {
    auditList.style.display  = 'none';
    accessList.style.display = 'block';
    if (btnAudit) btnAudit.className = 'btn btn-ghost btn-sm';
    if (btnAccess) btnAccess.className = 'btn btn-gold btn-sm';
    window.loadAccessLogs();
  }
};

window.loadAccessLogs = async function() {
  const container = document.getElementById('access-log-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Loading...</div>';
  const pid = patientId();
  if (!pid) {
    container.innerHTML = emptyState('No client selected. Choose one on the Dashboard.');
    return;
  }
  try {
    const d = await apiFetch(`/api/blockchain/${pid}/access-logs?limit=100`);
    if (!d.logs || d.logs.length === 0) {
      container.innerHTML = emptyState('No access log entries yet');
      return;
    }
    container.innerHTML = d.logs.map(log => {
      const isAlert = log.action.includes('FAILED') || log.action.includes('REVOKE');
      const statusLabel = isAlert ? 'ALERT' : 'ACCESS';
      return `
      <div class="record-card ${isAlert ? 'is-encrypted' : ''}" style="cursor:default">
        <div class="record-type-icon record-type-text">${escapeHtml(log.action.substring(0,3))}</div>
        <div class="record-main">
          <div class="record-title">${escapeHtml(log.action.replace(/_/g, ' '))}</div>
          <div class="record-meta">User: <strong>${escapeHtml(log.username)}</strong> · Block: ${log.block_index !== undefined && log.block_index !== null ? '#'+log.block_index : '—'}</div>
          <div class="record-meta" style="font-family:monospace;font-size:11px;color:var(--muted)">Device: ${escapeHtml((log.device_id||'?').substring(0,16))}...</div>
        </div>
        <div class="record-right">
          <div class="record-date">${formatTsFull(log.timestamp)}</div>
          <div class="record-hash" style="color:${isAlert?'var(--danger)':'var(--success)'}">${statusLabel}</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
};

window.refreshLogPage = function() {
  if (window.currentLogTab === 'audit') window.loadAuditLog();
  else window.loadAccessLogs();
};

/* -- Patient-facing access transparency ("Who Accessed My Records") -- */
window.loadMyAccessLog = async function() {
  const banner = document.getElementById('my-access-integrity');
  const container = document.getElementById('my-access-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Loading access ledger...</div>';
  if (banner) banner.innerHTML = '';

  const pid = patientId();
  if (!pid) {
    container.innerHTML = emptyState('No client selected. Choose one on the Dashboard.');
    return;
  }
  try {
    const d = await apiFetch(`/api/blockchain/${pid}/access-logs?limit=100`);

    // Integrity banner: the whole point of this view is that the trail is provable.
    const intact = !d.integrity || d.integrity.valid;
    if (banner) {
      banner.innerHTML = `
        <div class="alert ${intact ? 'alert-success' : 'alert-error'}" style="display:flex; align-items:center; gap:10px; line-height:1.5;">
          <span style="font-size:18px;">${intact ? '🛡️' : '⚠️'}</span>
          <div>
            <strong>${intact ? 'Access ledger intact' : 'Access ledger TAMPERED'}</strong>
            ${d.integrity ? ` — ${d.integrity.count} entr${d.integrity.count === 1 ? 'y' : 'ies'}, hash-linked${intact ? '' : `; broken at #${d.integrity.broken_at}`}` : ''}
          </div>
        </div>`;
    }

    if (!d.logs || d.logs.length === 0) {
      container.innerHTML = emptyState('No one has opened this file yet.');
      return;
    }

    container.innerHTML = d.logs.map(log => {
      const action = log.action || '';
      const isAlert = action.includes('FAILED') || action.includes('REVOKE');
      const isRead = action.includes('READ') || action.includes('DECRYPTED') || action.includes('VIEWED');
      const label = isAlert ? 'ALERT' : (isRead ? 'READ' : 'EVENT');
      return `
      <div class="record-card ${isAlert ? 'is-encrypted' : ''}" style="cursor:default">
        <div class="record-type-icon record-type-text">${escapeHtml(action.substring(0,3))}</div>
        <div class="record-main">
          <div class="record-title">${escapeHtml(action.replace(/_/g, ' '))}</div>
          <div class="record-meta">By <strong>${escapeHtml(log.username || 'unknown')}</strong>${log.block_index !== undefined && log.block_index !== null ? ' · Record #' + escapeHtml(String(log.block_index)) : ''}</div>
          <div class="record-meta" style="font-family:monospace;font-size:10px;color:var(--muted)">#${escapeHtml(String(log.seq ?? '?'))} · ${escapeHtml((log.hash || '').substring(0,16))}…</div>
        </div>
        <div class="record-right">
          <div class="record-date">${formatTsFull(log.timestamp)}</div>
          <div class="record-hash" style="color:${isAlert ? 'var(--danger)' : 'var(--success)'}">${label}</div>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    container.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
};



/* -- Environment check (Demo mode and credentials config) ------------ */
async function checkEnvironment() {
  try {
    const config = await apiFetch('/api/config');
    const demoCreds = document.querySelector('.demo-credentials');
    if (demoCreds) {
      if (config.demo_mode) {
        demoCreds.style.display = 'block';
        const demoList = document.getElementById('demo-credentials-list');
        if (demoList && config.demo_accounts) {
          demoList.innerHTML = config.demo_accounts.map(acc => `
            <div class="demo-item" data-action="fill-credentials" data-arg="${escapeHtml(acc.username)}" data-arg2="${escapeHtml(acc.password)}">
              <span class="role-badge badge-${escapeHtml(acc.role.toLowerCase())}">${escapeHtml(acc.role)}</span>
              <span>${escapeHtml(acc.username)} / ${escapeHtml(acc.password)}</span>
            </div>
          `).join('');
        }
      } else {
        demoCreds.style.display = 'none';
      }
    }
  } catch(e) {
    console.error("Failed to load system config:", e);
  }
}

/* -- Bind Window Properties for Inline Handlers -------------------- */
window.navigate = navigate;
window.logout = logout;
window.resetLoginForm = resetLoginForm;
window.fillCreds = fillCreds;
window.handleLoginSubmit = handleLoginSubmit;
window.loginWithPasskey = loginWithPasskey;
window.registerPasskey = registerPasskey;
window.filterRecords = filterRecords;
window.openRecord = openRecord;
window.decryptRecord = decryptRecord;
window.verifyMerkleProof = verifyMerkleProof;
window.closeModal = closeModal;
window.renderDynamicFields = renderDynamicFields;
window.loadChainStatus = loadChainStatus;
window.enable2FA = enable2FA;
window.disable2FA = disable2FA;
window.setup2FA = setup2FA;
window.toggleNotifications = toggleNotifications;
window.clearAllNotifications = clearAllNotifications;
window.markAsRead = markAsRead;
window.markAllAsRead = markAllAsRead;
window.loadRecords = loadRecords;
window.loadDashboard = loadDashboard;
window.loadClients = loadClients;
window.loadAppointments = loadAppointments;
window.loadErasureRequests = loadErasureRequests;
window.loadSecurityAlerts = loadSecurityAlerts;
window.openClientInPlace = (pid) => openClient(pid, 'dashboard');
window.renderRecordCard = renderRecordCard;

/* -- Security Settings Page Loader -------------------------------- */
window.loadSecuritySettings = function() {
  const statusBox = document.getElementById('mfa-status-box');
  if (!statusBox) return;

  const currentUser = getCurrentUser();
  if (!currentUser) return;

  const setupSection = document.getElementById('mfa-setup-section');
  const disableSection = document.getElementById('mfa-disable-section');

  if (currentUser.totp_enabled) {
    statusBox.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px; padding:14px; border-radius:8px; background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.25);">
        <span style="font-size:20px;">✅</span>
        <div>
          <div style="font-weight:700; color:#10b981; font-size:14px;">Two-Factor Authentication is ENABLED</div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">Your account is protected with TOTP 2FA.</div>
        </div>
      </div>
    `;
    if (setupSection) setupSection.style.display = 'none';
    if (disableSection) disableSection.style.display = 'block';
  } else {
    statusBox.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px; padding:14px; border-radius:8px; background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.25);">
        <span style="font-size:20px;">⚠️</span>
        <div>
          <div style="font-weight:700; color:#f59e0b; font-size:14px;">Two-Factor Authentication is NOT enabled</div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">Enable 2FA to secure your PsikoNot account.</div>
        </div>
      </div>
      <button class="btn btn-gold" style="margin-top:16px;" data-action="setup-2fa">Setup 2FA Now</button>
    `;
    if (setupSection) setupSection.style.display = 'none';
    if (disableSection) disableSection.style.display = 'none';
  }

  // Clear any previous error/success messages
  const errEl = document.getElementById('security-error');
  const succEl = document.getElementById('security-success');
  if (errEl) errEl.style.display = 'none';
  if (succEl) succEl.style.display = 'none';

  // Set saved theme on selector
  const savedTheme = localStorage.getItem('vhv_theme') || 'default';
  const sel = document.getElementById('theme-selector');
  if (sel) sel.value = savedTheme;
};

// Attachment downloads
window.downloadBase64File = downloadBase64File;
window.downloadOffchainFile = downloadOffchainFile;

/* ── COMMAND PALETTE LOGIC ───────────────────────────────── */
let commandPaletteSelectedIdx = 0;
let commandPaletteVisibleResults = [];

window.openCommandPalette = async function() {
  const pal = document.getElementById('command-palette');
  if (!pal) return;
  pal.classList.add('open');
  const inp = document.getElementById('command-palette-input');
  if (inp) {
    inp.value = '';
    inp.focus();
  }
  commandPaletteSelectedIdx = 0;
  renderCommandPaletteResults('');

  // Fetch records if empty to ensure search is populated
  if (!allRecords || allRecords.length === 0) {
    try {
      await loadRecords();
      renderCommandPaletteResults(inp ? inp.value : '');
    } catch (e) {
      console.error("Failed to pre-fetch records for command palette:", e);
    }
  }
};

window.closeCommandPalette = function() {
  const pal = document.getElementById('command-palette');
  if (!pal) return;
  pal.classList.remove('open');
};

function renderCommandPaletteResults(query = '') {
  const resultsContainer = document.getElementById('command-palette-results');
  if (!resultsContainer) return;
  
  query = query.trim().toLowerCase();
  
  const pages = [
    { type: 'nav', page: 'dashboard', title: 'Dashboard Overview', desc: 'System status, recent records, and chain activity', shortcut: 'G D' },
    { type: 'nav', page: 'records', title: 'Client Records', desc: 'Browse and decrypt records on the chain', shortcut: 'G R' },
    { type: 'nav', page: 'add-record', title: 'Add Record', desc: 'Write a session note, client profile or transcript to the chain', shortcut: 'G N' },
    { type: 'nav', page: 'chain-status', title: 'Chain Status Verification', desc: 'Verify cryptographic block structures', shortcut: 'G C' },
    { type: 'nav', page: 'clients', title: 'Clients', desc: 'Client cards, contact details and the tally', shortcut: 'G K' },
    { type: 'nav', page: 'security', title: 'Security & 2FA', desc: 'Manage Multi-Factor Authentication', shortcut: 'G A' }
  ];

  const currentUser = getCurrentUser();
  if (currentUser && currentUser.role === 'admin') {
    pages.push(
      { type: 'nav', page: 'audit', title: 'Access & Audit History', desc: 'Comprehensive audit logs for all access (Admin)', shortcut: 'G L' },
      { type: 'nav', page: 'users', title: 'User Management', desc: 'Accounts and their roles (Admin)', shortcut: 'G U' }
    );
  }

  const actions = [
    { type: 'action', action: 'logout', title: 'Sign Out / Logout', desc: 'Terminate session and clear token', shortcut: '⌥ L' },
    { type: 'action', action: 'refresh_chain', title: 'Refresh Chain Status', desc: 'Query and update cryptographic statuses', shortcut: '⌥ R' }
  ];

  let filteredItems = [];

  const allCommands = [...pages, ...actions];
  const matchedCommands = allCommands.filter(item => 
    item.title.toLowerCase().includes(query) || 
    item.desc.toLowerCase().includes(query)
  );
  
  if (matchedCommands.length > 0) {
    filteredItems.push({
      group: 'Commands & Navigation',
      items: matchedCommands
    });
  }

  if (allRecords && allRecords.length > 0) {
    const matchedRecords = allRecords.filter(r => {
      const txt = ((r.title||'') + ' ' + (r.doctor_name||'') + ' ' + (r.institution||'') + ' ' + (r.record_type||'')).toLowerCase();
      return txt.includes(query);
    }).map(r => ({
      type: 'record',
      block_index: r.block_index,
      title: r.title,
      desc: `Block #${r.block_index} · ${escapeHtml(r.doctor_name || 'System')} · ${escapeHtml(r.record_date || 'Date N/A')}`,
      shortcut: `#${r.block_index}`
    }));

    if (matchedRecords.length > 0) {
      filteredItems.push({
        group: 'Client Records',
        items: query ? matchedRecords : matchedRecords.slice(0, 5)
      });
    }
  }

  commandPaletteVisibleResults = [];
  filteredItems.forEach(group => {
    group.items.forEach(item => {
      commandPaletteVisibleResults.push(item);
    });
  });

  if (commandPaletteVisibleResults.length === 0) {
    resultsContainer.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--muted); font-size: 13px;">No results found for "${escapeHtml(query)}"</div>`;
    return;
  }

  let html = '';
  let globalIndex = 0;
  filteredItems.forEach(group => {
    html += `<div class="command-palette-group-title">${group.group}</div>`;
    group.items.forEach(item => {
      const isSelected = globalIndex === commandPaletteSelectedIdx;
      html += `
        <div class="command-palette-item ${isSelected ? 'selected' : ''}" data-index="${globalIndex}" data-action="command-palette-item" data-arg="${globalIndex}">
          <div class="command-palette-item-icon">
            ${item.type === 'nav' ? '🧭' : item.type === 'action' ? '⚡' : '📄'}
          </div>
          <div class="command-palette-item-content">
            <div class="command-palette-item-title">${escapeHtml(item.title)}</div>
            <div class="command-palette-item-desc">${escapeHtml(item.desc)}</div>
          </div>
          ${item.shortcut ? `<span class="command-palette-item-shortcut">${item.shortcut}</span>` : ''}
        </div>
      `;
      globalIndex++;
    });
  });

  resultsContainer.innerHTML = html;
  
  const selectedEl = resultsContainer.querySelector('.command-palette-item.selected');
  if (selectedEl) {
    selectedEl.scrollIntoView({ block: 'nearest' });
  }
}

window.triggerCommandPaletteItem = function(index) {
  const item = commandPaletteVisibleResults[index];
  if (!item) return;
  
  window.closeCommandPalette();
  
  if (item.type === 'nav') {
    navigate(item.page);
  } else if (item.type === 'action') {
    if (item.action === 'logout') {
      logout();
    } else if (item.action === 'refresh_chain') {
      loadChainStatus();
    }
  } else if (item.type === 'record') {
    window.openRecord(item.block_index);
  }
};

function initCommandPaletteListeners() {
  const inp = document.getElementById('command-palette-input');
  if (!inp) return;
  
  inp.addEventListener('input', (e) => {
    commandPaletteSelectedIdx = 0;
    renderCommandPaletteResults(e.target.value);
  });
  
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (commandPaletteVisibleResults.length > 0) {
        commandPaletteSelectedIdx = (commandPaletteSelectedIdx + 1) % commandPaletteVisibleResults.length;
        renderCommandPaletteResults(inp.value);
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (commandPaletteVisibleResults.length > 0) {
        commandPaletteSelectedIdx = (commandPaletteSelectedIdx - 1 + commandPaletteVisibleResults.length) % commandPaletteVisibleResults.length;
        renderCommandPaletteResults(inp.value);
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (commandPaletteVisibleResults.length > 0) {
        window.triggerCommandPaletteItem(commandPaletteSelectedIdx);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      window.closeCommandPalette();
    }
  });
}

// Global hotkey cmd+k / ctrl+k listener
window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    const pal = document.getElementById('command-palette');
    if (pal && pal.classList.contains('open')) {
      window.closeCommandPalette();
    } else {
      window.openCommandPalette();
    }
  }
});


/* ── BLOCKCHAIN EXPLORER LOGIC ───────────────────────────── */
window.toggleBlockchainExplorer = async function() {
  const sidebar = document.getElementById('blockchain-explorer-sidebar');
  if (!sidebar) return;
  
  const isOpen = sidebar.classList.toggle('open');
  if (isOpen) {
    await window.loadBlockchainExplorerData();
  }
};

window.loadBlockchainExplorerData = async function() {
  const listContainer = document.getElementById('explorer-blocks-list');
  const statusText = document.getElementById('explorer-status-text');
  const dot = document.getElementById('explorer-chain-dot');
  
  if (!listContainer) return;
  listContainer.innerHTML = '<div class="loading-spinner">Loading blocks...</div>';
  
  try {
    const pid = patientId();
    if (!pid) {
      listContainer.innerHTML = '<div class="alert alert-error">No active client ID found.</div>';
      return;
    }
    
    const [status, records] = await Promise.all([
      apiFetch(`/api/blockchain/${pid}/status`),
      apiFetch(`/api/records/${pid}`)
    ]);
    
    const valid = status.is_valid;
    const anchorVerified = status.anchor_verified;
    const onChainPill = anchorVerified
      ? `<span class="badge badge-shared" style="background:rgba(16,185,129,0.1);color:#10b981;border:1px solid rgba(16,185,129,0.3);cursor:pointer;font-size:9px;padding:2px 6px;" data-action="copy-tx" data-arg="${escapeHtml(status.anchor_signature || '')}" title="Click to copy the anchor signature (HMAC-SHA256 of the Merkle root)">Local Signed Anchor</span>`
      : `<span class="badge badge-private" style="background:rgba(239,68,68,0.1);color:#ef4444;border:1px solid rgba(239,68,68,0.3);font-size:9px;padding:2px 6px;">Not Anchored</span>`;

    const statusBox = document.querySelector('.explorer-sidebar-status');
    if (statusBox) {
      statusBox.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; width:100%;">
          <div class="explorer-status-pill" style="display:flex; align-items:center; gap:8px;">
            <span class="chain-dot ${valid ? 'valid' : 'invalid'}"></span>
            <span id="explorer-status-text" style="color:${valid ? 'var(--success)' : 'var(--danger)'}; font-size:11px;">
              ${valid ? `Chain Secured (${status.chain_length} Blocks)` : `CHAIN CORRUPTED`}
            </span>
          </div>
          ${onChainPill}
        </div>
      `;
    }
    
    const blocks = records.records || [];
    if (blocks.length === 0) {
      listContainer.innerHTML = '<div class="empty-state"><p>No blocks found for this client.</p></div>';
      return;
    }
    
    // Display in reverse order (newest blocks first)
    const sortedBlocks = [...blocks].reverse();
    
    listContainer.innerHTML = sortedBlocks.map(b => {
      const isGenesis = b.block_index === 0;
      const isAudit = b.record_type === 'audit' || b.title?.includes('AUDIT') || b.title?.includes('ACCESS');
      const typeLabel = isGenesis ? 'Genesis' : (isAudit ? 'Audit' : 'Data');
      const typeClass = isGenesis ? 'genesis' : (isAudit ? 'audit' : 'data');
      const broken = status.broken_at !== null && b.block_index >= status.broken_at;
      
      return `
        <div class="explorer-block-card ${broken ? 'broken-block' : `${typeClass}-block`}">
          <div class="explorer-blk-title">
            <span>BLOCK #${b.block_index}</span>
            <span class="explorer-blk-type explorer-type-${typeClass}">${typeLabel}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Title:</span>
            <span class="explorer-blk-val" style="font-weight:600">${escapeHtml(b.title || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Timestamp:</span>
            <span class="explorer-blk-val">${formatTs(b.timestamp)}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Hash:</span>
            <span class="explorer-blk-val hash">${escapeHtml(b.hash_preview || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Prev Hash:</span>
            <span class="explorer-blk-val">${escapeHtml(b.prev_hash_preview || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Merkle Root:</span>
            <span class="explorer-blk-val">${escapeHtml(b.merkle_root_preview || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Signature:</span>
            <span class="explorer-blk-val">${escapeHtml(b.signature_preview || '—')}</span>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    listContainer.innerHTML = `<div class="alert alert-error">Error loading explorer: ${escapeHtml(err.message)}</div>`;
  }
};

/* ── THEME SWITCHER LOGIC ─────────────────────────────────── */
window.switchTheme = function(themeName) {
  document.body.classList.remove('theme-slate', 'theme-emerald', 'theme-steel');
  if (themeName && themeName !== 'default') {
    document.body.classList.add(`theme-${themeName}`);
  }
  localStorage.setItem('vhv_theme', themeName || 'default');
  const sel = document.getElementById('theme-selector');
  if (sel) sel.value = themeName || 'default';
};

(function initTheme() {
  const savedTheme = localStorage.getItem('vhv_theme') || 'default';
  window.switchTheme(savedTheme);
})();

/* -- Session expiry: bounce back to the login screen ----------------- */
window.addEventListener('vhv:session-expired', (e) => {
  const pageApp = document.getElementById('page-app');
  if (pageApp) {
    pageApp.style.display = 'none';
    pageApp.classList.remove('active');
  }
  const pageLogin = document.getElementById('page-login');
  if (pageLogin) {
    pageLogin.style.display = 'block';
    pageLogin.classList.add('active');
  }

  closeModal();
  window.closeCommandPalette();
  resetLoginFormState();
  updateNotificationsUI();

  const err = document.getElementById('login-error');
  if (err) {
    err.textContent = e.detail || 'Your session has expired. Please sign in again.';
    err.style.display = 'block';
  }
});


/* ── ACTION REGISTRY ──────────────────────────────────────────
 * Every interactive element in the markup declares a data-action; these are the
 * handlers behind those declarations. Because nothing is bound with an inline
 * attribute, the CSP can refuse inline script entirely.
 */
const arg  = (el) => el.dataset.arg;
const arg2 = (el) => el.dataset.arg2;

registerActions('click', {
  'noop': () => {},

  // navigation and session
  'navigate':              (el) => navigate(arg(el)),
  'logout':                () => logout(),
  'reset-login':           (el, e) => resetLoginForm(e),
  'passkey-login':         () => loginWithPasskey(),
  'register-passkey':      () => registerPasskey(),
  'fill-credentials':      (el) => fillCreds(arg(el), arg2(el)),
  'show-redeem':           (el, e) => { e.preventDefault(); showRedeem(); },
  'show-login':            (el, e) => { e.preventDefault(); showLogin(); },

  // clients
  'open-client':           (el) => openClient(arg(el), arg2(el) || 'records'),
  'client-edit':           (el) => editClient(arg(el)),
  'client-edit-cancel':    () => cancelEdit(),
  'client-export':         (el) => exportClient(arg(el)),
  'client-erasure':        (el) => requestErasure(arg(el)),

  // appointments
  'appt-status':           (el) => setAppointmentStatus(arg(el), arg2(el)),
  'appt-move':             (el) => startMove(arg(el)),
  'appt-move-save':        (el) => saveMove(arg(el)),
  'appt-move-cancel':      () => cancelMove(),

  // KVKK
  'kvkk-erase':            (el) => eraseClient(arg(el)),
  'kvkk-close':            (el) => closeErasureRequest(arg(el), arg2(el)),
  'ack-alert':             (el) => acknowledgeAlert(arg(el)),
  'copy-field':            (el) => copyField(arg(el)),

  // records
  'open-record':           (el) => openRecord(Number(arg(el))),
  'decrypt-record':        (el) => decryptRecord(Number(arg(el))),
  'verify-proof':          (el) => verifyMerkleProof(Number(arg(el))),
  'view-original':         (el) => viewOriginalVersion(Number(arg(el))),
  'correct-record':        (el) => renderCorrectionForm(Number(arg(el))),
  'submit-correction':     (el) => submitCorrection(Number(arg(el))),
  'close-modal':           () => closeModal(),
  'choose-file':           () => document.getElementById('rec-file')?.click(),
  'download-attachment':   (el) => {
    const p = takePayload(arg(el)) || {};
    downloadBase64File(p.fileName, p.fileType, p.fileData);
  },
  'download-offchain':     (el) => {
    const p = takePayload(arg(el)) || {};
    downloadOffchainFile(p.patientId, p.blockIndex, p.password, p.fileName);
  },

  // chain
  'refresh-chain':         () => loadChainStatus(),
  'toggle-explorer':       () => window.toggleBlockchainExplorer(),
  'copy-tx':               (el) => {
    const hash = arg(el);
    if (!hash) return;
    navigator.clipboard?.writeText(hash);
    addNotification('Transaction Hash Copied', hash, 'info');
  },

  // dual control
  'dual-control-refresh':  () => window.refreshDualControlStatus(),
  'dual-control-discard':  () => window.clearDualControlToken(),

  // security settings
  'setup-2fa':             () => setup2FA(),
  'enable-2fa':            () => enable2FA(),
  'disable-2fa':           () => disable2FA(),

  // logs
  'refresh-logs':          () => window.refreshLogPage(),
  'switch-log-tab':        (el) => window.switchLogTab(arg(el)),

  // notifications
  'toggle-notifications':  (el, e) => toggleNotifications(e),
  'clear-notifications':   (el, e) => clearAllNotifications(e),
  'notification-read':     (el) => markAsRead(arg(el)),

  // command palette
  'close-command-palette': () => window.closeCommandPalette(),
  'command-palette-item':  (el) => window.triggerCommandPaletteItem(Number(arg(el))),
});

registerActions('change', {
  'filter-records':        () => filterRecords(),
  'render-dynamic-fields': () => renderDynamicFields(),
  'switch-theme':          (el) => window.switchTheme(el.value),
  'file-selected':         (el) => {
    const label = document.getElementById('file-name-label');
    if (label) label.textContent = el.files.length ? el.files[0].name : 'No file chosen';
  },
});

registerActions('input', {
  'filter-records': () => filterRecords(),
});

registerActions('submit', {
  'save-client':          (el, e) => saveClient(e),
  'invite-secretary':     (el, e) => inviteSecretary(e),
  'book-appointment':     (el, e) => bookAppointment(e),
  'redeem-invite':        (el, e) => redeemInvite(e),
  'dual-control-request': (el, e) => window.requestDualControl(e),
  'dual-control-cosign':  (el, e) => window.coSignDualControl(e),
  'select-patient':       (el, e) => window.selectPatient(e),
});

// Operators choose which client's file to load. Validated to the
// CL-### shape, then the current page is reloaded under the new patient context.
window.selectPatient = function(e) {
  if (e && e.preventDefault) e.preventDefault();
  const input = document.getElementById('patient-selector-input');
  const val = ((input && input.value) || '').trim().toUpperCase();
  if (!/^CL-[0-9]{3,}$/.test(val)) {
    if (input) input.style.borderColor = '#ef4444';
    return;
  }
  if (input) { input.value = val; input.style.borderColor = 'rgba(255,255,255,0.15)'; }
  setSelectedPatient(val);
  const recPatId = document.getElementById('rec-patient-id');
  if (recPatId) recPatId.value = val;
  navigate((appState && appState.activePage) || 'dashboard');
};

// Auto-run on load
const currentUser = getCurrentUser();

// Show the demo ribbon on every page when the server is in demo mode, so a
// public viewer always knows the data is synthetic.
apiFetch('/api/config')
  .then(cfg => {
    const banner = document.getElementById('demo-banner');
    if (banner && cfg && cfg.demo_mode) banner.hidden = false;
  })
  .catch(() => {});

initActionDispatch();
initAuthListeners();
initRecordsListeners();
initCommandPaletteListeners();

// A new practitioner or secretary opening their invitation link lands on the redeem form.
if (!currentUser) checkInviteLink();

if (currentUser) {
  // The session token is an httpOnly cookie the JS can't see, so we confirm it
  // with the server before showing the vault: /me succeeds only if the cookie is
  // still valid (a stale cookie from an earlier server run is rejected on every
  // call, which used to leave the UI looking signed in while nothing loaded).
  // This also refreshes the cached profile (role, 2FA state) from the server.
  apiFetch('/api/auth/me')
    .then(user => {
      setCurrentUser(user);
      window.enterApp();
    })
    .catch(() => {
      // A 401 already cleared the session and raised vhv:session-expired.
      resetLoginFormState();
    });
} else {
  resetLoginFormState();
}

checkEnvironment();
