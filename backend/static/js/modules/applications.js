/* applications.js — the intake inbox and waitlist ("Başvurular")
 *
 * Someone who wants to become a client calls, writes, walks in or is referred.
 * The front desk (or the practitioner) records the request here, before any
 * client card exists:
 *
 *   - the form: name, phone / e-mail and fixed choices only. There is no note
 *     field — this table is not encrypted, so nothing clinical goes in;
 *   - the waitlist: high priority first, then the oldest, with filters;
 *   - the detail panel: status actions, contact attempts, assigning a
 *     practitioner, "Danışan kartı oluştur" and then "Randevu ver", which books
 *     through the ordinary appointment book (POST /api/appointments) and then
 *     marks the application scheduled.
 *
 * The server decides who sees which application (backend/routers/intake.py).
 * Every value from the server is escaped or set as textContent.
 */
import { apiFetch, escapeHtml, emptyState, getCurrentUser, LOCALE, formatLabel } from './utils.js';

export const APP_STATUS = {
  new:        { label: 'Yeni',               cls: 'badge-shared' },
  contacted:  { label: 'Arandı',             cls: 'badge-shared' },
  waitlisted: { label: 'Bekleme listesinde', cls: 'badge-encrypted' },
  scheduled:  { label: 'Randevu verildi',    cls: 'badge-practitioner-only' },
  declined:   { label: 'Reddedildi',         cls: 'badge-private' },
  withdrawn:  { label: 'Vazgeçti',           cls: 'badge-private' },
  lapsed:     { label: 'Süresi doldu',       cls: 'badge-private' },
};
const CHANNEL = { phone: 'Telefon', email: 'E-posta', walk_in: 'Merkeze geldi', referral: 'Yönlendirme' };
const REASON = {
  no_capacity: 'Yer yok', outside_scope: 'Merkezin çalışma alanı dışında', no_answer: 'Ulaşılamadı',
  applicant_withdrew: 'Başvuran vazgeçti', other: 'Diğer',
};
const DAY = { mon: 'Pzt', tue: 'Sal', wed: 'Çar', thu: 'Per', fri: 'Cum', sat: 'Cmt', sun: 'Paz' };
const TIME = { morning: 'Sabah', afternoon: 'Öğleden sonra', evening: 'Akşam' };
const FORMAT = { either: 'Fark etmez', 'In-person': 'Yüz yüze', Online: 'Online' };
const OPEN = ['new', 'contacted', 'waitlisted'];

let practitioners = [];     // [{username, full_name, assignable}]
let items = [];
let selectedId = '';
let declining = false;

const isStaff = () => ['secretary', 'practitioner'].includes((getCurrentUser() || {}).role);
const label = (table, code) => table[code] || code || '';

function practitionerName(username) {
  const p = practitioners.find(x => x.username === username);
  return p ? p.full_name : username;
}

