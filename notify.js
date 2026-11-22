/* notify.js - Push-Benachrichtigungen der Surfwelle (Schritt 3: Probebetrieb)
 *
 * Läuft im 15-Minuten-Workflow nach collect.py / fetch_swell.py und rechnet
 * mit forecast.js exakt dieselbe Prognose wie die Seite. Im Probebetrieb
 * (--dry-run, Standard) wird NICHTS verschickt: jede Nachricht, die gesendet
 * würde, landet in data/notify_log.jsonl. Der Versand an die angemeldeten
 * Browser kommt in einem späteren Schritt dazu.
 *
 * Nachrichten (festgelegt mit Benny, 22.11.2026):
 *  1 Tagesprognose  - zur gewählten vollen Stunde (4-8 Uhr), wenn die Welle
 *                     heute voraussichtlich >= 1 h am Stück >= 57 cm läuft
 *                     (8-20 Uhr) und die Wahrscheinlichkeit > 50 % ist.
 *                     "🌊🏄🏼⭐⭐ heute zu 87 %" (Sterne = höchste Tagesstufe)
 *  2 Läuft          - 15 min durchgehend >= 57 cm, frühestens 1 h nach dem
 *                     letzten "Läuft", nur 8-20 Uhr. "🌊🏄🏼⭐ läuft"
 *  3 Läuft nicht    - 30 min durchgehend < 57 cm, nur 8-20 Uhr und nur nach
 *                     einem "Läuft". "🌊😢 läuft nicht"  (2+3 = eine Einstellung)
 *  4 Vorwarnung     - nach >= 3 Tagen ohne surfbaren Slot, wenn ein Tag im
 *                     7-Tage-Forecast die Schwellen (Sterne, Wahrscheinlichkeit)
 *                     erreicht; einmal je vorhergesagtem Tag, erneut nur bei
 *                     +20 Prozentpunkten, nur 8-20 Uhr.
 *                     "🌊🏄🏼⭐ übermorgen zu 70 %"
 * Alle Wellenwerte laufen vorher durch den Sondenfehler-Filter.
 *
 * Aufruf: node notify.js [--dry-run] [--now 2026-11-22T07:05:00+01:00]
 */
'use strict';
const fs = require('fs');
const path = require('path');
const F = require('./forecast.js');

const DATA = (() => { const i = process.argv.indexOf('--data'); return i > 0 ? process.argv[i + 1] : path.join(__dirname, 'data'); })();
const arg0 = n => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : null; };
const STATE_FILE = arg0('--state') || path.join(DATA, 'notify_state.json');
const LOG_FILE = arg0('--log') || path.join(DATA, 'notify_log.jsonl');
const LOG_KEEP = 2000;                 // Zeilen im Protokoll behalten

const DAILY_HOURS = [4, 5, 6, 7, 8];   // wählbare Uhrzeiten Tagesprognose
const START_MIN = 15, STOP_MIN = 30, RESTART_GAP_MIN = 60;
const DRY_DAYS = 3;                    // Trockenphase ab so vielen Tagen
const WARN_REPEAT_PP = 20;             // erneute Vorwarnung ab +20 Prozentpunkten
const MIN = 60e3;

const arg = n => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : null; };
const DRY = !process.argv.includes('--live');
const NOW = arg('--now') ? Date.parse(arg('--now')) : Date.now();

const stars = cm => cm >= 75 ? 3 : cm >= 65 ? 2 : cm >= F.SURF_CM ? 1 : 0;
const starStr = n => '⭐'.repeat(n);
const inSurfWindow = t => { const h = F.berlinHour(t); return h >= F.SURF_VON && h < F.SURF_BIS; };
const berlinDate = t => F.dayKey(t);

