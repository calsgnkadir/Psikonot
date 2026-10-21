/* kvkk.js — KVKK screens
 *
 * Clients do not sign in, so their practitioner acts for them: downloads a copy
 * of the client's data to hand over (KVKK Art. 11) and files their erasure
 * request (Art. 17), both from the Clients page.
 * Operators (admin, KVKK officer): carry the erasure requests out with the
 * dual-control-gated crypto-shred, and see the security alerts.
 */
import { API, apiFetch, escapeHtml, emptyState, getCurrentUser, LOCALE } from './utils.js';
import { apiErrorMessage } from './messages.js';

function day(iso) {
  return iso ? new Date(iso).toLocaleString(LOCALE, {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  }) : '—';
}

function showMessage(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

/* -- A client's data export and erasure request (practitioner) ------ */

// The export is a file: fetched with the session cookie and saved from a blob.
export async function exportClient(patientId) {
  showMessage('client-form-error', '');
  try {
    const res = await fetch(`${API}/api/v1/kvkk/export/${encodeURIComponent(patientId)}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(apiErrorMessage((await res.json().catch(() => ({}))).detail, 'Veri dışa aktarılamadı.'));
    const blob = await res.blob();
    const match = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = match ? match[1] : 'psikonot-export.json';
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (e) {
    showMessage('client-form-error', e.message);
  }
}

export async function requestErasure(patientId) {
  showMessage('client-form-error', '');
  showMessage('client-form-success', '');
  if (!confirm(`${patientId} için silme talebi (unutulma hakkı) açılsın mı? Talebi bir operatör, ikinci bir kişinin onayıyla yerine getirir ve geri alınamaz. Bazı kayıtları saklamanız yasal olarak gerekebilir.`)) return;
  try {
    await apiFetch('/api/kvkk/erasure-requests', { method: 'POST', body: JSON.stringify({ patient_id: patientId }) });
    showMessage('client-form-success', `${patientId} için silme talebi açıldı. Talebi Silme talepleri sayfasından izleyebilirsiniz.`);
  } catch (e) {
    showMessage('client-form-error', e.message);
  }
}

/* -- Erasure requests (operators carry out; a practitioner sees theirs) */

export async function loadErasureRequests() {
  const list = document.getElementById('erasure-requests-list');
  if (!list) return;
  showMessage('erasure-error', '');
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await apiFetch('/api/kvkk/erasure-requests');
    list.innerHTML = d.requests.length ? d.requests.map(r => {
      const id = escapeHtml(r.id);
      const pid = escapeHtml(r.patient_id);
      const isOperator = ['admin', 'security_officer'].includes((getCurrentUser() || {}).role);
      const actions = r.status === 'open' && isOperator ? `
        <button type="button" class="btn btn-error btn-sm" data-action="kvkk-erase" data-arg="${pid}">Sil (çift onay)</button>
        <button type="button" class="btn btn-ghost btn-sm" data-action="kvkk-close" data-arg="${id}" data-arg2="done">Tamamlandı olarak işaretle</button>
        <button type="button" class="btn btn-ghost btn-sm" data-action="kvkk-close" data-arg="${id}" data-arg2="rejected">Reddet</button>` : '';
      return `
        <div class="record-card appt-row" style="cursor:default">
          <div class="record-main">
            <div class="record-title">${pid} · ${escapeHtml(REQUEST_STATUS[r.status] || r.status)}</div>
            <div class="record-meta">Talep: ${escapeHtml(day(r.requested_at))}, ${escapeHtml(r.requested_by)}${r.handled_by ? ` · kapatan: ${escapeHtml(r.handled_by)}` : ''}</div>
          </div>
          <div class="appt-actions">${actions}</div>
        </div>`;
    }).join('') : emptyState('Silme talebi yok.');
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

export async function eraseClient(patientId) {
  if (!confirm(`${patientId} danışanının verisi silinsin mi? Bu işlem danışanın şifreleme anahtarını yok eder: kayıtları kalıcı olarak okunamaz hale gelir. Bu danışan için geçerli bir çift onay izni gerekir.`)) return;
  try {
    await apiFetch(`/api/erasure/${encodeURIComponent(patientId)}`, { method: 'POST' });
    loadErasureRequests();
  } catch (e) {
    showMessage('erasure-error', e.message);
  }
}

export async function closeErasureRequest(requestId, status) {
  try {
    await apiFetch(`/api/kvkk/erasure-requests/${encodeURIComponent(requestId)}/${encodeURIComponent(status)}`,
                   { method: 'POST' });
    loadErasureRequests();
  } catch (e) {
    showMessage('erasure-error', e.message);
  }
}

/* -- Security alerts (operators) -------------------------------------- */

const SEVERITY = { CRITICAL: 'badge-encrypted', HIGH: 'badge-encrypted', MEDIUM: 'badge-private', LOW: 'badge-shared' };
const SEVERITY_LABEL = { CRITICAL: 'KRİTİK', HIGH: 'YÜKSEK', MEDIUM: 'ORTA', LOW: 'DÜŞÜK' };

// An erasure request's status, as the list shows it.
const REQUEST_STATUS = { open: 'açık', done: 'tamamlandı', rejected: 'reddedildi' };

export async function loadSecurityAlerts() {
  const list = document.getElementById('alerts-list');
  if (!list) return;
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await apiFetch('/api/security/alerts?limit=100');
    list.innerHTML = d.alerts.length ? d.alerts.map(a => `
      <div class="record-card appt-row" style="cursor:default">
        <div class="record-main">
          <div class="record-title">${escapeHtml(a.title)}</div>
          <div class="record-meta">${escapeHtml(a.description || '')}</div>
          <div class="record-meta appt-muted">${escapeHtml(a.username || '—')} · ${escapeHtml(a.client_ip || '—')} · ${escapeHtml(day(new Date(Number(a.created_at) * 1000).toISOString()))}</div>
        </div>
        <span class="badge ${SEVERITY[a.severity] || ''}">${escapeHtml(SEVERITY_LABEL[a.severity] || a.severity)}</span>
        <div class="appt-actions">${a.acknowledged
          ? '<span class="appt-muted">Görüldü</span>'
          : `<button type="button" class="btn btn-ghost btn-sm" data-action="ack-alert" data-arg="${escapeHtml(a.alert_id)}">Görüldü olarak işaretle</button>`}</div>
      </div>`).join('') : emptyState('Güvenlik uyarısı yok.');
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

export async function acknowledgeAlert(alertId) {
  try {
    await apiFetch(`/api/security/alerts/acknowledge/${encodeURIComponent(alertId)}`, { method: 'POST' });
    loadSecurityAlerts();
  } catch (e) {
    alert('Uyarı işaretlenemedi: ' + e.message);
  }
}
