/* frontdesk.js — a center's front desk
 *
 * A secretary may run the appointment books of several practitioners. This
 * module knows which books they run (GET /api/appointments/practitioners):
 *
 *   - the practitioner picker on the Appointments and Clients pages, shown only
 *     to a secretary who runs more than one book. Requests for one book carry
 *     ?practitioner=<username>; the server checks the link on every request.
 *   - the "Today" page: one day of every book, grouped by practitioner. The day
 *     is the practice's (the server's timezone), and times are shown in it.
 *
 * Nothing here touches a record: names, client IDs and times only. Every value
 * from the server is escaped or set as textContent.
 */
import { apiFetch, escapeHtml, emptyState, getCurrentUser } from './utils.js';

const DAY_STATUS = {
  scheduled: { label: 'Scheduled', cls: 'badge-shared' },
  completed: { label: 'Came',      cls: 'badge-practitioner-only' },
  cancelled: { label: 'Cancelled', cls: 'badge-private' },
  no_show:   { label: 'Did not come', cls: 'badge-encrypted' },
};

let books = [];
let selectedBook = '';

const isSecretary = () => (getCurrentUser() || {}).role === 'secretary';

/* -- Which book ------------------------------------------------------- */

// Reads the books again on every page load, so a removed link shows at once.
export async function loadBooks() {
  if (!isSecretary()) {
    books = [];
    selectedBook = '';
    renderBookPickers();
    return;
  }
  try {
    books = (await apiFetch('/api/appointments/practitioners')).practitioners || [];
  } catch (e) {
    books = [];
  }
  if (!books.some(p => p.username === selectedBook)) {
    selectedBook = books.length ? books[0].username : '';
  }
  renderBookPickers();
}

// The query string for a request about one book; empty when there is nothing
// to choose (a practitioner, or a secretary with one book).
export function bookQuery() {
  return books.length > 1 && selectedBook ? `?practitioner=${encodeURIComponent(selectedBook)}` : '';
}

export function chooseBook(username) {
  if (books.some(p => p.username === username)) selectedBook = username;
  renderBookPickers();
}

function renderBookPickers() {
  document.querySelectorAll('select.book-picker').forEach(select => {
    const bar = select.closest('.book-picker-bar');
    if (bar) bar.hidden = books.length <= 1;
    select.replaceChildren();
    books.forEach(p => {
      const o = document.createElement('option');
      o.value = p.username;
      o.textContent = p.full_name;
      o.selected = p.username === selectedBook;
      select.appendChild(o);
    });
  });
}

/* -- The day ---------------------------------------------------------- */

// YYYY-MM-DD of a Date in the browser's calendar.
function isoDay(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function clockIn(iso, timeZone) {
  const opts = { hour: '2-digit', minute: '2-digit' };
  try {
    return new Date(iso).toLocaleTimeString('en-GB', { ...opts, timeZone });
  } catch (e) {
    return new Date(iso).toLocaleTimeString('en-GB', opts);
  }
}

export function shiftDay(delta) {
  const input = document.getElementById('day-date');
  if (!input) return;
  const step = Number(delta);
  if (!step || !input.value) {
    input.value = isoDay(new Date());
  } else {
    const date = new Date(`${input.value}T12:00:00`);
    date.setDate(date.getDate() + step);
    input.value = isoDay(date);
  }
  loadDay();
}

export async function loadDay() {
  const input = document.getElementById('day-date');
  const list = document.getElementById('day-list');
  const error = document.getElementById('day-error');
  if (!input || !list) return;
  if (!input.value) input.value = isoDay(new Date());
  if (error) { error.textContent = ''; error.style.display = 'none'; }
  list.innerHTML = '<div class="loading-spinner">Loading...</div>';
  try {
    const d = await apiFetch(`/api/appointments/day?date=${encodeURIComponent(input.value)}`);
    renderDay(d);
  } catch (e) {
    list.innerHTML = '';
    if (error) { error.textContent = e.message; error.style.display = 'block'; }
  }
}

function renderDay(day) {
  const list = document.getElementById('day-list');
  const zone = document.getElementById('day-timezone');
  if (zone) zone.textContent = `Times in ${day.timezone}.`;
  if (!day.practitioners.length) {
    list.innerHTML = emptyState('You do not run any appointment book.');
    return;
  }
  list.innerHTML = day.practitioners.map(p => `
    <div class="glass day-book">
      <h3 class="appt-section-title">${escapeHtml(p.full_name)} <span class="appt-muted">@${escapeHtml(p.username)}</span></h3>
      ${p.appointments.length
        ? p.appointments.map(a => dayRow(a, day.timezone)).join('')
        : emptyState('No appointments this day.')}
    </div>`).join('');
}

function dayRow(a, timeZone) {
  const st = DAY_STATUS[a.status] || { label: a.status, cls: '' };
  const time = `${clockIn(a.starts_at, timeZone)}–${clockIn(a.ends_at, timeZone)}`;  // xss-reviewed: escaped below
  return `
    <div class="record-card appt-row" style="cursor:default">
      <div class="record-main">
        <div class="record-title">${escapeHtml(time)}</div>
        <div class="record-meta">${escapeHtml(a.client_name)} <span class="appt-muted">${escapeHtml(a.patient_id)}</span> · ${escapeHtml(a.session_format)} · ${escapeHtml(String(a.duration_min))} min</div>
      </div>
      <span class="badge ${escapeHtml(st.cls)}">${escapeHtml(st.label)}</span>
    </div>`;
}