function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch (e) { return {}; }
}
function saveState(s) { fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 1) + '\n'); }
function appendLog(entries) {
  if (!entries.length) return;
  let lines = [];
  try { lines = fs.readFileSync(LOG_FILE, 'utf8').split('\n').filter(Boolean); } catch (e) {}
  lines.push(...entries.map(e => JSON.stringify(e)));
  fs.writeFileSync(LOG_FILE, lines.slice(-LOG_KEEP).join('\n') + '\n');
}

/* ---------- Daten ---------- */
const d = F.prepareData(fs.readFileSync(path.join(DATA, 'recent.csv'), 'utf8'),
                        fs.readFileSync(path.join(DATA, 'recent_swell.csv'), 'utf8'));
// Für Tests mit --now: nur Daten bis zu diesem Zeitpunkt verwenden
const coll = d.coll.filter(x => x.t <= NOW);
const wave = d.wave.filter(x => x.t <= NOW);
const ref = Math.max(wave.length ? wave[wave.length - 1].t : 0, coll.length ? coll[coll.length - 1].t : 0);
const fc = F.buildForecast(coll, wave, ref);
const outlook = F.latestOutlook(coll);
const ol = F.computeOutlook(fc, coll, ref, outlook);
const waveOfQ = q => Math.max(0, F.waveFromFab(F.fabFromTue(q)) + fc.bias);

const state = loadState();
const events = [];
const emit = (type, text, extra = {}) =>
  events.push({ at: new Date(NOW).toISOString(), type, text, dry: DRY, ...extra });

/* ---------- 2 + 3: Läuft / läuft nicht ---------- */
// Dauer des aktuellen Abschnitts (>= 57 bzw. < 57) bis zum letzten Messwert.
function currentRun() {
  if (!wave.length) return null;
  const last = wave[wave.length - 1];
  const up = last.v >= F.SURF_CM;
  let first = last;
  for (let i = wave.length - 2; i >= 0; i--) {
    const w = wave[i];
    if ((w.v >= F.SURF_CM) !== up) break;
    if (first.t - w.t > 20 * MIN) break;          // Messlücke trennt
    first = w;
  }
  return { up, minutes: (last.t - first.t) / MIN, last };
}
const run = currentRun();
const ws = state.wave || { running: false, lastStart: 0 };
const fresh = run && NOW - run.last.t <= 30 * MIN;   // veraltete Sondenwerte nicht auswerten
if (!inSurfWindow(NOW)) {
  ws.running = false;                               // nach 20 Uhr still zurücksetzen
} else if (fresh) {
  if (!ws.running && run.up && run.minutes >= START_MIN && NOW - ws.lastStart >= RESTART_GAP_MIN * MIN) {
    ws.running = true; ws.lastStart = NOW;
    const s = stars(run.last.v);
    emit('start', `🌊🏄🏼${starStr(s)} läuft`, { cm: Math.round(run.last.v), stars: s });
  } else if (ws.running && !run.up && run.minutes >= STOP_MIN) {
    ws.running = false;
    emit('stop', '🌊😢 läuft nicht', { cm: Math.round(run.last.v) });
  }
}
state.wave = ws;

