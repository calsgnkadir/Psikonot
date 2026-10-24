/* appointments.js — the appointment book
 *
 * Practitioner and secretary: book, move, cancel, and mark whether the client
 * came or did not come. Clients do not sign in; the book is the practice's.
 *
 * Times are the practice's: the forms send the date and time picked
 * (local_date, local_time) and the server reads them in its own timezone
 * (VHV_TIMEZONE), as the calendar and the day views do; the list shows times
 * in that timezone too, whatever the browser's. Nothing here touches a
 * client's records: a secretary only ever sees names, client IDs and times.
 *
 * A secretary who runs several practitioners' books picks one at the top of
 * the page (frontdesk.js); the list and the booking form follow that choice.
 *
 * Rooms: an in-person appointment may be in one of the centre's rooms (the
 * room selector is hidden for online sessions). A room's day shows the books
 * the caller runs in full and every other book's only as "busy"; the date is
 * read, and the times shown, in the server's timezone (rooms.js).
 *
 * Weekly series: "Tekrarla" in the booking form previews every session of a
 * series (what is in its way, nothing written) before it is booked in one
 * go. A session of a series is cancelled on its own, or with every later one.
 * The calendar (calendar.js) opens the form at a slot with prefillBooking().
 */
import { apiFetch, escapeHtml, emptyState, LOCALE, formatLabel } from './utils.js';
import { loadBooks, bookQuery } from './frontdesk.js';
import { activeRooms, fetchRoomDay, roomDayHtml, isoDay } from './rooms.js';

const STATUS = {
  scheduled: { label: 'Planlandı', cls: 'badge-shared' },
  completed: { label: 'Geldi',      cls: 'badge-practitioner-only' },
  cancelled: { label: 'İptal edildi', cls: 'badge-private' },
  no_show:   { label: 'Gelmedi', cls: 'badge-encrypted' },
};

let appointments = [];
let rooms = [];   // the rooms in use: [{id, name, active}]
let serverZone = '';   // the practice's timezone, from the server

