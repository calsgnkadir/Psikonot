/* calendar.js — the week calendar ("Takvim")
 *
 * Practitioner and secretary: a Monday-to-Sunday grid of one book, or of one
 * room, over the hours of the working day (08:00-21:00 unless the user picks
 * other hours; the choice is remembered in this browser only).
 *
 *   - Book: GET /api/appointments/week. A secretary who runs several books
 *     picks one with the same practitioner picker as the Appointments page
 *     (frontdesk.js), so the booking form follows the same book.
 *   - Room: GET /api/rooms/{id}/week. The books the caller runs show in full,
 *     every other book only as "Dolu" (the server sends nothing more).
 *
 * The week, and every time on the grid, is the practice's: the server's
 * timezone, whatever the browser's. Clicking a free half hour opens the
 * booking form at that date and time (and room). Narrow screens show one day
 * at a time.
 *
 * Everything from the server is escaped; buttons declare data-action only.
 */
import { apiFetch, escapeHtml, emptyState, LOCALE, formatLabel } from './utils.js';
import { loadBooks, bookQuery } from './frontdesk.js';
import { activeRooms, isoDay } from './rooms.js';
import { prefillBooking } from './appointments.js';
import { navigate } from './dashboard.js';

const SLOT_MIN = 30;   // one clickable cell
const SLOT_PX = 24;    // its height
const HOURS_KEY = 'psikonot.calendar.hours';
const DEFAULT_HOURS = { from: 8, to: 21 };

const STATUS_LABEL = { completed: 'Geldi', no_show: 'Gelmedi' };

let weekOf = '';        // any day of the shown week (YYYY-MM-DD)
let mode = 'book';      // 'book' or 'room'
let roomList = [];
let dayIndex = null;    // the day shown on a narrow screen (0 = Monday)
let lastWeek = null;    // the last answer, to redraw without asking again

/* -- Working hours (remembered in this browser only) ------------------ */

function loadHours() {
  try {
    const saved = JSON.parse(localStorage.getItem(HOURS_KEY) || 'null');
    if (saved && Number.isInteger(saved.from) && Number.isInteger(saved.to) && saved.from < saved.to
        && saved.from >= 0 && saved.to <= 24) {
      return saved;
    }
  } catch (e) { /* no storage: the default */ }
  return { ...DEFAULT_HOURS };
}

function saveHours(hours) {
  try { localStorage.setItem(HOURS_KEY, JSON.stringify(hours)); } catch (e) { /* not kept */ }
}

let hours = loadHours();

function fillHourSelects() {
  const option = (h, chosen) =>
    `<option value="${h}"${h === chosen ? ' selected' : ''}>${String(h).padStart(2, '0')}:00</option>`;
  const from = document.getElementById('cal-from');
  const to = document.getElementById('cal-to');
  if (from) from.innerHTML = Array.from({ length: 13 }, (_, i) => option(i + 5, hours.from)).join('');
  if (to) to.innerHTML = Array.from({ length: 10 }, (_, i) => option(i + 15, hours.to)).join('');
}

export function changeHours() {
  const from = Number((document.getElementById('cal-from') || {}).value);
  const to = Number((document.getElementById('cal-to') || {}).value);
  if (Number.isInteger(from) && Number.isInteger(to) && from < to) {
    hours = { from, to };
    saveHours(hours);
  }
  fillHourSelects();
  if (lastWeek) renderWeek(lastWeek);
}

/* -- Dates and times in the practice's timezone ----------------------- */

// The date (YYYY-MM-DD) and minutes after midnight of an instant, read in
// `timeZone` (the browser's own if the zone is unknown).
function partsIn(iso, timeZone) {
  const date = new Date(iso);
  let parts;
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(date);
  } catch (e) {
    parts = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date);
  }
  const get = type => (parts.find(p => p.type === type) || {}).value;
  return { day: `${get('year')}-${get('month')}-${get('day')}`, minutes: Number(get('hour')) * 60 + Number(get('minute')) };
}

