/* stafflinks.js — who runs whose appointment book
 *
 * Admin and security officer (the center manager): see every practitioner with
 * the secretaries linked to their book, and the secretaries linked to nobody;
 * link a secretary to a practitioner's book, or remove the link. Practitioners
 * do not link (the server refuses them): a secretary on a second book becomes
 * shared, and then only an operator resets or disables them.
 *
 * The list holds usernames, names, roles and account status only. Every value
 * from it is escaped, or set with textContent for the selectors.
 */
import { apiFetch, escapeHtml, emptyState } from './utils.js';
import { showMessage } from './clients.js';

const STATUS_BADGE = {
  active:   ['badge-shared', 'Etkin'],
  pending:  ['badge-private', 'Davet edildi'],
  disabled: ['badge-private', 'Devre dışı'],
};

// The status only picks one of the constant labels above.
function statusBadge(status) {
  const [cls, label] = STATUS_BADGE[status] || STATUS_BADGE.pending;
  return `<span class="badge ${cls}">${label}</span>`;
}

let accounts = { practitioners: [], secretaries: [] };

export async function loadStaffLinks() {
  showMessage('staff-links-error', '');
  const list = document.getElementById('staff-links-list');
  if (!list) return;
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    accounts = await apiFetch('/api/onboarding/staff/links');
    fillSelect('staff-link-secretary', accounts.secretaries, 'Sekreter seçin');
    fillSelect('staff-link-practitioner', accounts.practitioners, 'Psikolog seçin');
    renderLinks();
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

// Options are built with textContent, never markup; the chosen value is kept.
function fillSelect(id, people, placeholder) {
  const select = document.getElementById(id);
  if (!select) return;
  const chosen = select.value;
  select.replaceChildren();
  const first = document.createElement('option');
  first.value = '';
  first.textContent = placeholder;
  select.appendChild(first);
  people.forEach(person => {
    const option = document.createElement('option');
    option.value = person.username;
    const state = person.status === 'active' ? '' : ` (${(STATUS_BADGE[person.status] || STATUS_BADGE.pending)[1].toLocaleLowerCase('tr-TR')})`;
    option.textContent = `${person.full_name} — @${person.username}${state}`;
    select.appendChild(option);
  });
  if (people.some(person => person.username === chosen)) select.value = chosen;
}

const nameOf = (username) => {
  const s = accounts.secretaries.find(x => x.username === username);
  return s ? s.full_name : username;
};

function renderSecretaryRow(secretary, practitioner) {
  const user = escapeHtml(secretary);
  const book = escapeHtml(practitioner);
  return `
    <div class="appt-row" style="display:flex; align-items:center; gap:10px; padding:6px 0;">
      <div style="flex:1">
        <span style="font-weight:500">${escapeHtml(nameOf(secretary))}</span>
        <span style="font-size:12px;color:var(--muted)">@${user}</span>
      </div>
      <button type="button" class="btn btn-ghost btn-sm" data-action="staff-link-remove" data-arg="${user}" data-arg2="${book}">Çıkar</button>
    </div>`;
}

function renderPractitioner(p) {
  const desks = p.secretaries.length
    ? p.secretaries.map(s => renderSecretaryRow(s, p.username)).join('')
    : '<div style="font-size:12px;color:var(--muted)">Bu defterde sekreter yok.</div>';
  return `
    <div class="record-card" style="cursor:default; display:block;">
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
        <div style="flex:1">
          <div class="record-title">${escapeHtml(p.full_name)}</div>
          <div class="record-meta">@${escapeHtml(p.username)}</div>
        </div>
        ${statusBadge(p.status)}
      </div>
      ${desks}
    </div>`;
}

function renderUnlinked(s) {
  return `
    <div class="user-card glass">
      <div style="flex:1">
        <div style="font-weight:600">${escapeHtml(s.full_name)}</div>
        <div style="font-size:12px;color:var(--muted)">@${escapeHtml(s.username)}</div>
      </div>
      ${statusBadge(s.status)}
    </div>`;
}

function renderLinks() {
  const list = document.getElementById('staff-links-list');
  if (list) {
    list.innerHTML = accounts.practitioners.length
      ? accounts.practitioners.map(renderPractitioner).join('')
      : emptyState('Henüz psikolog hesabı yok.');
  }
  const lonely = document.getElementById('staff-links-unlinked');
  if (lonely) {
    const unlinked = accounts.secretaries.filter(s => !s.practitioners.length);
    lonely.innerHTML = unlinked.length
      ? unlinked.map(renderUnlinked).join('')
      : emptyState('Her sekreter en az bir defterde çalışıyor.');
  }
}

export async function linkStaff(e) {
  if (e) e.preventDefault();
  showMessage('staff-links-error', '');
  showMessage('staff-links-success', '');
  const secretary = document.getElementById('staff-link-secretary');
  const practitioner = document.getElementById('staff-link-practitioner');
  if (!secretary || !practitioner || !secretary.value || !practitioner.value) {
    showMessage('staff-links-error', 'Bir sekreter ve bir psikolog seçin.');
    return;
  }
  try {
    const d = await apiFetch('/api/onboarding/staff/link', {
      method: 'POST',
      body: JSON.stringify({ username: secretary.value, practitioner: practitioner.value }),
    });
    showMessage('staff-links-success', `${d.username} artık ${d.practitioner} adlı psikoloğun defterinde çalışıyor.`);  // xss-reviewed: showMessage sets textContent
    secretary.value = '';
    await loadStaffLinks();
  } catch (ex) {
    showMessage('staff-links-error', ex.message);
  }
}

export async function unlinkStaff(secretary, practitioner) {
  showMessage('staff-links-error', '');
  showMessage('staff-links-success', '');
  if (!confirm(`${secretary}, ${practitioner} adlı psikoloğun defterinden çıkarılsın mı? O defterin randevularına ve danışan kartlarına erişimi hemen sona erer.`)) return;
  try {
    await apiFetch('/api/onboarding/staff/unlink', {
      method: 'POST',
      body: JSON.stringify({ username: secretary, practitioner }),
    });
    await loadStaffLinks();
  } catch (ex) {
    showMessage('staff-links-error', ex.message);
  }
}