function when(iso) {
  const opts = { weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' };
  try {
    return new Date(iso).toLocaleString(LOCALE, serverZone ? { ...opts, timeZone: serverZone } : opts);
  } catch (e) {
    return new Date(iso).toLocaleString(LOCALE, opts);
  }
}

// The wall-clock date (YYYY-MM-DD) and time (HH:MM) of an instant in the
// practice's timezone (the browser's own if it is not known), as the date
// and time inputs want them.
export function wallClock(iso, timeZone = serverZone) {
  const opts = { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
  let parts;
  try {
    parts = new Intl.DateTimeFormat('en-CA', timeZone ? { ...opts, timeZone } : opts).formatToParts(new Date(iso));
  } catch (e) {
    parts = new Intl.DateTimeFormat('en-CA', opts).formatToParts(new Date(iso));
  }
  const get = type => (parts.find(p => p.type === type) || {}).value;
  return { date: `${get('year')}-${get('month')}-${get('day')}`, time: `${get('hour')}:${get('minute')}` };
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
  if (list) list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  closeSeriesPreview();   // a preview belongs to the book it was made for
  await loadBooks();
  loadBookableClients();
  loadBookableRooms();
  if (!list) return;
  try {
    const d = await apiFetch(`/api/appointments${bookQuery()}`);
    appointments = d.appointments;
    serverZone = d.timezone || '';
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
    <h3 class="appt-section-title">Yaklaşan</h3>
    ${upcoming.length ? upcoming.map(renderRow).join('') : emptyState('Yaklaşan randevu yok.')}
    <h3 class="appt-section-title">Geçmiş</h3>
    ${past.length ? past.map(renderRow).join('') : emptyState('Son 30 günde randevu yok.')}`;
}

// A session of a weekly series carries a small marker (the series' id is an
// opaque server value, used only as a title).
export function seriesMarker(a) {
  return a.series_id
    ? ` <span class="badge badge-series" title="Tekrarlanan seri">Seri</span>`
    : '';
}

function renderRow(a) {
  const st = STATUS[a.status] || { label: a.status, cls: '' };
  const id = escapeHtml(a.id);
  const who = `${escapeHtml(a.client_name)} <span class="appt-muted">${escapeHtml(a.patient_id)}</span>`;
  const room = a.room_name ? ` · ${escapeHtml(a.room_name)}` : '';
  return `
    <div class="record-card appt-row" id="appt-${id}" style="cursor:default">
      <div class="record-main">
        <div class="record-title">${escapeHtml(when(a.starts_at))}${seriesMarker(a)}</div>
        <div class="record-meta">${who} · ${escapeHtml(formatLabel(a.session_format))}${room} · ${escapeHtml(String(a.duration_min))} dk</div>
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
    if (a.series_id) {
      return button('appt-move', 'Randevuyu taşı') + button('appt-status', 'Bu seansı iptal et', 'cancelled')
        + button('appt-cancel-following', 'Bundan sonrakileri iptal et');
    }
    return button('appt-move', 'Randevuyu taşı') + button('appt-status', 'İptal et', 'cancelled');
  }
  return button('appt-status', 'Geldi', 'completed') + button('appt-status', 'Gelmedi', 'no_show');
}

// This session of a series and every later one still planned.
export async function cancelFollowing(id) {
  if (!confirm('Bu seans ve serinin bundan sonraki bütün planlanmış seansları iptal edilsin mi?')) return;
  try {
    const d = await apiFetch(`/api/appointments/${encodeURIComponent(id)}/cancel-following`, { method: 'POST' });
    await loadAppointments();
    showMessage('appt-success', `${d.cancelled} seans iptal edildi.`);  // xss-reviewed: showMessage sets textContent
  } catch (e) {
    showMessage('appt-error', e.message);
  }
}

/* -- Actions ---------------------------------------------------------- */

const CONFIRM = {
  cancelled: 'Bu randevu iptal edilsin mi?',
  completed: 'Danışan geldi olarak işaretlensin mi?',
  no_show: 'Danışan gelmedi olarak işaretlensin mi?',
};

export async function setAppointmentStatus(id, status) {
  if (!confirm(CONFIRM[status] || 'Bu randevu değiştirilsin mi?')) return;
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
    list.push(option(selectedId, `${current ? current.room_name : selectedId} (kullanım dışı)`));
  }
  return option('', 'Oda yok') + list.join('');
}

// Swap the row's buttons for a date and a time picker, in the practice's
// timezone (and a room selector for an in-person session).
export function startMove(id) {
  const a = appointments.find(x => x.id === id);
  const box = document.getElementById(`appt-actions-${id}`);
  if (!a || !box) return;
  const safeId = escapeHtml(id);
  const at = wallClock(a.starts_at);
  const roomSelect = a.session_format === 'Online' ? ''
    : `<select id="appt-move-room" aria-label="Oda">${roomOptions(a.room_id)}</select>`;
  box.innerHTML = `
    <input type="date" id="appt-move-date" value="${escapeHtml(at.date)}" aria-label="Yeni tarih" />
    <input type="time" id="appt-move-time" step="900" value="${escapeHtml(at.time)}" aria-label="Yeni saat" />
    ${roomSelect}
    <button type="button" class="btn btn-gold btn-sm" data-action="appt-move-save" data-arg="${safeId}">Kaydet</button>
    <button type="button" class="btn btn-ghost btn-sm" data-action="appt-move-cancel">Vazgeç</button>`;
}

export async function saveMove(id) {
  const date = (document.getElementById('appt-move-date') || {}).value;
  const clock = (document.getElementById('appt-move-time') || {}).value;
  if (!date || !clock) {
    showMessage('appt-error', 'Bir tarih ve saat seçin.');
    return;
  }
  // The server reads the date and time in the practice's timezone.
  const body = { local_date: date, local_time: clock };
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
      o.textContent = 'Henüz danışan yok';
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
      : '<option value="">Henüz oda yok</option>';
  }
  const date = document.getElementById('room-day-date');
  if (date && !date.value) date.value = isoDay(new Date());
  toggleRoomField();
  applyPendingRoom();
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

// The booking form as a request body, or null (with a message) when the date
// or time is missing.
function bookingBody() {
  const date = document.getElementById('appt-date').value;
  const clock = document.getElementById('appt-time').value;
  if (!date || !clock) {
    showMessage('appt-error', 'Bir tarih ve saat seçin.');
    return null;
  }
  const sessionFormat = document.getElementById('appt-format').value;
  const room = document.getElementById('appt-room');
  const roomId = sessionFormat === 'Online' || !room ? '' : room.value;
  // The date and time as picked: the server reads them in the practice's
  // timezone, like the calendar that may have filled them in.
  return {
    patient_id: document.getElementById('appt-client').value,
    local_date: date,
    local_time: clock,
    duration_min: Number(document.getElementById('appt-duration').value),
    session_format: sessionFormat,
    room_id: roomId || null,
  };
}

export async function bookAppointment(e) {
  if (e) e.preventDefault();
  showMessage('appt-error', '');
  showMessage('appt-success', '');
  if (repeatChoice()) {
    previewSeries();
    return;
  }
  const body = bookingBody();
  if (!body) return;
  try {
    const d = await apiFetch(`/api/appointments${bookQuery()}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    await loadAppointments();   // clears old messages, so the new one comes after
    showMessage('appt-success', `Randevu verildi: ${d.appointment.client_name}, ${when(d.appointment.starts_at)}.`);  // xss-reviewed: showMessage sets textContent
  } catch (ex) {
    showMessage('appt-error', ex.message);
  }
}

/* -- Weekly series ---------------------------------------------------- *
 * "Tekrarla" turns the booking form into a series: the server lists every
 * session with what is in its way (preview, nothing written), the user keeps
 * or leaves out each one, and the series is booked in one go. If a kept
 * session was taken in the meantime, the server books nothing and the
 * preview is shown again.
 */

const CONFLICT_LABEL = {
  past: 'Tarihi geçmiş',
  practitioner: 'Psikoloğun bu saatte başka randevusu var',
  room: 'Oda bu saatte dolu',
};

let seriesRequest = null;   // the body that was previewed
let seriesPreview = [];     // its sessions, as the server listed them

function repeatChoice() {
  const select = document.getElementById('appt-repeat');
  return select ? select.value : '';
}

// Show the end fields only for a series, and only the chosen one of them.
export function toggleRepeatFields() {
  const repeat = repeatChoice();
  const end = (document.getElementById('appt-repeat-end') || {}).value || 'count';
  const show = (id, on) => { const el = document.getElementById(id); if (el) el.hidden = !on; };
  show('appt-repeat-end-group', !!repeat);
  show('appt-repeat-count-group', !!repeat && end === 'count');
  show('appt-repeat-until-group', !!repeat && end === 'until');
  const submit = document.getElementById('appt-submit');
  if (submit) submit.textContent = repeat ? 'Seansları önizle' : 'Randevu ver';
  if (!repeat) closeSeriesPreview();
}

function seriesBody() {
  const body = bookingBody();
  if (!body) return null;
  body.frequency = repeatChoice();
  if ((document.getElementById('appt-repeat-end') || {}).value === 'until') {
    const until = document.getElementById('appt-repeat-until').value;
    if (!until) {
      showMessage('appt-error', 'Son seansın tarihini seçin.');
      return null;
    }
    body.until = until;
  } else {
    body.count = Number(document.getElementById('appt-repeat-count').value);
  }
  return body;
}

function dayLabel(isoDate) {
  // Read at noon, so the browser's own timezone cannot move it to another day.
  return new Date(`${isoDate}T12:00:00`).toLocaleDateString(LOCALE, {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
  });
}

export async function previewSeries(body = null) {
  const box = document.getElementById('appt-series-preview');
  if (!box) return;
  const request = body || seriesBody();
  if (!request) return;
  box.hidden = false;
  box.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await apiFetch(`/api/appointments/series/preview${bookQuery()}`, {
      method: 'POST', body: JSON.stringify(request),
    });
    seriesRequest = request;
    seriesPreview = d.occurrences;
    renderSeriesPreview(d.timezone);
  } catch (ex) {
    box.hidden = true;
    showMessage('appt-error', ex.message);
  }
}

function renderSeriesPreview(timeZone) {
  const box = document.getElementById('appt-series-preview');
  if (!box) return;
  const rows = seriesPreview.map((o, i) => {
    const blocked = o.conflicts.length > 0;
    const past = o.conflicts.includes('past');
    const reasons = o.conflicts.map(c => CONFLICT_LABEL[c] || c).join(' · ');
    return `
      <label class="series-row${blocked ? ' series-row-blocked' : ''}">
        <input type="checkbox" class="series-keep" data-index="${i}"${blocked ? '' : ' checked'}${past ? ' disabled' : ''} />
        <span class="series-when">${escapeHtml(dayLabel(o.date))} · ${escapeHtml(o.time)}</span>
        <span class="series-state">${blocked ? escapeHtml(reasons) : 'Uygun'}</span>
      </label>`;
  }).join('');
  const free = seriesPreview.filter(o => !o.conflicts.length).length;
  const blocked = seriesPreview.length - free;
  box.innerHTML = `
    <h4 class="series-title">Seri önizlemesi</h4>
    <p class="appt-muted">${escapeHtml(String(seriesPreview.length))} seans, saatler ${escapeHtml(timeZone)} saat dilimindedir.
      ${blocked ? `${escapeHtml(String(blocked))} seans uygun değil; işareti kaldırılan seanslar atlanır.` : 'Bütün seanslar uygun.'}
      Henüz hiçbir randevu verilmedi.</p>
    <div class="series-list">${rows}</div>
    <div class="appt-actions" style="margin-top:12px">
      <button type="button" class="btn btn-gold btn-sm" data-action="appt-series-confirm">Seriyi oluştur</button>
      <button type="button" class="btn btn-ghost btn-sm" data-action="appt-series-cancel">Vazgeç</button>
    </div>`;
}

export function closeSeriesPreview() {
  const box = document.getElementById('appt-series-preview');
  if (box) { box.hidden = true; box.innerHTML = ''; }
  seriesRequest = null;
  seriesPreview = [];
}

export async function confirmSeries() {
  if (!seriesRequest) return;
  showMessage('appt-error', '');
  const kept = new Set([...document.querySelectorAll('#appt-series-preview .series-keep')]
    .filter(box => box.checked).map(box => Number(box.dataset.index)));
  const skip = seriesPreview.filter((o, i) => !kept.has(i)).map(o => o.date);
  if (skip.length === seriesPreview.length) {
    showMessage('appt-error', 'Seriden en az bir seans seçin.');
    return;
  }
  const request = seriesRequest;
  try {
    const d = await apiFetch(`/api/appointments/series${bookQuery()}`, {
      method: 'POST', body: JSON.stringify({ ...request, skip }),
    });
    closeSeriesPreview();
    await loadAppointments();
    const first = d.appointments[0];
    showMessage('appt-success', `Seri oluşturuldu: ${d.appointments.length} seans, ilki ${when(first.starts_at)}.`);  // xss-reviewed: showMessage sets textContent
  } catch (ex) {
    showMessage('appt-error', ex.message);
    // Something changed since the preview: show the sessions as they are now.
    if (ex.status === 409) previewSeries(request);
  }
}

/* -- Prefill from the calendar --------------------------------------- */

// Open the booking form at a date and time (and room) picked on the
// calendar. The values are the practice's (server's) wall-clock time.
export function prefillBooking({ date, time, roomId } = {}) {
  const set = (id, value) => { const el = document.getElementById(id); if (el && value) el.value = value; };
  set('appt-date', date);
  set('appt-time', time);
  const format = document.getElementById('appt-format');
  if (roomId && format) format.value = 'In-person';
  toggleRoomField();
  // The rooms load with the page; choose the room once they are there.
  pendingRoom = roomId || '';
  applyPendingRoom();
  const card = document.getElementById('appointment-book-card');
  if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

let pendingRoom = '';

function applyPendingRoom() {
  const select = document.getElementById('appt-room');
  if (!pendingRoom || !select) return;
  if ([...select.options].some(o => o.value === pendingRoom)) {
    select.value = pendingRoom;
    pendingRoom = '';
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
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await fetchRoomDay(roomId, date);
    list.innerHTML = roomDayHtml(d, 'Dolu (başka bir psikolog)');
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
    serverZone = d.timezone || serverZone;
    const now = Date.now();
    const next = d.appointments
      .filter(a => a.status === 'scheduled' && new Date(a.ends_at).getTime() > now)
      .sort((x, y) => x.starts_at.localeCompare(y.starts_at))
      .slice(0, 3);
    box.innerHTML = next.length
      ? next.map(a => `
          <div class="appt-mini">
            <strong>${escapeHtml(when(a.starts_at))}</strong>
            <span>${escapeHtml(a.client_name)} · ${escapeHtml(formatLabel(a.session_format))}${a.room_name ? ` · ${escapeHtml(a.room_name)}` : ''}</span>
          </div>`).join('')
      : '<div class="appt-muted">Yaklaşan randevu yok.</div>';
  } catch (e) {
    box.innerHTML = `<div class="appt-muted">${escapeHtml(e.message)}</div>`;
  }
}
