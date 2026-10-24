import { API, apiFetch, patientId, setSelectedPatient, getSelectedPatient, formatTs, formatTsFull, emptyState, ROLE_LABEL, escapeHtml, actionLabel, recordTitle, getCurrentUser, setCurrentUser, getDualControlToken, setDualControlToken, appState } from './modules/utils.js';
import { mfaRequired, resetLoginFormState, resetLoginForm, fillCreds, handleLoginSubmit, logout, setup2FA, enable2FA, disable2FA, initAuthListeners, loginWithPasskey, registerPasskey } from './modules/auth.js';
import { updateChainPill, loadDashboard, navigate } from './modules/dashboard.js';
import { allRecords, recordTypes, loadRecordTypes, loadRecords, filterRecords, renderAllRecords, renderRecordCard, renderAttachmentHtml, downloadBase64File, downloadOffchainFile, openRecord, decryptRecord, verifyMerkleProof, viewOriginalVersion, renderCorrectionForm, submitCorrection, closeModal, DYNAMIC_FIELDS, renderDynamicFields, initRecordsListeners } from './modules/records.js';
import { getNotifications, addNotification, updateNotificationsUI, toggleNotifications, closeAllDropdowns, markAsRead, markAllAsRead, clearAllNotifications } from './modules/notifications.js';
import { loadChainStatus } from './modules/blockchain.js';
import { registerActions, initActionDispatch, takePayload } from './modules/actions.js';
import { translateApiMessage } from './modules/messages.js';
import { exportClient, requestErasure, loadErasureRequests, eraseClient, closeErasureRequest, loadSecurityAlerts, acknowledgeAlert } from './modules/kvkk.js';
import { loadAppointments, setAppointmentStatus, startMove, saveMove, cancelMove, bookAppointment } from './modules/appointments.js';
import { toggleRoomField, showRoomDay } from './modules/appointments.js';
// Weekly series and the week calendar
import { toggleRepeatFields, confirmSeries, closeSeriesPreview, cancelFollowing } from './modules/appointments.js';
import { startMoveFollowing, previewMoveFollowing, confirmMoveFollowing } from './modules/appointments.js';
import { loadCalendar, fetchWeek, changeWeek, shiftWeek, shiftDayShown, changeMode, changeHours, openSlot } from './modules/calendar.js';
import { loadRooms, createRoom, startRename, saveRename, cancelRename, setRoomActive } from './modules/rooms.js';
import { showOperatorRoomDay, shiftOperatorRoomDay, openOperatorRoomDay } from './modules/rooms.js';
import { loadClients, loadStaff, saveClient, editClient, cancelEdit, inviteSecretary, unlinkSecretary, copyField, openClient, showRedeem, showLogin, redeemInvite, checkInviteLink } from './modules/clients.js';
import { chooseBook, loadDay, shiftDay } from './modules/frontdesk.js';
import { loadStaffLinks, linkStaff, unlinkStaff } from './modules/stafflinks.js';
import { changePassword, accountButtons, setAccountEnabled, issueResetCode } from './modules/accounts.js';
// Intake: applications and the waitlist
import { loadApplications, refreshApplicationsBadge, filterApplications, openApplication, setApplicationStatus, addContactAttempt, startDecline, confirmDecline, assignApplication, togglePriority, convertApplication, bookFromApplication, createApplication } from './modules/applications.js';
import { toggleApplicationRoom, markApplicationScheduled } from './modules/applications.js';

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
  addNotification('Giriş yapıldı', `${currentUser.username} kullanıcısı giriş yaptı. Cihaz doğrulandı.`, 'success');

  if (options.passkeyRequired) {
    addNotification('Geçiş anahtarı gerekli', 'Bu sistem geçiş anahtarı (passkey) gerektirir. Devam etmeden önce bu sayfada bir tane kaydedin.', 'warning');
  }
  // The front desk starts on its day; everyone else on the dashboard.
  const landing = options.passkeyRequired ? 'security' : (isSecretary ? 'day' : 'dashboard');
  loadRecordTypes().then(() => navigate(landing));

  // Intake: the count of new applications on the menu.
  refreshApplicationsBadge();
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

