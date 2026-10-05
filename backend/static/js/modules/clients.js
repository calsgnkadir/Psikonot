/* clients.js — the practice's client cards
 *
 * Practitioner and secretary ("Clients"): add a client, keep their contact
 * details up to date, and see the tally — sessions attended, missed and
 * cancelled. Only the practitioner opens a client's file from here, and acts on
 * the client's KVKK rights (kvkk.js).
 *
 * Practitioner only: invite the secretary who runs the appointment book.
 *
 * Login screen: a new practitioner or secretary redeems their invitation code.
 */
import { apiFetch, escapeHtml, emptyState, getCurrentUser, setSelectedPatient } from './utils.js';
import { navigate } from './dashboard.js';

// The code travels in the URL fragment (#invite=...). Browsers never send the
// fragment to the server, so the code does not end up in access logs.
function inviteLink(code) {
  return `${location.origin}/#invite=${encodeURIComponent(code)}`;
}

export function showMessage(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

const isPractitioner = () => (getCurrentUser() || {}).role === 'practitioner';

function when(iso) {
  return new Date(iso).toLocaleString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/* -- Client cards ---------------------------------------------------- */

let clients = [];

// One line of the tally: "4 attended · 1 missed · 1 cancelled".
// Plain text, not HTML: every caller escapes it.
export function tallyLine(c) {
  const parts = [`${c.attended} attended`, `${c.no_show} missed`];  // xss-reviewed: plain text, escaped by the caller
  if (c.cancelled) parts.push(`${c.cancelled} cancelled`);  // xss-reviewed: plain text, escaped by the caller
  return parts.join(' · ');
}

export async function loadClients() {
  const staffPanel = document.getElementById('staff-panel');
  if (staffPanel) staffPanel.hidden = !isPractitioner();
  if (isPractitioner()) loadStaff();
  const list = document.getElementById('clients-list');
  if (!list) return;
  list.innerHTML = '<div class="loading-spinner">Loading...</div>';
  try {
    clients = (await apiFetch('/api/clients')).clients;
    list.innerHTML = clients.length
      ? clients.map(renderClient).join('')
      : emptyState('No clients yet. Add the first one above.');
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

function renderClient(c) {
  const pid = escapeHtml(c.patient_id);
  const contact = [c.phone, c.email].filter(Boolean).map(escapeHtml).join(' · ') || 'No contact details';
  const next = c.next_appointment_at ? `Next: ${escapeHtml(when(c.next_appointment_at))}` : 'No upcoming appointment';
  const kvkk = c.kvkk_signed_on
    ? `<span class="badge badge-shared">KVKK signed ${escapeHtml(c.kvkk_signed_on)}</span>`
    : '<span class="badge badge-private">KVKK forms missing</span>';
  const button = (action, label) =>
    `<button type="button" class="btn btn-ghost btn-sm" data-action="${action}" data-arg="${pid}">${label}</button>`;
  const fileButtons = isPractitioner()
    ? button('open-client', 'Open file') + button('client-export', 'Export data') + button('client-erasure', 'Request erasure')
    : '';
  return `
    <div class="user-card glass" style="flex-wrap:wrap">
      <div class="user-avatar" style="background:linear-gradient(135deg,#C9A84C,#8B6914)">${escapeHtml(c.full_name.charAt(0))}</div>
      <div style="flex:1; min-width:220px">
        <div style="font-weight:600">${escapeHtml(c.full_name)} <span style="font-size:12px;color:var(--muted);font-weight:400">${pid}</span></div>
        <div style="font-size:12px;color:var(--muted)">${contact}</div>
        <div style="font-size:12px;color:var(--muted)">${escapeHtml(tallyLine(c))} · ${next}</div>
      </div>
      ${kvkk}
      <div class="appt-actions">${button('client-edit', 'Edit')}${fileButtons}</div>
    </div>`;
}

function formValue(id) {
  return document.getElementById(id).value.trim();
}

export function editClient(patientId) {
  const c = clients.find(x => x.patient_id === patientId);
  if (!c) return;
  document.getElementById('client-edit-id').value = c.patient_id;
  document.getElementById('client-full-name').value = c.full_name;
  document.getElementById('client-phone').value = c.phone || '';
  document.getElementById('client-email').value = c.email || '';
  document.getElementById('client-kvkk').value = c.kvkk_signed_on || '';
  document.getElementById('client-form-title').textContent = `Edit ${c.full_name} (${c.patient_id})`;
  document.getElementById('client-form-submit').textContent = 'Save';
  document.getElementById('client-form-cancel').style.display = '';
  document.getElementById('client-full-name').focus();
}

export function cancelEdit() {
  document.querySelector('[data-submit-action="save-client"]').reset();
  document.getElementById('client-edit-id').value = '';
  document.getElementById('client-form-title').textContent = 'Add a client';
  document.getElementById('client-form-submit').textContent = 'Add client';
  document.getElementById('client-form-cancel').style.display = 'none';
}

export async function saveClient(e) {
  if (e) e.preventDefault();
  showMessage('client-form-error', '');
  showMessage('client-form-success', '');
  const editId = document.getElementById('client-edit-id').value;
  const body = {
    full_name: formValue('client-full-name'),
    phone: formValue('client-phone') || null,
    email: formValue('client-email') || null,
    kvkk_signed_on: formValue('client-kvkk') || null,
  };
  try {
    const d = editId
      ? await apiFetch(`/api/clients/${encodeURIComponent(editId)}`, { method: 'PATCH', body: JSON.stringify(body) })
      : await apiFetch('/api/clients', { method: 'POST', body: JSON.stringify(body) });
    cancelEdit();
    await loadClients();
    showMessage('client-form-success', `${editId ? 'Saved' : 'Added'}: ${d.client.full_name} (${d.client.patient_id}).`);  // xss-reviewed: showMessage sets textContent
  } catch (ex) {
    showMessage('client-form-error', ex.message);
  }
}

export function openClient(patientId, page = 'records') {
  setSelectedPatient(patientId);
  const selector = document.getElementById('patient-selector-input');
  if (selector) selector.value = patientId;
  const recPatId = document.getElementById('rec-patient-id');
  if (recPatId) recPatId.value = patientId;
  navigate(page);
}

/* -- Practitioner: secretaries --------------------------------------- */

async function loadStaff() {
  const list = document.getElementById('staff-list');
  if (!list) return;
  try {
    const d = await apiFetch('/api/onboarding/staff');
    list.innerHTML = d.staff.length
      ? d.staff.map(s => `
          <div class="user-card glass">
            <div class="user-avatar" style="background:linear-gradient(135deg,#818cf8,#4f46e5)">${escapeHtml(s.full_name.charAt(0))}</div>
            <div style="flex:1">
              <div style="font-weight:600">${escapeHtml(s.full_name)}</div>
              <div style="font-size:12px;color:var(--muted)">@${escapeHtml(s.username)}</div>
            </div>
            <span class="badge ${s.status === 'active' ? 'badge-shared' : 'badge-private'}">${s.status === 'active' ? 'Active' : 'Invited'}</span>
          </div>`).join('')
      : emptyState('No secretary yet.');
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

export async function inviteSecretary(e) {
  if (e) e.preventDefault();
  showMessage('secretary-error', '');
  const name = document.getElementById('secretary-full-name');
  const username = document.getElementById('secretary-username');
  try {
    const d = await apiFetch('/api/onboarding/invite-secretary', {
      method: 'POST',
      body: JSON.stringify({ full_name: name.value, username: username.value }),
    });
    name.value = '';
    username.value = '';
    document.getElementById('secretary-result').hidden = false;
    document.getElementById('secretary-result-username').textContent = d.username;
    document.getElementById('secretary-result-link').value = inviteLink(d.invite_code);
    loadStaff();
  } catch (ex) {
    showMessage('secretary-error', ex.message);
  }
}

export function copyField(id) {
  const el = document.getElementById(id);
  if (el && el.value) navigator.clipboard?.writeText(el.value);
}

/* -- Login screen: redeem an invitation ------------------------------ */

export function showRedeem(code = '') {
  document.getElementById('login-form').style.display = 'none';
  document.getElementById('login-invite-link').style.display = 'none';
  document.getElementById('redeem-form').style.display = 'block';
  showMessage('login-error', '');
  showMessage('redeem-error', '');
  document.getElementById('inp-invite-code').value = code;
  document.getElementById(code ? 'inp-new-password' : 'inp-invite-code').focus();
}

export function showLogin() {
  document.getElementById('redeem-form').style.display = 'none';
  document.getElementById('login-form').style.display = 'block';
  document.getElementById('login-invite-link').style.display = 'block';
  showMessage('redeem-error', '');
}

export async function redeemInvite(e) {
  if (e) e.preventDefault();
  showMessage('redeem-error', '');
  const code = document.getElementById('inp-invite-code').value.trim();
  const password = document.getElementById('inp-new-password').value;
  if (password !== document.getElementById('inp-new-password2').value) {
    showMessage('redeem-error', 'The two passwords do not match.');
    return;
  }
  try {
    const d = await apiFetch('/api/onboarding/redeem', {
      method: 'POST',
      body: JSON.stringify({ enrollment_token: code, new_password: password }),
    });
    document.getElementById('redeem-form').reset();
    showLogin();
    document.getElementById('inp-username').value = d.username;
    showMessage('login-success', `Your account is ready. Your username is ${d.username}.`);  // xss-reviewed: showMessage sets textContent
    document.getElementById('inp-password').focus();
  } catch (ex) {
    showMessage('redeem-error', ex.message);
  }
}

// Opening an invitation link (…/#invite=CODE) goes straight to the redeem form.
// The code is removed from the address bar and the history at once.
export function checkInviteLink() {
  const m = location.hash.match(/^#invite=([A-Za-z0-9_%-]+)$/);
  if (!m) return;
  history.replaceState(null, '', location.pathname + location.search);
  showRedeem(decodeURIComponent(m[1]));
}
