/* appointments.js — the appointment book
 *
 * Practitioner and secretary: book, move, cancel, and mark whether the client
 * came or did not come. Clients do not sign in; the book is the practice's.
 *
 * Times go to the server as UTC ISO strings and are shown in the browser's
 * local time. Nothing here touches a client's records: a secretary only ever
 * sees names, client IDs and times.
 *
 * A secretary who runs several practitioners' books picks one at the top of
 * the page (frontdesk.js); the list and the booking form follow that choice.
 *
 * Rooms: an in-person appointment may be in one of the centre's rooms (the
 * room selector is hidden for online sessions). A room's day shows the books
 * the caller runs in full and every other book's only as "busy"; the date is
 * read, and the times shown, in the server's timezone (rooms.js).
 */
import { apiFetch, escapeHtml, emptyState } from './utils.js';
import { loadBooks, bookQuery } from './frontdesk.js';
import { activeRooms, fetchRoomDay, roomDayHtml, isoDay } from './rooms.js';

const STATUS = {
  scheduled: { label: 'Scheduled', cls: 'badge-shared' },
  completed: { label: 'Came',      cls: 'badge-practitioner-only' },
  cancelled: { label: 'Cancelled', cls: 'badge-private' },
  no_show:   { label: 'Did not come', cls: 'badge-encrypted' },
};

let appointments = [];
let rooms = [];   // the rooms in use: [{id, name, active}]