// What an approved token unlocks, by request type (constant strings).
const DC_APPROVED_LABEL = {
  DECRYPT_RAW_RECORD: 'kayıtlara erişim açıldı',
  RESET_ACCOUNT: 'bu hesap için bir sıfırlama kodu verebilirsiniz',
  ENABLE_ACCOUNT: 'bu hesabı bir kez yeniden etkinleştirebilirsiniz',
};

window.renderDualControlToken = function() {
  const box = document.getElementById('dc-active-token');
  if (!box) return;

  const token = getDualControlToken();
  if (!token) {
    box.innerHTML = `
      <div class="glass" style="padding:16px; border-radius:var(--radius); border:1px solid var(--border);">
        <div style="font-size:13px; color:var(--muted);">Elinizde onaylanmış bir çift onay izni yok. İkinci bir yetkili bir talebi onaylayana kadar danışan kayıtları kilitli kalır.</div>
      </div>`;
    return;
  }

  const approved = token.status === 'APPROVED';
  // A constant from the table above, whatever the stored request type says.
  const approvedLabel = DC_APPROVED_LABEL[token.request_type] || DC_APPROVED_LABEL.DECRYPT_RAW_RECORD;
  const expiresIn = token.expires_at ? Math.max(0, Math.round((token.expires_at * 1000 - Date.now()) / 60000)) : null;
  box.innerHTML = `
    <div class="glass" style="padding:16px 20px; border-radius:var(--radius); border:1px solid ${approved ? 'rgba(16,185,129,0.35)' : 'rgba(245,158,11,0.35)'};">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap;">
        <div>
          <div style="font-weight:700; font-size:14px; color:${approved ? '#10b981' : '#f59e0b'};">
            ${approved ? `ONAYLANDI — ${approvedLabel}` : 'İKİNCİ ONAY BEKLENİYOR'}
          </div>
          <div style="font-size:12px; color:var(--muted-hi); margin-top:4px;">
            Hedef <strong>${escapeHtml(token.target_patient_id || '—')}</strong> ·
            Talep <code style="font-family:var(--font-mono);">${escapeHtml(token.token_id)}</code>
            ${expiresIn !== null ? ` · ${expiresIn} dk içinde sona erer` : ''}
          </div>
          ${token.co_signed_by ? `<div style="font-size:12px; color:var(--muted); margin-top:2px;">Onaylayan: <strong>${escapeHtml(token.co_signed_by)}</strong></div>` : ''}
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-ghost btn-sm" data-action="dual-control-refresh">Durumu yenile</button>
          <button class="btn btn-ghost btn-sm" data-action="dual-control-discard">İzni bırak</button>
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
      request_type: info.request_type,
      status: info.status,
      target_patient_id: info.target_patient_id,
      expires_at: info.expires_at,
      co_signed_by: info.co_signed_by
    });
    window.renderDualControlToken();
    if (info.status === 'APPROVED') {
      addNotification('Çift onay verildi', `${info.token_id} numaralı talep ${info.co_signed_by} tarafından onaylandı.`, 'success');
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
  addNotification('Çift onay izni bırakıldı', 'Kayıtlara yetkili erişim sona erdi.', 'info');
};

window.requestDualControl = async function(event) {
  if (event) event.preventDefault();
  const errEl = document.getElementById('dc-error');
  const succEl = document.getElementById('dc-success');
  errEl.style.display = 'none';
  succEl.style.display = 'none';

  // Record access names a client ID; an account request names a username.
  const requestType = document.getElementById('dc-request-type').value;
  try {
    const res = await apiFetch('/api/security/dual-control/request', {
      method: 'POST',
      body: JSON.stringify({
        request_type: requestType,
        target_patient_id: document.getElementById('dc-patient-id').value.trim(),
        reason: document.getElementById('dc-reason').value.trim(),
        validity_minutes: parseInt(document.getElementById('dc-validity').value || 30)
      })
    });

    setDualControlToken({
      token_id: res.token_id,
      request_type: requestType,
      status: res.status,
      target_patient_id: document.getElementById('dc-patient-id').value.trim(),
      expires_at: res.expires_at
    });
    window.renderDualControlToken();

    succEl.textContent = `${translateApiMessage(res.message)} Talep numarası: ${res.token_id}`;
    succEl.style.display = 'block';
    document.getElementById('dc-cosign-token').value = res.token_id;
    addNotification('Çift onay istendi', `${document.getElementById('dc-patient-id').value.trim()} için erişim talebi oluşturuldu. İkinci onay bekleniyor.`, 'warning');
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

    succEl.textContent = translateApiMessage(res.message);
    succEl.style.display = 'block';
    addNotification('Çift onay verildi', `${res.token_id} numaralı talep ${res.target_patient_id} için onaylandı.`, 'success');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
};

// The server's account states, as the user list shows them.
const ACCOUNT_STATUS_LABEL = {
  ACTIVE_ENROLLED: 'etkin',
  PENDING_ONBOARDING: 'davet bekliyor',
  DISABLED: 'devre dışı',
};

// The demo accounts' role codes from /api/config, as the sign-in screen shows them.
const DEMO_ROLE_LABEL = {
  ADMIN: 'Yönetici',
  PRACTITIONER: 'Psikolog',
  SECRETARY: 'Sekreter',
  SECOFF: 'Güvenlik sorumlusu',
};

// Clients no longer sign in, and nobody manages their own account from here.
function userActions(u, me) {
  if (u.role === 'client' || u.username === me) return '';
  const status = { ACTIVE_ENROLLED: 'active', DISABLED: 'disabled' }[u.account_status] || 'pending';
  return accountButtons(u.username, status);
}

window.loadUsers = async function() {
  const container = document.getElementById('users-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await apiFetch('/api/admin/users');
    const me = (getCurrentUser() || {}).username;
    container.innerHTML = d.users.map(u => `
      <div class="user-card glass" style="flex-wrap:wrap">
        <div class="user-avatar" style="background:linear-gradient(135deg,#C9A84C,#8B6914)">${escapeHtml(u.full_name.charAt(0))}</div>
        <div style="flex:1; min-width:200px">
          <div style="font-weight:600">${escapeHtml(u.full_name)}</div>
          <div style="font-size:12px;color:var(--muted)">@${escapeHtml(u.username)} · ${escapeHtml(ACCOUNT_STATUS_LABEL[u.account_status] || u.account_status || '')}</div>
        </div>
        <span class="role-badge badge-${escapeHtml(ROLE_LABEL[u.role] ? u.role : 'client')}">${escapeHtml(ROLE_LABEL[u.role]||u.role)}</span>
        <div class="appt-actions">${userActions(u, me)}</div>
      </div>`
    ).join('');
  } catch(e) { container.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`; }
};

