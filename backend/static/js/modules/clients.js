/* clients.js — the practice's client cards
 *
 * Practitioner and secretary ("Clients"): add a client, keep their contact
 * details up to date, and see the tally — sessions attended, missed and
 * cancelled. Only the practitioner opens a client's file from here, and acts on
 * the client's KVKK rights (kvkk.js).
 *
 * Practitioner only: invite the secretary who runs the appointment book, add
 * an existing secretary (a center's shared front desk) to their book, remove a
 * secretary from it, and reset, disable or enable a secretary who works for
 * them alone (accounts.js). A shared secretary's account is an operator's.
 *
 * A secretary who runs several practitioners' books picks one at the top of
 * the page (frontdesk.js); the list and the form follow that choice.
 *
 * Login screen: a new practitioner or secretary redeems their invitation code;
 * anyone given a password reset code redeems it on the same form.
 */
import { apiFetch, escapeHtml, emptyState, getCurrentUser, setSelectedPatient, LOCALE } from './utils.js';
import { navigate } from './dashboard.js';
import { accountButtons } from './accounts.js';
import { loadBooks, bookQuery } from './frontdesk.js';

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
  return new Date(iso).toLocaleString(LOCALE, {
    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/* -- Client cards ---------------------------------------------------- */

let clients = [];

// One line of the tally: "4 seansa geldi · 1 gelmedi · 1 iptal".
// Plain text, not HTML: every caller escapes it.
export function tallyLine(c) {
  const parts = [`${c.attended} seansa geldi`, `${c.no_show} gelmedi`];  // xss-reviewed: plain text, escaped by the caller
  if (c.cancelled) parts.push(`${c.cancelled} iptal`);  // xss-reviewed: plain text, escaped by the caller
  return parts.join(' · ');
}

export async function loadClients() {
  const staffPanel = document.getElementById('staff-panel');
  if (staffPanel) staffPanel.hidden = !isPractitioner();
  if (isPractitioner()) loadStaff();
  const list = document.getElementById('clients-list');
  if (!list) return;
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  await loadBooks();
  try {
    clients = (await apiFetch(`/api/clients${bookQuery()}`)).clients;
    list.innerHTML = clients.length
      ? clients.map(renderClient).join('')
      : emptyState('Henüz danışan yok. İlk danışanı yukarıdan ekleyin.');
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

function renderClient(c) {
  const pid = escapeHtml(c.patient_id);
  const contact = [c.phone, c.email].filter(Boolean).map(escapeHtml).join(' · ') || 'İletişim bilgisi yok';
  const next = c.next_appointment_at ? `Sonraki randevu: ${escapeHtml(when(c.next_appointment_at))}` : 'Yaklaşan randevu yok';
  const kvkk = c.kvkk_signed_on
    ? `<span class="badge badge-shared">KVKK imzalı: ${escapeHtml(c.kvkk_signed_on)}</span>`
    : '<span class="badge badge-private">KVKK formları eksik</span>';
  const button = (action, label) =>
    `<button type="button" class="btn btn-ghost btn-sm" data-action="${action}" data-arg="${pid}">${label}</button>`;
  const fileButtons = isPractitioner()
    ? button('open-client', 'Danışan dosyasını aç') + button('client-export', 'Veri dışa aktarma') + button('client-erasure', 'Silme talebi')
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
      <div class="appt-actions">${button('client-edit', 'Düzenle')}${fileButtons}</div>
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
  document.getElementById('client-form-title').textContent = `Danışanı düzenle: ${c.full_name} (${c.patient_id})`;
  document.getElementById('client-form-submit').textContent = 'Kaydet';
  document.getElementById('client-form-cancel').style.display = '';
  document.getElementById('client-full-name').focus();
}

export function cancelEdit() {
  document.querySelector('[data-submit-action="save-client"]').reset();
  document.getElementById('client-edit-id').value = '';
  document.getElementById('client-form-title').textContent = 'Danışan ekle';
  document.getElementById('client-form-submit').textContent = 'Danışan ekle';
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
      : await apiFetch(`/api/clients${bookQuery()}`, { method: 'POST', body: JSON.stringify(body) });
    cancelEdit();
    await loadClients();
    showMessage('client-form-success', `${editId ? 'Kaydedildi' : 'Eklendi'}: ${d.client.full_name} (${d.client.patient_id}).`);  // xss-reviewed: showMessage sets textContent
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

const STAFF_BADGE = {
  active:   ['badge-shared', 'Etkin'],
  pending:  ['badge-private', 'Davet edildi'],
  disabled: ['badge-private', 'Devre dışı'],
};

// accountButtons escapes the username; the status only picks constant labels.
// A secretary shared with other practitioners is only removed from this book:
// resetting or disabling them is an operator's job.
function buttons(s) {
  const user = escapeHtml(s.username);
  const remove = `<button type="button" class="btn btn-ghost btn-sm" data-action="staff-unlink" data-arg="${user}">Defterimden çıkar</button>`;
  return (s.shared ? '' : accountButtons(s.username, s.status)) + remove;  // xss-reviewed: escaped inside accountButtons
}

const sharedBadge = (s) => (s.shared ? '<span class="badge badge-shared" title="Başka psikologlar için de çalışıyor">Ortak</span>' : '');

export async function loadStaff() {
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
            ${sharedBadge(s)}
            <span class="badge ${(STAFF_BADGE[s.status] || STAFF_BADGE.pending)[0]}">${(STAFF_BADGE[s.status] || STAFF_BADGE.pending)[1]}</span>
            <div class="appt-actions">${buttons(s)}</div>
          </div>`).join('')
      : emptyState('Henüz sekreter yok.');
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

// Adding an existing secretary to a further book is the center manager's job
// (stafflinks.js): a practitioner only removes one from their own book.
export async function unlinkSecretary(username) {
  showMessage('staff-error', '');
  if (!confirm(`${username} randevu defterinizden çıkarılsın mı? Randevularınıza ve danışan kartlarınıza erişimi hemen sona erer.`)) return;
  try {
    await apiFetch('/api/onboarding/staff/unlink', { method: 'POST', body: JSON.stringify({ username }) });
    loadStaff();
  } catch (ex) {
    showMessage('staff-error', ex.message);
  }
}

export function copyField(id) {
  const el = document.getElementById(id);
  if (el && el.value) navigator.clipboard?.writeText(el.value);
}

/* -- Login screen: redeem an invitation ------------------------------ */

// One form for both codes; `mode` says which endpoint redeems it.
const REDEEM_MODES = {
  invite: {
    intro: 'Yeni bir hesap için size davet kodu verildi. Hesabı açmak için bir şifre belirleyin.',
    label: 'Davet kodu',
    submit: 'HESABI ETKİNLEŞTİR',
  },
  reset: {
    intro: 'Şifrenizi mi unuttunuz? Psikoloğunuz ya da merkez yöneticisi size yüz yüze bir sıfırlama kodu verir. Bu kodla yeni bir şifre belirleyin.',
    label: 'Sıfırlama kodu',
    submit: 'YENİ ŞİFREYİ KAYDET',
  },
};

export function showRedeem(code = '', mode = 'invite') {
  const texts = REDEEM_MODES[mode] || REDEEM_MODES.invite;
  document.getElementById('inp-redeem-mode').value = mode;
  document.getElementById('redeem-intro').textContent = texts.intro;
  document.getElementById('redeem-code-label').textContent = texts.label;
  document.getElementById('redeem-submit').textContent = texts.submit;
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
    showMessage('redeem-error', 'Şifreler birbiriyle eşleşmiyor.');
    return;
  }
  const isReset = document.getElementById('inp-redeem-mode').value === 'reset';
  try {
    const d = isReset
      ? await apiFetch('/api/accounts/redeem-reset', {
        method: 'POST',
        body: JSON.stringify({ reset_code: code, new_password: password }),
      })
      : await apiFetch('/api/onboarding/redeem', {
        method: 'POST',
        body: JSON.stringify({ enrollment_token: code, new_password: password }),
      });
    document.getElementById('redeem-form').reset();
    showLogin();
    document.getElementById('inp-username').value = d.username;
    showMessage('login-success', isReset
      ? `Yeni şifreniz kaydedildi. ${d.username} olarak giriş yapın.`  // xss-reviewed: showMessage sets textContent
      : `Hesabınız hazır. Kullanıcı adınız: ${d.username}.`);  // xss-reviewed: showMessage sets textContent
    document.getElementById('inp-password').focus();
  } catch (ex) {
    showMessage('redeem-error', ex.message);
  }
}

// Opening an invitation link (…/#invite=CODE) or a reset link (…/#reset=CODE)
// goes straight to the redeem form. The code is removed from the address bar
// and the history at once.
export function checkInviteLink() {
  const m = location.hash.match(/^#(invite|reset)=([A-Za-z0-9_%-]+)$/);
  if (!m) return;
  history.replaceState(null, '', location.pathname + location.search);
  showRedeem(decodeURIComponent(m[2]), m[1]);
}