/* ---------- 1: Tagesprognose ---------- */
// Stundenprognose (Nah- + Fernpfad) für heute 8-20 Uhr; wo sie nicht bis
// 20 Uhr reicht, entscheidet die Tageswahrscheinlichkeit allein.
function todayPlan() {
  if (!ol || ol.err) return null;
  const today = ol.info.find(x => x.key === berlinDate(NOW));
  if (!today || today.past) return null;
  const futNear = fc.near.filter(x => x.t >= ref);
  const nearEnd = futNear.length ? futNear[futNear.length - 1].t : ref;
  const pts = fc.near.concat(fc.far.filter(x => x.t > nearEnd))
    .filter(x => x.t >= ref && x.mid != null && berlinDate(x.t) === today.key && inSurfWindow(x.t))
    .sort((a, b) => a.t - b.t);
  let best = 0, startT = null, prevT = null;
  for (const p of pts) {
    if (p.mid >= F.SURF_CM && (prevT === null || p.t - prevT <= 20 * MIN)) {
      if (startT === null) startT = p.t;
      best = Math.max(best, (p.t - startT) / MIN);
    } else startT = p.mid >= F.SURF_CM ? p.t : null;
    prevT = p.t;
  }
  // 20 Uhr Ortszeit: 19 Uhr UTC im Winter, 18 Uhr UTC im Sommer
  let end20 = Date.parse(today.key + 'T19:00:00Z');
  if (F.berlinHour(end20) !== 20) end20 = Date.parse(today.key + 'T18:00:00Z');
  const covers = pts.length && pts[pts.length - 1].t >= end20 - 15 * MIN;
  const hourlyOk = best >= 60;
  const maxCm = Math.max(pts.length ? Math.max(...pts.map(p => p.mid)) : 0, waveOfQ(today.q));
  return { p: today.p, hourlyOk, covers, runMin: Math.round(best), stars: stars(maxCm), maxCm: Math.round(maxCm) };
}
const dayKeyNow = berlinDate(NOW);
state.daily = state.daily || {};
const hNow = F.berlinHour(NOW);
if (DAILY_HOURS.includes(hNow) && state.daily[hNow] !== dayKeyNow) {
  state.daily[hNow] = dayKeyNow;
  const plan = todayPlan();
  if (plan && plan.p > 0.5 && (plan.hourlyOk || !plan.covers) && plan.stars >= 1) {
    emit('daily', `🌊🏄🏼${starStr(plan.stars)} heute zu ${Math.round(plan.p * 100)} %`,
         { hour: hNow, ...plan });
  } else {
    emit('daily-skip', 'keine Tagesprognose', { hour: hNow, ...(plan || {}) });
  }
}

/* ---------- 4: Vorwarnung ---------- */
// Trockenphase: letzter surfbarer Slot (serverseitig, gefiltert) liegt
// mindestens 3 Tage zurück. Im Probebetrieb mit den offensten Schwellen
// (ab ⭐, ab 50 %) - die Nutzer-Filter greifen später beim Versand.
let lastSlotEnd = 0;
try { lastSlotEnd = Date.parse(JSON.parse(fs.readFileSync(path.join(DATA, 'last_surf_slot.json'), 'utf8')).end) || 0; } catch (e) {}
const dry = NOW - lastSlotEnd >= DRY_DAYS * 86400e3;
state.warn = state.warn || {};
for (const k of Object.keys(state.warn)) if (k < dayKeyNow) delete state.warn[k];
if (dry && inSurfWindow(NOW) && ol && !ol.err) {
  // Nur der FRÜHESTE Tag, der die Schwellen erreicht - sonst kämen bei einer
  // nassen Woche sechs Nachrichten auf einmal. Wurde er schon gemeldet, kommt
  // erst bei +20 Prozentpunkten eine neue Nachricht.
  const day = ol.info.find(x => x.key > dayKeyNow && !x.past && x.p >= 0.5 && stars(waveOfQ(x.q)) >= 1);
  if (day) {
    const s = stars(waveOfQ(day.q));
    const pct = Math.round(day.p * 100);
    const prev = state.warn[day.key];
    if (prev === undefined || pct >= prev + WARN_REPEAT_PP) {
      state.warn[day.key] = pct;
      const n = Math.round((Date.parse(day.key + 'T12:00:00Z') - Date.parse(dayKeyNow + 'T12:00:00Z')) / 86400e3);
      const when = n === 1 ? 'morgen' : n === 2 ? 'übermorgen' : `in ${n} Tagen`;
      emit('warn', `🌊🏄🏼${starStr(s)} ${when} zu ${pct} %`, { day: day.key, p: pct, stars: s });
    }
  }
}

/* ---------- Abschluss ---------- */
state.lastRun = new Date(NOW).toISOString();
saveState(state);
appendLog(events);
for (const e of events) console.log(`[${e.type}] ${e.text}`);
if (!events.length) console.log('keine Nachricht fällig');