window.loadAuditLog = async function() {
  const container = document.getElementById('audit-list');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const pid = patientId();
    const d = await apiFetch(`/api/blockchain/${pid}/audit?limit=100`);
    if (!d.logs || d.logs.length === 0) {
      container.innerHTML = emptyState('Henüz denetim kaydı yok.');
      return;
    }
    container.innerHTML = d.logs.map(log => {
      const isAlert = log.action.includes('FAILED') || log.action.includes('REVOKE');
      const statusLabel = isAlert ? 'UYARI' : 'TAMAM';
      return `
      <div class="record-card ${isAlert ? 'is-encrypted' : ''}" style="cursor:default">
        <div class="record-type-icon record-type-text">${escapeHtml(log.action.substring(0,3))}</div>
        <div class="record-main">
          <div class="record-title">${escapeHtml(actionLabel(log.action))}</div>
          <div class="record-meta">Kullanıcı: <strong>${escapeHtml(log.username)}</strong> · Blok: ${log.block_index !== null ? '#'+log.block_index : '—'}</div>
          <div class="record-meta" style="font-family:monospace;font-size:11px;color:var(--muted)">Cihaz: ${escapeHtml((log.device_id||'?').substring(0,16))}...</div>
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
  container.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  const pid = patientId();
  if (!pid) {
    container.innerHTML = emptyState('Danışan seçilmedi. Genel bakış sayfasından bir danışan seçin.');
    return;
  }
  try {
    const d = await apiFetch(`/api/blockchain/${pid}/access-logs?limit=100`);
    if (!d.logs || d.logs.length === 0) {
      container.innerHTML = emptyState('Henüz erişim kaydı yok.');
      return;
    }
    container.innerHTML = d.logs.map(log => {
      const isAlert = log.action.includes('FAILED') || log.action.includes('REVOKE');
      const statusLabel = isAlert ? 'UYARI' : 'ERİŞİM';
      return `
      <div class="record-card ${isAlert ? 'is-encrypted' : ''}" style="cursor:default">
        <div class="record-type-icon record-type-text">${escapeHtml(log.action.substring(0,3))}</div>
        <div class="record-main">
          <div class="record-title">${escapeHtml(actionLabel(log.action))}</div>
          <div class="record-meta">Kullanıcı: <strong>${escapeHtml(log.username)}</strong> · Blok: ${log.block_index !== undefined && log.block_index !== null ? '#'+log.block_index : '—'}</div>
          <div class="record-meta" style="font-family:monospace;font-size:11px;color:var(--muted)">Cihaz: ${escapeHtml((log.device_id||'?').substring(0,16))}...</div>
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
  container.innerHTML = '<div class="loading-spinner">Erişim kaydı yükleniyor...</div>';
  if (banner) banner.innerHTML = '';

  const pid = patientId();
  if (!pid) {
    container.innerHTML = emptyState('Danışan seçilmedi. Genel bakış sayfasından bir danışan seçin.');
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
            <strong>${intact ? 'Erişim kaydı sağlam' : 'Erişim kaydı DEĞİŞTİRİLMİŞ'}</strong>
            ${d.integrity ? ` — ${d.integrity.count} giriş, hash ile bağlı${intact ? '' : `; #${d.integrity.broken_at} numaralı girişte bozulmuş`}` : ''}
          </div>
        </div>`;
    }

    if (!d.logs || d.logs.length === 0) {
      container.innerHTML = emptyState('Bu dosyayı henüz kimse açmadı.');
      return;
    }

    container.innerHTML = d.logs.map(log => {
      const action = log.action || '';
      const isAlert = action.includes('FAILED') || action.includes('REVOKE');
      const isRead = action.includes('READ') || action.includes('DECRYPTED') || action.includes('VIEWED');
      const label = isAlert ? 'UYARI' : (isRead ? 'OKUMA' : 'OLAY');
      return `
      <div class="record-card ${isAlert ? 'is-encrypted' : ''}" style="cursor:default">
        <div class="record-type-icon record-type-text">${escapeHtml(action.substring(0,3))}</div>
        <div class="record-main">
          <div class="record-title">${escapeHtml(actionLabel(action))}</div>
          <div class="record-meta">Kişi: <strong>${escapeHtml(log.username || 'bilinmiyor')}</strong>${log.block_index !== undefined && log.block_index !== null ? ' · Kayıt #' + escapeHtml(String(log.block_index)) : ''}</div>
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
              <span class="role-badge badge-${escapeHtml(acc.role.toLowerCase())}">${escapeHtml(DEMO_ROLE_LABEL[acc.role] || acc.role)}</span>
              <span class="demo-creds">${escapeHtml(acc.username)} / ${escapeHtml(acc.password)}</span>
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
window.loadDay = loadDay;
window.loadApplications = loadApplications;
window.loadCalendar = loadCalendar;
window.loadRooms = loadRooms;
window.loadStaffLinks = loadStaffLinks;
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
          <div style="font-weight:700; color:#10b981; font-size:14px;">İki adımlı doğrulama AÇIK</div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">Hesabınız iki adımlı doğrulama (TOTP) ile korunuyor.</div>
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
          <div style="font-weight:700; color:#f59e0b; font-size:14px;">İki adımlı doğrulama KAPALI</div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">PsikoNot hesabınızı korumak için iki adımlı doğrulamayı açın.</div>
        </div>
      </div>
      <button class="btn btn-gold" style="margin-top:16px;" data-action="setup-2fa">İki adımlı doğrulamayı kur</button>
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
  
  query = query.trim().toLocaleLowerCase('tr-TR');
  
  const pages = [
    { type: 'nav', page: 'dashboard', title: 'Genel bakış', desc: 'Sistem durumu, son kayıtlar ve zincir etkinliği', shortcut: 'G D' },
    { type: 'nav', page: 'records', title: 'Danışan kayıtları', desc: 'Zincirdeki kayıtlara göz atın ve şifreli kayıtları açın', shortcut: 'G R' },
    { type: 'nav', page: 'add-record', title: 'Kayıt ekle', desc: 'Zincire seans notu, danışan profili veya seans dökümü yazın', shortcut: 'G N' },
    { type: 'nav', page: 'chain-status', title: 'Zincir durumu', desc: 'Blokların kriptografik bütünlüğünü doğrulayın', shortcut: 'G C' },
    { type: 'nav', page: 'clients', title: 'Danışanlar', desc: 'Danışan kartları, iletişim bilgileri ve seans sayıları', shortcut: 'G K' },
    { type: 'nav', page: 'security', title: 'Ayarlar', desc: 'İki adımlı doğrulama, şifre ve tema', shortcut: 'G A' }
  ];

  const currentUser = getCurrentUser();
  if (currentUser && currentUser.role === 'admin') {
    pages.push(
      { type: 'nav', page: 'audit', title: 'Denetim kaydı', desc: 'Tüm erişimlerin denetim kaydı (yönetici)', shortcut: 'G L' },
      { type: 'nav', page: 'users', title: 'Kullanıcı yönetimi', desc: 'Hesaplar ve rolleri (yönetici)', shortcut: 'G U' }
    );
  }

  const actions = [
    { type: 'action', action: 'logout', title: 'Çıkış yap', desc: 'Oturumu kapatın', shortcut: '⌥ L' },
    { type: 'action', action: 'refresh_chain', title: 'Zincir durumunu yenile', desc: 'Zincirin bütünlüğünü yeniden denetleyin', shortcut: '⌥ R' }
  ];

  let filteredItems = [];

  const allCommands = [...pages, ...actions];
  const matchedCommands = allCommands.filter(item => 
    item.title.toLocaleLowerCase('tr-TR').includes(query) || 
    item.desc.toLocaleLowerCase('tr-TR').includes(query)
  );
  
  if (matchedCommands.length > 0) {
    filteredItems.push({
      group: 'Komutlar ve sayfalar',
      items: matchedCommands
    });
  }

  if (allRecords && allRecords.length > 0) {
    const matchedRecords = allRecords.filter(r => {
      const txt = ((r.title||'') + ' ' + (r.doctor_name||'') + ' ' + (r.institution||'') + ' ' + (r.record_type||'')).toLocaleLowerCase('tr-TR');
      return txt.includes(query);
    }).map(r => ({
      type: 'record',
      block_index: r.block_index,
      title: recordTitle(r.title),
      desc: `Blok #${r.block_index} · ${escapeHtml(r.doctor_name || 'Sistem')} · ${escapeHtml(r.record_date || 'Tarih yok')}`,
      shortcut: `#${r.block_index}`
    }));

    if (matchedRecords.length > 0) {
      filteredItems.push({
        group: 'Danışan kayıtları',
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
    resultsContainer.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--muted); font-size: 13px;">"${escapeHtml(query)}" için sonuç bulunamadı</div>`;
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
  listContainer.innerHTML = '<div class="loading-spinner">Bloklar yükleniyor...</div>';
  
  try {
    const pid = patientId();
    if (!pid) {
      listContainer.innerHTML = '<div class="alert alert-error">Etkin bir danışan seçilmedi.</div>';
      return;
    }
    
    const [status, records] = await Promise.all([
      apiFetch(`/api/blockchain/${pid}/status`),
      apiFetch(`/api/records/${pid}`)
    ]);
    
    const valid = status.is_valid;
    const anchorVerified = status.anchor_verified;
    const onChainPill = anchorVerified
      ? `<span class="badge badge-shared" style="background:rgba(16,185,129,0.1);color:#10b981;border:1px solid rgba(16,185,129,0.3);cursor:pointer;font-size:9px;padding:2px 6px;" data-action="copy-tx" data-arg="${escapeHtml(status.anchor_signature || '')}" title="Çapa imzasını kopyalamak için tıklayın (Merkle kökünün HMAC-SHA256 imzası)">İmzalı çapa</span>`
      : `<span class="badge badge-private" style="background:rgba(239,68,68,0.1);color:#ef4444;border:1px solid rgba(239,68,68,0.3);font-size:9px;padding:2px 6px;">Çapa yok</span>`;

    const statusBox = document.querySelector('.explorer-sidebar-status');
    if (statusBox) {
      statusBox.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; width:100%;">
          <div class="explorer-status-pill" style="display:flex; align-items:center; gap:8px;">
            <span class="chain-dot ${valid ? 'valid' : 'invalid'}"></span>
            <span id="explorer-status-text" style="color:${valid ? 'var(--success)' : 'var(--danger)'}; font-size:11px;">
              ${valid ? `Zincir doğrulandı (${status.chain_length} blok)` : `ZİNCİR BOZULMUŞ`}
            </span>
          </div>
          ${onChainPill}
        </div>
      `;
    }
    
    const blocks = records.records || [];
    if (blocks.length === 0) {
      listContainer.innerHTML = '<div class="empty-state"><p>Bu danışan için blok bulunamadı.</p></div>';
      return;
    }
    
    // Display in reverse order (newest blocks first)
    const sortedBlocks = [...blocks].reverse();
    
    listContainer.innerHTML = sortedBlocks.map(b => {
      const isGenesis = b.block_index === 0;
      const isAudit = b.record_type === 'audit' || b.title?.includes('AUDIT') || b.title?.includes('ACCESS');
      const typeLabel = isGenesis ? 'Başlangıç' : (isAudit ? 'Denetim' : 'Veri');
      const typeClass = isGenesis ? 'genesis' : (isAudit ? 'audit' : 'data');
      const broken = status.broken_at !== null && b.block_index >= status.broken_at;
      
      return `
        <div class="explorer-block-card ${broken ? 'broken-block' : `${typeClass}-block`}">
          <div class="explorer-blk-title">
            <span>BLOK #${b.block_index}</span>
            <span class="explorer-blk-type explorer-type-${typeClass}">${typeLabel}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Başlık:</span>
            <span class="explorer-blk-val" style="font-weight:600">${escapeHtml(recordTitle(b.title) || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Zaman:</span>
            <span class="explorer-blk-val">${formatTs(b.timestamp)}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Hash:</span>
            <span class="explorer-blk-val hash">${escapeHtml(b.hash_preview || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Önceki hash:</span>
            <span class="explorer-blk-val">${escapeHtml(b.prev_hash_preview || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">Merkle kökü:</span>
            <span class="explorer-blk-val">${escapeHtml(b.merkle_root_preview || '—')}</span>
          </div>
          <div class="explorer-blk-row">
            <span class="explorer-blk-lbl">İmza:</span>
            <span class="explorer-blk-val">${escapeHtml(b.signature_preview || '—')}</span>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    listContainer.innerHTML = `<div class="alert alert-error">Zincir gezgini yüklenemedi: ${escapeHtml(err.message)}</div>`;
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
    err.textContent = e.detail || 'Oturumunuzun süresi doldu. Lütfen yeniden giriş yapın.';
    err.style.display = 'block';
  }
});


// The account buttons sit on the practitioner's staff list or the admin's
// user list; the active page says which.
const accountsPanel = () => (appState && appState.activePage === 'users') ? 'users' : 'staff';
const reloadAccounts = () => (accountsPanel() === 'users' ? window.loadUsers() : loadStaff());

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
  'show-reset':            (el, e) => { e.preventDefault(); showRedeem('', 'reset'); },

  // accounts: the staff list (practitioner) and the user list (admin)
  'account-reset':         (el) => issueResetCode(arg(el), accountsPanel()),
  'account-disable':       (el) => setAccountEnabled(arg(el), false, accountsPanel(), reloadAccounts),
  'account-enable':        (el) => setAccountEnabled(arg(el), true, accountsPanel(), reloadAccounts),
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
  // weekly series and the week calendar
  'appt-series-confirm':   () => confirmSeries(),
  'appt-series-cancel':    () => closeSeriesPreview(),
  'appt-cancel-following': (el) => cancelFollowing(arg(el)),
  'appt-move-following':   (el) => startMoveFollowing(arg(el)),
  'appt-move-following-preview': (el) => previewMoveFollowing(arg(el)),
  'appt-move-following-confirm': (el) => confirmMoveFollowing(arg(el)),
  'cal-shift':             (el) => shiftWeek(arg(el)),
  'cal-day':               (el) => shiftDayShown(arg(el)),
  'cal-slot':              (el) => openSlot(arg(el), arg2(el)),

  // front desk: the day of every book, and the practitioner's staff
  'day-shift':             (el) => shiftDay(arg(el)),
  'staff-unlink':          (el) => unlinkSecretary(arg(el)),
  // staff links (admin, security officer)
  'staff-link-remove':     (el) => unlinkStaff(arg(el), arg2(el)),
  // rooms (admin, security officer)
  'room-rename':           (el) => startRename(arg(el)),
  'room-rename-save':      (el) => saveRename(arg(el)),
  'room-rename-cancel':    () => cancelRename(),
  'room-active':           (el) => setRoomActive(arg(el), arg2(el) === 'on'),
  'room-day-open':         (el) => openOperatorRoomDay(arg(el)),
  'rooms-day-shift':       (el) => shiftOperatorRoomDay(arg(el)),

  // intake: applications and the waitlist
  'application-open':            (el) => openApplication(arg(el)),
  'application-status':          (el) => setApplicationStatus(arg(el)),
  'application-contact':         (el) => addContactAttempt(arg(el)),
  'application-decline':         () => startDecline(),
  'application-decline-confirm': (el) => confirmDecline(arg(el)),
  'application-assign':          (el) => assignApplication(arg(el)),
  'application-priority':        (el) => togglePriority(arg(el)),
  'application-convert':         (el) => convertApplication(arg(el)),
  'application-scheduled':       (el) => markApplicationScheduled(arg(el)),

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
    addNotification('Çapa imzası kopyalandı', hash, 'info');
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
  // A secretary of several practitioners picks the book this page works on.
  'choose-book':           (el) => {
    chooseBook(el.value);
    if (el.id === 'clients-book') loadClients();
    else if (el.id === 'cal-book') fetchWeek();
    else loadAppointments();
  },
  'day-date':              () => loadDay(),
  'appt-format':           () => toggleRoomField(),
  // weekly series and the week calendar
  'appt-repeat':           () => toggleRepeatFields(),
  'cal-week':              () => changeWeek(),
  'cal-mode':              () => changeMode(),
  'cal-refresh':           () => fetchWeek(),
  'cal-hours':             () => changeHours(),
  'rooms-day-change':      () => showOperatorRoomDay(),
  'applications-filter':   () => filterApplications(),
  'application-book-format': () => toggleApplicationRoom(),
  'file-selected':         (el) => {
    const label = document.getElementById('file-name-label');
    if (label) label.textContent = el.files.length ? el.files[0].name : 'Dosya seçilmedi';
  },
});

registerActions('input', {
  'filter-records': () => filterRecords(),
});

registerActions('submit', {
  'save-client':          (el, e) => saveClient(e),
  'invite-secretary':     (el, e) => inviteSecretary(e),
  'staff-link':           (el, e) => linkStaff(e),
  'change-password':      (el, e) => changePassword(e),
  'book-appointment':     (el, e) => bookAppointment(e),
  'room-day':             (el, e) => showRoomDay(e),
  'rooms-day':            (el, e) => showOperatorRoomDay(e),
  'create-room':          (el, e) => createRoom(e),
  'redeem-invite':        (el, e) => redeemInvite(e),
  'dual-control-request': (el, e) => window.requestDualControl(e),
  'dual-control-cosign':  (el, e) => window.coSignDualControl(e),
  'select-patient':       (el, e) => window.selectPatient(e),
  // intake: applications and the waitlist
  'create-application':   (el, e) => createApplication(e),
  'application-book':     (el, e) => bookFromApplication(el, e),
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