function show(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

function waitingText(days) {
  if (days === null || days === undefined) return '';
  return days === 0 ? 'Bugün başvurdu' : `${days} gündür bekliyor`;
}

function dateText(iso) {
  return iso ? new Date(iso).toLocaleDateString(LOCALE, { day: '2-digit', month: 'short', year: 'numeric' }) : '';
}

function preferenceText(a) {
  const days = a.preferred_days.length ? a.preferred_days.map(d => label(DAY, d)).join(', ') : 'Her gün';
  const times = a.preferred_times.length ? a.preferred_times.map(t => label(TIME, t)).join(', ') : 'her saat';
  return `${days} · ${times}`;
}

/* -- The badge on the menu ------------------------------------------- */

export async function refreshApplicationsBadge() {
  const badge = document.getElementById('nav-applications-badge');
  if (!badge) return;
  if (!isStaff()) { badge.hidden = true; return; }
  try {
    const counts = (await apiFetch('/api/applications/summary')).counts || {};
    const n = Number(counts.new) || 0;
    badge.textContent = String(n);
    badge.hidden = n === 0;
  } catch (e) {
    badge.hidden = true;
  }
}

/* -- Loading ---------------------------------------------------------- */

function fillSelect(id, options, keep) {
  const select = document.getElementById(id);
  if (!select) return;
  const current = keep ? select.value : '';
  select.replaceChildren();
  options.forEach(([value, text]) => {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = text;
    select.appendChild(o);
  });
  if (current && options.some(([v]) => v === current)) select.value = current;
}

async function loadPractitioners() {
  try {
    practitioners = (await apiFetch('/api/applications/practitioners')).practitioners || [];
  } catch (e) {
    practitioners = [];
  }
  const all = practitioners.map(p => [p.username, p.full_name]);
  const mine = practitioners.filter(p => p.assignable).map(p => [p.username, p.full_name]);
  fillSelect('app-preferred', [['', 'Fark etmez'], ...all], true);
  fillSelect('app-assigned', [['', 'Atanmadı'], ...mine], true);
  fillSelect('app-filter-practitioner', [['', 'Tüm psikologlar'], ...all], true);
}

export async function loadApplications() {
  const list = document.getElementById('applications-list');
  if (!list) return;
  show('app-error', '');
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  await loadPractitioners();
  await reloadList();
}

function filterQuery() {
  const params = new URLSearchParams();
  const pick = (id, name) => {
    const el = document.getElementById(id);
    if (el && el.value) params.set(name, el.value);
  };
  pick('app-filter-status', 'status');
  pick('app-filter-practitioner', 'practitioner');
  pick('app-filter-format', 'session_format');
  pick('app-filter-day', 'day');
  pick('app-filter-time', 'time_of_day');
  const q = params.toString();
  return q ? `?${q}` : '';
}

async function reloadList() {
  const list = document.getElementById('applications-list');
  if (!list) return;
  try {
    const d = await apiFetch(`/api/applications${filterQuery()}`);
    items = d.applications || [];
    const note = document.getElementById('app-retention');
    if (note) {
      note.textContent = `Reddedilen, vazgeçilen veya ${d.retention_days} gün boyunca hiç işlem görmeyen başvuruların kişisel bilgileri ${d.retention_days} gün sonra silinir (KVKK).`;
    }
    list.innerHTML = items.length ? items.map(renderRow).join('') : emptyState('Bu filtrelere uyan başvuru yok.');
  } catch (e) {
    items = [];
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
  if (selectedId && !items.some(a => a.id === selectedId)) selectedId = '';
  renderDetail();
  refreshApplicationsBadge();
}

export function filterApplications() {
  selectedId = '';
  reloadList();
}

function renderRow(a) {
  const st = APP_STATUS[a.status] || { label: a.status, cls: '' };
  const who = a.assigned_practitioner
    ? `Atanan: ${practitionerName(a.assigned_practitioner)}`
    : (a.preferred_practitioner ? `Tercih: ${practitionerName(a.preferred_practitioner)}` : 'Atanmadı');
  const meta = [label(CHANNEL, a.channel), label(FORMAT, a.session_format), preferenceText(a), who].join(' · ');
  const high = a.priority === 'high' ? '<span class="badge badge-encrypted">Öncelikli</span> ' : '';
  const selected = a.id === selectedId ? ' style="border-color: var(--gold)"' : '';
  return `
    <div class="record-card appt-row" data-action="application-open" data-arg="${escapeHtml(a.id)}"${selected}>
      <div class="record-main">
        <div class="record-title">${high}${escapeHtml(a.full_name || 'Kişisel bilgiler silinmiş')}</div>
        <div class="record-meta">${escapeHtml(meta)}</div>
        <div class="record-meta appt-muted">${escapeHtml(waitingText(a.waiting_days) || dateText(a.received_at))}</div>
      </div>
      <span class="badge ${escapeHtml(st.cls)}">${escapeHtml(st.label)}</span>
    </div>`;
}

/* -- The detail panel ------------------------------------------------- */

export function openApplication(id) {
  selectedId = id;
  declining = false;
  const list = document.getElementById('applications-list');
  if (list) list.innerHTML = items.map(renderRow).join('');
  renderDetail();
  const panel = document.getElementById('application-detail');
  if (panel && panel.scrollIntoView) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function option(value, text, isSelected) {
  return `<option value="${escapeHtml(value)}"${isSelected ? ' selected' : ''}>${escapeHtml(text)}</option>`;
}

function button(action, text, cls = 'btn-ghost', arg = selectedId) {
  return `<button type="button" class="btn ${cls} btn-sm" data-action="${action}" data-arg="${escapeHtml(arg)}">${escapeHtml(text)}</button>`;
}

function renderDetail() {
  const panel = document.getElementById('application-detail');
  if (!panel) return;
  const a = items.find(x => x.id === selectedId);
  if (!a) {
    panel.hidden = true;
    panel.innerHTML = '';
    return;
  }
  panel.hidden = false;
  const st = APP_STATUS[a.status] || { label: a.status, cls: '' };
  const open = OPEN.includes(a.status);
  const rows = [
    ['Durum', st.label + (a.close_reason ? ` — ${label(REASON, a.close_reason)}` : '')],
    ['Başvuru', `${dateText(a.received_at)} · ${label(CHANNEL, a.channel)}`],
    ['Bekleme', waitingText(a.waiting_days) || '—'],
    ['Telefon', a.phone || '—'],
    ['E-posta', a.email || '—'],
    ['Tercih edilen psikolog', a.preferred_practitioner ? practitionerName(a.preferred_practitioner) : 'Fark etmez'],
    ['Atanan psikolog', a.assigned_practitioner ? practitionerName(a.assigned_practitioner) : 'Atanmadı'],
    ['Görüşme biçimi', label(FORMAT, a.session_format)],
    ['Uygun zaman', preferenceText(a)],
    ['Öncelik', a.priority === 'high' ? 'Yüksek' : 'Normal'],
    ['Arama denemesi', a.contact_attempts
      ? `${a.contact_attempts} kez · son: ${new Date(a.last_contact_at).toLocaleString(LOCALE, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}`
      : 'Henüz yok'],
    ['Danışan kartı', a.client_id || '—'],
  ];
  const details = rows.map(([k, v]) => `
      <div class="app-detail-row"><span class="appt-muted">${escapeHtml(k)}</span><span>${escapeHtml(String(v))}</span></div>`).join('');

  let actions = '';
  if (open) {
    const status = [];
    if (a.status === 'new') status.push(button('application-status', 'Arandı', 'btn-ghost', `${a.id}|contacted`));
    if (a.status !== 'waitlisted') status.push(button('application-status', 'Bekleme listesine al', 'btn-ghost', `${a.id}|waitlisted`));
    status.push(button('application-contact', 'Ulaşılamadı — arama denemesi ekle'));
    status.push(button('application-decline', 'Reddet'));
    status.push(button('application-status', 'Vazgeçti', 'btn-ghost', `${a.id}|withdrawn`));
    status.push(button('application-priority', a.priority === 'high' ? 'Önceliği normale al' : 'Öncelikli yap'));
    actions += `<div class="app-actions">${status.join('')}</div>`;

    if (declining) {
      const reasons = Object.entries(REASON).filter(([code]) => code !== 'applicant_withdrew')
        .map(([code, text]) => option(code, text, false)).join('');
      actions += `
        <div class="app-actions">
          <label for="app-decline-reason">Reddetme nedeni</label>
          <select id="app-decline-reason">${reasons}</select>
          ${button('application-decline-confirm', 'Reddet', 'btn-gold')}
        </div>`;
    }

    if (!a.client_id) {
      const mine = practitioners.filter(p => p.assignable)
        .map(p => option(p.username, p.full_name, p.username === a.assigned_practitioner)).join('');
      actions += `
        <div class="app-actions">
          <label for="app-assign">Psikolog</label>
          <select id="app-assign"><option value="">Atanmadı</option>${mine}</select>
          ${button('application-assign', 'Ata')}
          ${button('application-convert', 'Danışan kartı oluştur', 'btn-gold')}
        </div>
        <p class="appt-muted">Danışan kartı atanan psikolog için oluşturulur; ad, telefon ve e-posta karta taşınır ve başvurudan silinir.</p>`;
    } else {
      const fmt = a.session_format === 'either' ? 'In-person' : a.session_format;
      const formats = ['In-person', 'Online'].map(f => option(f, formatLabel(f), f === fmt)).join('');
      actions += `
        <form class="appt-form" data-submit-action="application-book" data-arg="${escapeHtml(a.id)}">
          <div class="field-group"><label for="app-book-date">Tarih</label><input type="date" id="app-book-date" required /></div>
          <div class="field-group"><label for="app-book-time">Saat</label><input type="time" id="app-book-time" step="900" required /></div>
          <div class="field-group"><label for="app-book-duration">Süre</label>
            <select id="app-book-duration"><option value="45">45 dk</option><option value="50" selected>50 dk</option><option value="60">60 dk</option></select></div>
          <div class="field-group"><label for="app-book-format">Görüşme biçimi</label><select id="app-book-format">${formats}</select></div>
          <button type="submit" class="btn btn-gold">Randevu ver</button>
        </form>
        <p class="appt-muted">İlk randevu ${escapeHtml(practitionerName(a.assigned_practitioner))} defterine yazılır; başvuru “Randevu verildi” olur.</p>`;
    }
  }

  panel.innerHTML = `
    <h3 class="appt-section-title">${escapeHtml(a.full_name || 'Kişisel bilgiler silinmiş')}</h3>
    <div id="app-detail-error" class="alert alert-error" style="display:none; margin-bottom:12px;"></div>
    <div class="app-detail-grid">${details}</div>
    ${actions}`;
}

/* -- Actions ---------------------------------------------------------- */

async function act(path, body, success) {
  show('app-detail-error', '');
  show('app-success', '');
  try {
    const d = await apiFetch(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });
    await reloadList();
    if (success) show('app-success', success);
    return d;
  } catch (e) {
    show('app-detail-error', e.message);
    return null;
  }
}

export function setApplicationStatus(arg) {
  const [id, status] = String(arg).split('|');
  const text = { contacted: 'Başvuru “Arandı” olarak işaretlendi.', waitlisted: 'Başvuru bekleme listesine alındı.',
    withdrawn: 'Başvuru “Vazgeçti” olarak kapatıldı.' }[status];
  return act(`/api/applications/${encodeURIComponent(id)}/status`, { status }, text);
}

export function addContactAttempt(id) {
  return act(`/api/applications/${encodeURIComponent(id)}/contact-attempt`, null, 'Arama denemesi kaydedildi.');
}

export function startDecline() {
  declining = !declining;
  renderDetail();
}

export function confirmDecline(id) {
  const reason = (document.getElementById('app-decline-reason') || {}).value;
  declining = false;
  return act(`/api/applications/${encodeURIComponent(id)}/status`, { status: 'declined', reason },
    'Başvuru reddedildi.');
}

async function patch(id, body, success) {
  show('app-detail-error', '');
  show('app-success', '');
  try {
    await apiFetch(`/api/applications/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
    await reloadList();
    if (success) show('app-success', success);
  } catch (e) {
    show('app-detail-error', e.message);
  }
}

export function assignApplication(id) {
  const value = (document.getElementById('app-assign') || {}).value || null;
  return patch(id, { assigned_practitioner: value }, value ? 'Başvuru psikoloğa atandı.' : 'Atama kaldırıldı.');
}

export function togglePriority(id) {
  const a = items.find(x => x.id === id);
  if (!a) return null;
  return patch(id, { priority: a.priority === 'high' ? 'normal' : 'high' }, 'Öncelik güncellendi.');
}

export async function convertApplication(id) {
  const d = await act(`/api/applications/${encodeURIComponent(id)}/convert`, null, '');
  if (d && d.client) {
    show('app-success', `Danışan kartı oluşturuldu: ${d.client.patient_id}. Şimdi ilk randevuyu verin.`);  // xss-reviewed: show() sets textContent
  }
}

export async function bookFromApplication(el, e) {
  if (e) e.preventDefault();
  const id = el.dataset.arg;
  const a = items.find(x => x.id === id);
  if (!a || !a.client_id) return;
  show('app-detail-error', '');
  const date = document.getElementById('app-book-date').value;
  const clock = document.getElementById('app-book-time').value;
  if (!date || !clock) {
    show('app-detail-error', 'Bir tarih ve saat seçin.');
    return;
  }
  try {
    // The ordinary appointment book: the same rules, checks and audit.
    await apiFetch(`/api/appointments?practitioner=${encodeURIComponent(a.assigned_practitioner)}`, {
      method: 'POST',
      body: JSON.stringify({
        patient_id: a.client_id,
        starts_at: new Date(`${date}T${clock}`).toISOString(),
        duration_min: Number(document.getElementById('app-book-duration').value),
        session_format: document.getElementById('app-book-format').value,
      }),
    });
  } catch (ex) {
    show('app-detail-error', ex.message);
    return;
  }
  await act(`/api/applications/${encodeURIComponent(id)}/scheduled`, null,
    'İlk randevu verildi; başvuru “Randevu verildi” olarak kapandı.');
}

/* -- The new-application form ----------------------------------------- */

function checked(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(el => el.value);
}

export async function createApplication(e) {
  if (e) e.preventDefault();
  show('app-form-error', '');
  show('app-success', '');
  const value = id => ((document.getElementById(id) || {}).value || '').trim();
  const body = {
    channel: value('app-channel'),
    full_name: value('app-full-name'),
    phone: value('app-phone') || null,
    email: value('app-email') || null,
    preferred_practitioner: value('app-preferred') || null,
    session_format: value('app-format') || 'either',
    preferred_days: checked('app-day'),
    preferred_times: checked('app-time'),
    priority: value('app-priority') || 'normal',
  };
  // Left out, a practitioner's new application is assigned to them.
  if (value('app-assigned')) body.assigned_practitioner = value('app-assigned');
  if (!body.phone && !body.email) {
    show('app-form-error', 'Bir telefon numarası veya e-posta adresi girin.');
    return;
  }
  try {
    const d = await apiFetch('/api/applications', { method: 'POST', body: JSON.stringify(body) });
    const form = document.getElementById('application-form');
    if (form) form.reset();
    selectedId = d.application.id;
    await reloadList();
    show('app-success', 'Başvuru kaydedildi.');
  } catch (ex) {
    show('app-form-error', ex.message);
  }
}
