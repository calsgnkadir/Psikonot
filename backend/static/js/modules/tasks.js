/* tasks.js — the staff task list ("İşler")
 *
 * The front desk's and practitioners' shared to-do list (backend/routers/tasks.py):
 *
 *   - the form: a type, the one client / application / appointment it is
 *     about, who does it, when, and how urgent. There is no text field — the
 *     table is not encrypted, so nothing clinical goes in. The line shown for
 *     a task is built from its type and the linked item;
 *   - the lists: Bugün (due today or overdue), Yaklaşan, Tamamlanan, filtered
 *     by assignee, type and book; done / cancel / reopen;
 *   - the "Bugünkü işler" card on the practitioner's dashboard and the
 *     secretary's day, and the count on the menu.
 *
 * The server decides whose tasks a user sees. Every value from the server is
 * escaped or set as textContent. The module registers its own data-action
 * handlers, so the page needs no inline script.
 */
import { apiFetch, escapeHtml, emptyState, getCurrentUser, LOCALE } from './utils.js';
import { registerActions } from './actions.js';
import { navigate } from './dashboard.js';
import { loadApplications, openApplication } from './applications.js';

export const TASK_TYPE = {
  call_back:             'Geri ara',
  kvkk_forms:            'KVKK formlarını imzalat',
  confirm_appointment:   'Randevuyu teyit et',
  request_document:      'Belge hatırlat',
  follow_up_application: 'Başvuruyu takip et',
  prepare_room:          'Odayı hazırla',
  other:                 'Diğer iş',
};
const LINK_KIND = { client: 'Danışan', application: 'Başvuru', appointment: 'Randevu' };
const STATUS = {
  open:      { label: 'Açık',         cls: 'badge-shared' },
  done:      { label: 'Tamamlandı',   cls: 'badge-practitioner-only' },
  cancelled: { label: 'İptal edildi', cls: 'badge-private' },
};
const EMPTY = {
  today:    'Bugün için iş yok.',
  upcoming: 'Yaklaşan iş yok.',
  done:     'Tamamlanan veya iptal edilen iş yok.',
};

let view = 'today';
let books = [];            // [{username, full_name}]
let options = {};          // book -> {clients, applications, appointments, assignees}
let items = [];
let serverToday = '';

const user = () => getCurrentUser() || {};
const isStaff = () => ['secretary', 'practitioner'].includes(user().role);
const multiBook = () => books.length > 1;