// YYYY-MM-DD `n` days after a YYYY-MM-DD, whatever the browser's timezone.
function addDays(isoDate, n) {
  const date = new Date(`${isoDate}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + n);
  return date.toISOString().slice(0, 10);
}

const hhmm = minutes => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;

function dayHeading(isoDate) {
  return new Date(`${isoDate}T12:00:00Z`).toLocaleDateString(LOCALE, {
    weekday: 'short', day: '2-digit', month: 'short', timeZone: 'UTC',
  });
}

/* -- Loading ---------------------------------------------------------- */

export async function loadCalendar() {
  const input = document.getElementById('cal-week');
  if (input && !input.value) input.value = isoDay(new Date());
  weekOf = input ? input.value : isoDay(new Date());
  fillHourSelects();
  await loadBooks();
  roomList = await activeRooms();
  fillRooms();
  showModeControls();
  await fetchWeek();
}

function fillRooms() {
  const select = document.getElementById('cal-room');
  if (!select) return;
  const chosen = select.value;
  select.replaceChildren();
  roomList.forEach(room => {
    const o = document.createElement('option');
    o.value = room.id;
    o.textContent = room.name;
    o.selected = room.id === chosen;
    select.appendChild(o);
  });
}

function showModeControls() {
  const roomBar = document.getElementById('cal-room-group');
  const bookBar = document.getElementById('cal-book-bar');
  if (roomBar) roomBar.hidden = mode !== 'room';
  // The practitioner picker shows itself only for a desk with several books.
  if (bookBar) bookBar.classList.toggle('cal-hidden', mode !== 'book');
}

export function changeMode() {
  const select = document.getElementById('cal-mode');
  mode = select && select.value === 'room' ? 'room' : 'book';
  showModeControls();
  fetchWeek();
}

export function changeWeek() {
  const input = document.getElementById('cal-week');
  if (input && input.value) weekOf = input.value;
  dayIndex = null;
  fetchWeek();
}

export function shiftWeek(delta) {
  const step = Number(delta);
  const input = document.getElementById('cal-week');
  weekOf = step && weekOf ? addDays(weekOf, 7 * step) : isoDay(new Date());
  if (input) input.value = weekOf;
  dayIndex = null;
  fetchWeek();
}

export function shiftDayShown(delta) {
  dayIndex = Math.min(6, Math.max(0, (dayIndex || 0) + Number(delta)));
  if (lastWeek) renderWeek(lastWeek);
}

export async function fetchWeek() {
  const grid = document.getElementById('cal-grid');
  const error = document.getElementById('cal-error');
  if (!grid) return;
  if (error) { error.textContent = ''; error.style.display = 'none'; }
  grid.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    if (mode === 'room') {
      const roomId = (document.getElementById('cal-room') || {}).value;
      if (!roomId) {
        lastWeek = null;
        grid.innerHTML = emptyState('Henüz oda yok.');
        return;
      }
      const query = new URLSearchParams({ start: weekOf });
      const d = await apiFetch(`/api/rooms/${encodeURIComponent(roomId)}/week?${query}`);
      lastWeek = { start: d.start, timezone: d.timezone, roomId, items: d.slots.map(roomItem) };
    } else {
      const query = new URLSearchParams(bookQuery().slice(1));
      query.set('start', weekOf);
      const d = await apiFetch(`/api/appointments/week?${query}`);
      const several = d.practitioners.length > 1;
      const items = d.practitioners.flatMap(p => p.appointments
        .filter(a => a.status !== 'cancelled')
        .map(a => bookItem(a, several ? p.full_name : '')));
      lastWeek = { start: d.start, timezone: d.timezone, roomId: '', items };
    }
    renderWeek(lastWeek);
  } catch (e) {
    lastWeek = null;
    grid.innerHTML = '';
    if (error) { error.textContent = e.message; error.style.display = 'block'; }
  }
}

// What a block on the grid shows, as plain text (escaped when drawn).
function bookItem(a, practitionerName) {
  const lines = [a.client_name];
  const details = [formatLabel(a.session_format)];
  if (a.room_name) details.push(a.room_name);
  if (STATUS_LABEL[a.status]) details.push(STATUS_LABEL[a.status]);
  lines.push(details.join(' · '));
  if (practitionerName) lines.push(practitionerName);
  return { starts_at: a.starts_at, ends_at: a.ends_at, lines, series: !!a.series_id, busy: false, status: a.status };
}

function roomItem(slot) {
  if (!slot.mine) return { starts_at: slot.starts_at, ends_at: slot.ends_at, lines: ['Dolu'], series: false, busy: true };
  return { ...bookItem(slot, ''), busy: false };
}

/* -- Drawing ---------------------------------------------------------- */

function renderWeek(week) {
  const grid = document.getElementById('cal-grid');
  const zone = document.getElementById('cal-timezone');
  if (!grid) return;
  if (zone) zone.textContent = `Saatler ${week.timezone} saat dilimindedir.`;
  const days = Array.from({ length: 7 }, (_, i) => addDays(week.start, i));
  const now = partsIn(new Date().toISOString(), week.timezone);
  if (dayIndex === null) dayIndex = Math.max(0, days.indexOf(now.day));

  const first = hours.from * 60;
  const last = hours.to * 60;
  const height = ((last - first) / SLOT_MIN) * SLOT_PX;

  // Each block in its day, cut to the working hours.
  const byDay = days.map(() => []);
  let outside = 0;
  week.items.forEach(item => {
    const start = partsIn(item.starts_at, week.timezone);
    const end = partsIn(item.ends_at, week.timezone);
    const i = days.indexOf(start.day);
    if (i < 0) return;
    const endMinutes = end.day === start.day ? end.minutes : 24 * 60;
    if (endMinutes <= first || start.minutes >= last) { outside += 1; return; }
    byDay[i].push({ ...item, from: start.minutes, to: endMinutes });
  });

  const head = days.map((day, i) => `
    <div class="cal-dayhead${i === dayIndex ? ' cal-day-active' : ''}${day === now.day ? ' cal-today' : ''}">${escapeHtml(dayHeading(day))}</div>`).join('');

  const times = [];
  for (let m = first; m < last; m += 60) {
    times.push(`<div class="cal-time" style="height:${2 * SLOT_PX}px">${hhmm(m)}</div>`);
  }

  const columns = days.map((day, i) => {
    const cells = [];
    for (let m = first; m < last; m += SLOT_MIN) {
      const isPast = day < now.day || (day === now.day && m + SLOT_MIN <= now.minutes);
      const label = `${dayHeading(day)} ${hhmm(m)}`;
      cells.push(isPast
        ? `<div class="cal-slot cal-slot-past" style="height:${SLOT_PX}px"></div>`
        : `<button type="button" class="cal-slot" style="height:${SLOT_PX}px" data-action="cal-slot" data-arg="${escapeHtml(day)}" data-arg2="${hhmm(m)}" aria-label="${escapeHtml(label)}: randevu ver"></button>`);
    }
    const blocks = byDay[i].map(item => drawBlock(item, first, last)).join('');
    return `<div class="cal-day${i === dayIndex ? ' cal-day-active' : ''}" style="height:${height}px">${cells.join('')}${blocks}</div>`;
  }).join('');

  const switcher = `
    <div class="cal-dayswitch">
      <button type="button" class="btn btn-ghost btn-sm" data-action="cal-day" data-arg="-1" aria-label="Önceki gün"${dayIndex === 0 ? ' disabled' : ''}>&larr;</button>
      <strong>${escapeHtml(dayHeading(days[dayIndex]))}</strong>
      <button type="button" class="btn btn-ghost btn-sm" data-action="cal-day" data-arg="1" aria-label="Sonraki gün"${dayIndex === 6 ? ' disabled' : ''}>&rarr;</button>
    </div>`;

  const note = outside
    ? `<p class="appt-muted cal-note">Bu hafta çalışma saatleri dışında ${outside} randevu var; saat aralığını genişleterek görebilirsiniz.</p>`
    : '';
  grid.innerHTML = `${switcher}
    <div class="cal-grid">
      <div class="cal-corner"></div>${head}
      <div class="cal-times">${times.join('')}</div>${columns}
    </div>${note}`;
}

function drawBlock(item, first, last) {
  const from = Math.max(item.from, first);
  const to = Math.min(item.to, last);
  const top = ((from - first) / SLOT_MIN) * SLOT_PX;
  const height = Math.max(((to - from) / SLOT_MIN) * SLOT_PX - 2, 14);
  // Minutes are numbers computed here; the label is escaped where it is drawn.
  const startLabel = hhmm(item.from);
  const endLabel = hhmm(Math.min(item.to, 24 * 60 - 1));
  const time = `${startLabel}–${endLabel}`;
  let cls = 'cal-block cal-block-closed';
  if (item.busy) cls = 'cal-block cal-block-busy';
  else if (item.status === 'scheduled') cls = 'cal-block cal-block-planned';
  const title = [time, ...item.lines].join(' · ') + (item.series ? ' · Seri' : '');
  const series = item.series ? ' <span class="cal-series" aria-label="Seri">↻</span>' : '';
  const lines = item.lines.map((line, k) => `<div class="${k ? 'cal-line' : 'cal-name'}">${escapeHtml(line)}</div>`).join('');
  return `
    <div class="${cls}" style="top:${top}px; height:${height}px" title="${escapeHtml(title)}">
      <div class="cal-line">${escapeHtml(time)}${series}</div>${lines}
    </div>`;
}

/* -- A free half hour: open the booking form there --------------------- */

export function openSlot(day, time) {
  const roomId = mode === 'room' && lastWeek ? lastWeek.roomId : '';
  navigate('appointments');
  prefillBooking({ date: day, time, roomId });
}