function when(iso) {
  return new Date(iso).toLocaleString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

// "2026-10-01T10:00" in local time, the format a datetime-local input wants.
function localInputValue(iso) {
  const date = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function showMessage(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

/* -- Page ------------------------------------------------------------- */

export async function loadAppointments() {
  showMessage('appt-error', '');
  showMessage('appt-success', '');
  const list = document.getElementById('appointments-list');
  if (list) list.innerHTML = '<div class="loading-spinner">Loading...</div>';
  await loadBooks();
  loadBookableClients();
  loadBookableRooms();
  if (!list) return;
  try {
    const d = await apiFetch(`/api/appointments${bookQuery()}`);
    appointments = d.appointments;
    renderAppointments();
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

function renderAppointments() {
  const list = document.getElementById('appointments-list');
  if (!list) return;
  const now = Date.now();
  const upcoming = appointments
    .filter(a => a.status === 'scheduled' && new Date(a.ends_at).getTime() > now)
    .sort((x, y) => x.starts_at.localeCompare(y.starts_at));
  const past = appointments
    .filter(a => !upcoming.includes(a))
    .sort((x, y) => y.starts_at.localeCompare(x.starts_at));

  list.innerHTML = `
    <h3 class="appt-section-title">Upcoming</h3>
    ${upcoming.length ? upcoming.map(renderRow).join('') : emptyState('No upcoming appointments.')}
    <h3 class="appt-section-title">Earlier</h3>
    ${past.length ? past.map(renderRow).join('') : emptyState('Nothing in the last 30 days.')}`;
}

function renderRow(a) {
  const st = STATUS[a.status] || { label: a.status, cls: '' };
  const id = escapeHtml(a.id);
  const who = `${escapeHtml(a.client_name)} <span class="appt-muted">${escapeHtml(a.patient_id)}</span>`;
  const room = a.room_name ? ` · ${escapeHtml(a.room_name)}` : '';
  return `
    <div class="record-card appt-row" id="appt-${id}" style="cursor:default">
      <div class="record-main">
        <div class="record-title">${escapeHtml(when(a.starts_at))}</div>
        <div class="record-meta">${who} · ${escapeHtml(a.session_format)}${room} · ${escapeHtml(String(a.duration_min))} min</div>
      </div>
      <span class="badge ${st.cls}">${escapeHtml(st.label)}</span>
      <div class="appt-actions" id="appt-actions-${id}">${renderActions(a)}</div>
    </div>`;
}

function renderActions(a) {
  const id = escapeHtml(a.id);
  const button = (action, label, arg2 = '', argValue = id) =>
    `<button type="button" class="btn btn-ghost btn-sm" data-action="${action}" data-arg="${argValue}"${arg2 ? ` data-arg2="${arg2}"` : ''}>${label}</button>`;
  if (a.status !== 'scheduled') return '';
  const started = new Date(a.starts_at).getTime() <= Date.now();
  if (!started) {
    return button('appt-move', 'Move') + button('appt-status', 'Cancel', 'cancelled');
  }
  return button('appt-status', 'Came', 'completed') + button('appt-status', 'Did not come', 'no_show');
}

/* -- Actions ---------------------------------------------------------- */

const CONFIRM = {
  cancelled: 'Cancel this appointment?',
  completed: 'Mark that the client came?',
  no_show: 'Mark that the client did not come?',
};

export async function setAppointmentStatus(id, status) {
  if (!confirm(CONFIRM[status] || 'Change this appointment?')) return;
  try {
    await apiFetch(`/api/appointments/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: JSON.stringify({ status }),
    });
    loadAppointments();
  } catch (e) {
    showMessage('appt-error', e.message);
  }
}

// <option>s for a room selector: "No room", then the rooms in use. A room
// that has been switched off stays listed when it is the current one, so the
// server can say why it cannot be kept.
function roomOptions(selectedId) {
  const option = (id, label) =>
    `<option value="${escapeHtml(id)}"${id === (selectedId || '') ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  const list = rooms.map(room => option(room.id, room.name));
  if (selectedId && !rooms.some(room => room.id === selectedId)) {
    const current = appointments.find(x => x.room_id === selectedId);
    list.push(option(selectedId, `${current ? current.room_name : selectedId} (switched off)`));
  }
  return option('', 'No room') + list.join('');
}

// Swap the row's buttons for a date-time picker (and a room selector for an
// in-person session).
export function startMove(id) {
  const a = appointments.find(x => x.id === id);
  const box = document.getElementById(`appt-actions-${id}`);
  if (!a || !box) return;
  const safeId = escapeHtml(id);
  const roomSelect = a.session_format === 'Online' ? ''
    : `<select id="appt-move-room" aria-label="Room">${roomOptions(a.room_id)}</select>`;
  box.innerHTML = `
    <input type="datetime-local" id="appt-move-input" value="${escapeHtml(localInputValue(a.starts_at))}" />
    ${roomSelect}
    <button type="button" class="btn btn-gold btn-sm" data-action="appt-move-save" data-arg="${safeId}">Save</button>
    <button type="button" class="btn btn-ghost btn-sm" data-action="appt-move-cancel">Back</button>`;
}

export async function saveMove(id) {
  const input = document.getElementById('appt-move-input');
  if (!input || !input.value) return;
  const body = { starts_at: new Date(input.value).toISOString() };
  const room = document.getElementById('appt-move-room');
  if (room) body.room_id = room.value || null;
  try {
    await apiFetch(`/api/appointments/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: JSON.stringify(body),
    });
    loadAppointments();
  } catch (e) {
    showMessage('appt-error', e.message);
  }
}

export function cancelMove() {
  renderAppointments();
}

/* -- Booking form ----------------------------------------------------- */

async function loadBookableClients() {
  const select = document.getElementById('appt-client');
  if (!select) return;
  try {
    const d = await apiFetch(`/api/clients${bookQuery()}`);
    select.innerHTML = '';
    d.clients.forEach(c => {
      const o = document.createElement('option');
      o.value = c.patient_id;
      o.textContent = `${c.full_name} (${c.patient_id})`;
      select.appendChild(o);
    });
    if (!d.clients.length) {
      const o = document.createElement('option');
      o.value = '';
      o.textContent = 'No clients yet';
      select.appendChild(o);
    }
  } catch (e) {
    showMessage('appt-error', e.message);
  }
}

async function loadBookableRooms() {
  rooms = await activeRooms();
  const select = document.getElementById('appt-room');
  if (select) select.innerHTML = roomOptions('');
  const day = document.getElementById('room-day-room');
  if (day) {
    day.innerHTML = rooms.length
      ? rooms.map(room => `<option value="${escapeHtml(room.id)}">${escapeHtml(room.name)}</option>`).join('')
      : '<option value="">No rooms yet</option>';
  }
  const date = document.getElementById('room-day-date');
  if (date && !date.value) date.value = isoDay(new Date());
  toggleRoomField();
}

// An online session takes no room: hide the selector and clear it.
export function toggleRoomField() {
  const format = document.getElementById('appt-format');
  const group = document.getElementById('appt-room-group');
  const select = document.getElementById('appt-room');
  if (!format || !group || !select) return;
  const online = format.value === 'Online';
  group.hidden = online || !rooms.length;
  if (online) select.value = '';
}

export async function bookAppointment(e) {
  if (e) e.preventDefault();
  showMessage('appt-error', '');
  showMessage('appt-success', '');
  const date = document.getElementById('appt-date').value;
  const clock = document.getElementById('appt-time').value;
  if (!date || !clock) {
    showMessage('appt-error', 'Choose a date and a time.');
    return;
  }
  const sessionFormat = document.getElementById('appt-format').value;
  const room = document.getElementById('appt-room');
  const roomId = sessionFormat === 'Online' || !room ? '' : room.value;
  try {
    const d = await apiFetch(`/api/appointments${bookQuery()}`, {
      method: 'POST',
      body: JSON.stringify({
        patient_id: document.getElementById('appt-client').value,
        starts_at: new Date(`${date}T${clock}`).toISOString(),
        duration_min: Number(document.getElementById('appt-duration').value),
        session_format: sessionFormat,
        room_id: roomId || null,
      }),
    });
    await loadAppointments();   // clears old messages, so the new one comes after
    showMessage('appt-success', `Booked: ${d.appointment.client_name}, ${when(d.appointment.starts_at)}.`);  // xss-reviewed: showMessage sets textContent
  } catch (ex) {
    showMessage('appt-error', ex.message);
  }
}

/* -- A room's day ----------------------------------------------------- */

export async function showRoomDay(e) {
  if (e) e.preventDefault();
  const list = document.getElementById('room-day-list');
  const roomId = (document.getElementById('room-day-room') || {}).value;
  const date = (document.getElementById('room-day-date') || {}).value;
  if (!list || !roomId || !date) return;
  // The server reads the date in its own timezone (rooms.js).
  list.innerHTML = '<div class="loading-spinner">Loading...</div>';
  try {
    const d = await fetchRoomDay(roomId, date);
    list.innerHTML = roomDayHtml(d, 'Busy (another practitioner)');
  } catch (ex) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(ex.message)}</div>`;
  }
}

/* -- Practitioner dashboard: the next few sessions ------------------- */

export async function loadUpcomingAppointments() {
  const box = document.getElementById('dashboard-upcoming');
  if (!box) return;
  try {
    const d = await apiFetch('/api/appointments');
    const now = Date.now();
    const next = d.appointments
      .filter(a => a.status === 'scheduled' && new Date(a.ends_at).getTime() > now)
      .sort((x, y) => x.starts_at.localeCompare(y.starts_at))
      .slice(0, 3);
    box.innerHTML = next.length
      ? next.map(a => `
          <div class="appt-mini">
            <strong>${escapeHtml(when(a.starts_at))}</strong>
            <span>${escapeHtml(a.client_name)} · ${escapeHtml(a.session_format)}${a.room_name ? ` · ${escapeHtml(a.room_name)}` : ''}</span>
          </div>`).join('')
      : '<div class="appt-muted">No upcoming appointments.</div>';
  } catch (e) {
    box.innerHTML = `<div class="appt-muted">${escapeHtml(e.message)}</div>`;
  }
}