function show(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

function localDay(iso) {
  // A YYYY-MM-DD in the practice's timezone: read at noon so no browser
  // timezone moves it to another day.
  return new Date(`${iso}T12:00:00`);
}

function dayText(due, today) {
  if (!due) return '';
  if (today) {
    const diff = Math.round((localDay(due) - localDay(today)) / 86400000);
    if (diff === 0) return 'Bugün';
    if (diff === 1) return 'Yarın';
    if (diff === -1) return 'Dün';
  }
  return localDay(due).toLocaleDateString(LOCALE, { day: '2-digit', month: 'short', year: 'numeric' });
}

function whenText(iso) {
  return iso ? new Date(iso).toLocaleString(LOCALE, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
}

function bookName(username) {
  const b = books.find(x => x.username === username);
  return b ? b.full_name : username;
}

// The line a task shows: its type, and what it is about.
export function taskTitle(t) {
  const kind = TASK_TYPE[t.type] || t.type;
  if (!t.link) return kind;
  if (!t.link.available) return `${kind} — ${LINK_KIND[t.link.type] || ''} (artık bu defterde değil)`;
  const label = t.link.label || (t.link.type === 'application' ? 'Kişisel bilgileri silinmiş başvuru' : t.link.id);
  const when = t.link.starts_at ? ` · ${whenText(t.link.starts_at)}` : '';
  return `${kind} — ${label}${when}`;
}

/* -- The badge on the menu ------------------------------------------- */

export async function refreshTasksBadge() {
  const badge = document.getElementById('nav-tasks-badge');
  if (!badge) return;
  if (!isStaff()) { badge.hidden = true; return; }
  try {
    const counts = (await apiFetch('/api/tasks/summary')).counts || {};
    const n = Number(counts.today) || 0;
    badge.textContent = String(n);
    badge.hidden = n === 0;
  } catch (e) {
    badge.hidden = true;
  }
}

/* -- The "Bugünkü işler" card ---------------------------------------- */

export async function loadTodayTasks(containerId) {
  const box = document.getElementById(containerId);
  if (!box) return;
  if (!isStaff()) { box.hidden = true; return; }
  box.hidden = false;
  const list = box.querySelector('.tasks-card-list');
  if (!list) return;
  try {
    const d = await apiFetch('/api/tasks?view=today');
    const today = d.today;
    const tasks = d.tasks || [];
    const shown = tasks.slice(0, 5);
    const more = tasks.length > shown.length
      ? `<p class="appt-muted">${tasks.length - shown.length} iş daha var.</p>` : '';
    list.innerHTML = shown.length
      ? shown.map(t => `
        <div class="task-mini${t.overdue ? ' task-overdue' : ''}">
          <span class="task-mini-title">${escapeHtml(taskTitle(t))}</span>
          <span class="appt-muted">${escapeHtml(dueText(t, today))}${t.overdue ? ' · <strong class="task-late">Gecikti</strong>' : ''}</span>
        </div>`).join('') + more
      : emptyState('Bugün için iş yok.');
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

function dueText(t, today) {
  return `${dayText(t.due_on, today)}${t.due_time ? ` ${t.due_time}` : ''}`;
}

/* -- Loading ---------------------------------------------------------- */

function fillSelect(id, entries, keep = true) {
  const select = document.getElementById(id);
  if (!select) return;
  const current = keep ? select.value : '';
  select.replaceChildren();
  entries.forEach(([value, text]) => {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = text;
    select.appendChild(o);
  });
  if (current && entries.some(([v]) => v === current)) select.value = current;
}

async function loadBooks() {
  try {
    books = (await apiFetch('/api/appointments/practitioners')).practitioners || [];
  } catch (e) {
    books = [];
  }
  const bookEntries = books.map(b => [b.username, b.full_name]);
  fillSelect('task-book', bookEntries);
  fillSelect('task-filter-book', [['', 'Tüm defterler'], ...bookEntries]);
  ['task-book-field', 'task-filter-book-field'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.hidden = !multiBook();
  });
}

async function loadOptions() {
  options = {};
  await Promise.all(books.map(async b => {
    const q = user().role === 'secretary' ? `?practitioner=${encodeURIComponent(b.username)}` : '';  // xss-reviewed: a URL query, not HTML
    try {
      options[b.username] = await apiFetch(`/api/tasks/options${q}`);
    } catch (e) {
      options[b.username] = { clients: [], applications: [], appointments: [], assignees: [] };
    }
  }));
  const people = new Map();
  Object.values(options).forEach(o => (o.assignees || []).forEach(p => people.set(p.username, p.full_name)));
  fillSelect('task-filter-assignee', [['', 'Herkes'], ['me', 'Bana atananlar'], ['unassigned', 'Atanmamış'],
    ...[...people].filter(([u]) => u !== user().username)]);
  fillFormChoices();
}

function selectedBook() {
  const el = document.getElementById('task-book');
  return (el && el.value) || (books[0] || {}).username || '';
}

// The link and assignee pickers follow the book chosen in the form.
export function fillFormChoices() {
  const o = options[selectedBook()] || { clients: [], applications: [], appointments: [], assignees: [] };
  const kind = (document.getElementById('task-link-type') || {}).value || '';
  const field = document.getElementById('task-link-field');
  if (field) field.hidden = !kind;
  let entries = [];
  // Option texts: fillSelect sets them as textContent.
  if (kind === 'client') entries = o.clients.map(c => [c.id, `${c.label} · ${c.id}`]);  // xss-reviewed: textContent via fillSelect
  if (kind === 'application') entries = o.applications.map(a => [a.id, a.label || 'Kişisel bilgileri silinmiş başvuru']);
  if (kind === 'appointment') entries = o.appointments.map(a => [a.id, `${a.label} · ${whenText(a.starts_at)}`]);
  fillSelect('task-link-id', entries.length ? entries : [['', 'Seçilecek kayıt yok']]);
  const me = user().username;
  fillSelect('task-assignee', [['', 'Atanmamış'],
    ...o.assignees.map(p => [p.username, p.username === me ? `${p.full_name} (ben)` : p.full_name])]);
}

export async function loadTasks() {
  const list = document.getElementById('tasks-list');
  if (!list) return;
  show('task-error', '');
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  const due = document.getElementById('task-due-on');
  await loadBooks();
  await loadOptions();
  await reloadList();
  if (due && !due.value && serverToday) due.value = serverToday;
}

function filterQuery() {
  const params = new URLSearchParams({ view });
  const pick = (id, name) => {
    const el = document.getElementById(id);
    if (el && el.value) params.set(name, el.value);
  };
  pick('task-filter-assignee', 'assignee');
  pick('task-filter-type', 'type');
  if (multiBook()) pick('task-filter-book', 'practitioner');
  return `?${params.toString()}`;
}

async function reloadList() {
  const list = document.getElementById('tasks-list');
  if (!list) return;
  document.querySelectorAll('[data-action="tasks-view"]').forEach(b => {
    const on = b.dataset.arg === view;
    b.classList.toggle('btn-gold', on);
    b.classList.toggle('btn-ghost', !on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  try {
    const d = await apiFetch(`/api/tasks${filterQuery()}`);
    items = d.tasks || [];
    serverToday = d.today || '';
    const note = document.getElementById('task-retention');
    if (note) {
      note.textContent = `Tamamlanan ve iptal edilen işler ${d.retention_days} gün sonra silinir (KVKK).`;
    }
    list.innerHTML = items.length ? items.map(renderRow).join('') : emptyState(EMPTY[view]);
  } catch (e) {
    items = [];
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
  refreshTasksBadge();
}

function button(action, text, arg, cls = 'btn-ghost') {
  return `<button type="button" class="btn ${cls} btn-sm" data-action="${action}" data-arg="${escapeHtml(arg)}">${escapeHtml(text)}</button>`;
}

function openLinkButton(t) {
  if (!t.link || !t.link.available) return '';
  if (t.link.type === 'client') {
    return `<button type="button" class="btn btn-ghost btn-sm" data-action="open-client" data-arg="${escapeHtml(t.link.id)}" data-arg2="clients">Danışan kartı</button>`;
  }
  if (t.link.type === 'application') return button('task-open-application', 'Başvuruyu aç', t.link.id);
  return button('task-open-appointments', 'Randevu defteri', t.practitioner);
}

function renderRow(t) {
  const st = STATUS[t.status] || { label: t.status, cls: '' };
  const meta = [
    `${t.status === 'open' ? 'Son gün' : 'Son gündü'}: ${dueText(t, serverToday)}`,
    t.assignee ? `Yapacak: ${t.assignee_name || t.assignee}` : 'Atanmamış',
  ];
  if (multiBook()) meta.push(`Defter: ${bookName(t.practitioner)}`);
  if (t.status === 'done' && t.done_at) meta.push(`Tamamlandı: ${whenText(t.done_at)}`);
  const badges = [
    t.overdue ? '<span class="badge badge-private task-late">Gecikti</span>' : '',
    t.priority === 'high' ? '<span class="badge badge-encrypted">Öncelikli</span>' : '',
    t.automatic ? '<span class="badge badge-series">Otomatik</span>' : '',
  ].join(' ');
  const actions = [openLinkButton(t)];
  if (t.status === 'open') {
    if (!t.assignee) actions.push(button('task-take', 'Üstlen', t.id));
    actions.push(button('task-done', 'Tamamlandı', t.id, 'btn-gold'));
    actions.push(button('task-cancel', 'İptal et', t.id));
  } else {
    actions.push(button('task-reopen', 'Yeniden aç', t.id));
  }
  return `
    <div class="record-card task-row${t.overdue ? ' task-overdue' : ''}">
      <div class="record-main">
        <div class="record-title">${badges} ${escapeHtml(taskTitle(t))}</div>
        <div class="record-meta">${escapeHtml(meta.join(' · '))}</div>
        <div class="app-actions">${actions.join('')}</div>
      </div>
      <span class="badge ${escapeHtml(st.cls)}">${escapeHtml(st.label)}</span>
    </div>`;
}

/* -- Actions ---------------------------------------------------------- */

export function setTasksView(next) {
  if (!EMPTY[next]) return;
  view = next;
  reloadList();
}

async function act(path, method, body, success) {
  show('task-error', '');
  show('task-success', '');
  try {
    await apiFetch(path, { method, body: body ? JSON.stringify(body) : undefined });
    await reloadList();
    if (success) show('task-success', success);
  } catch (e) {
    show('task-error', e.message);
  }
}

const taskPath = (id, action = '') => `/api/tasks/${encodeURIComponent(id)}${action ? `/${action}` : ''}`;

export const markTaskDone = id => act(taskPath(id, 'done'), 'POST', null, 'İş tamamlandı.');
export const cancelTask = id => act(taskPath(id, 'cancel'), 'POST', null, 'İş iptal edildi.');
export const reopenTask = id => act(taskPath(id, 'reopen'), 'POST', null, 'İş yeniden açıldı.');
export const takeTask = id => act(taskPath(id), 'PATCH', { assignee: user().username }, 'İşi üstlendiniz.');

export async function createTask(e) {
  if (e) e.preventDefault();
  show('task-form-error', '');
  show('task-success', '');
  const value = id => ((document.getElementById(id) || {}).value || '').trim();
  const body = {
    type: value('task-type'),
    due_on: value('task-due-on'),
    due_time: value('task-due-time') || null,
    priority: value('task-priority') || 'normal',
    assignee: value('task-assignee') || null,
  };
  const kind = value('task-link-type');
  if (kind) {
    if (!value('task-link-id')) {
      show('task-form-error', 'Bağlanacak kaydı seçin ya da “Bağlantı yok”u seçin.');
      return;
    }
    body.link_type = kind;
    body.link_id = value('task-link-id');
  }
  if (!body.due_on) {
    show('task-form-error', 'Son günü seçin.');
    return;
  }
  const q = user().role === 'secretary' ? `?practitioner=${encodeURIComponent(selectedBook())}` : '';
  try {
    await apiFetch(`/api/tasks${q}`, { method: 'POST', body: JSON.stringify(body) });
    const form = document.getElementById('task-form');
    if (form) form.reset();
    const due = document.getElementById('task-due-on');
    if (due) due.value = serverToday;
    fillFormChoices();
    view = body.due_on > serverToday ? 'upcoming' : 'today';
    await reloadList();
    show('task-success', 'İş eklendi.');
  } catch (ex) {
    show('task-form-error', ex.message);
  }
}

async function openLinkedApplication(id) {
  navigate('applications');
  await loadApplications();
  openApplication(id);
}

registerActions('click', {
  'tasks-view':             (el) => setTasksView(el.dataset.arg),
  'task-done':              (el) => markTaskDone(el.dataset.arg),
  'task-cancel':            (el) => cancelTask(el.dataset.arg),
  'task-reopen':            (el) => reopenTask(el.dataset.arg),
  'task-take':              (el) => takeTask(el.dataset.arg),
  'task-open-application':  (el) => openLinkedApplication(el.dataset.arg),
  'task-open-appointments': () => navigate('appointments'),
});
registerActions('change', {
  'tasks-filter':     () => reloadList(),
  'task-form-choice': () => fillFormChoices(),
});
registerActions('submit', {
  'create-task': (el, e) => createTask(e),
});

