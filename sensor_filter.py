"""
Plausibilitaetsfilter fuer die Wellensonde - serverseitiges Gegenstueck zu
filterSensorFaults() in index.html (gleiche Schwellen, gleiche Logik).

Hintergrund: Die Sonde kippt zeitweise in einen Fehlermodus und liefert dann
verrauschte Werte um 15-35 cm (auch Nullen), obwohl die Welle voll laeuft -
und springt spaeter wieder auf den richtigen Wert (Beispiel 21./22.11.2026:
86 cm -> 15-35 cm ueber 15 Stunden, zwischendurch kurz 81 cm, ab 9:25 wieder
88 cm, Fabrikkanal die ganze Zeit gesaettigt bei ~22,5 m3/s).

Ein Messpunkt wird verworfen, wenn BEIDES zutrifft:
  - er liegt mehr als 35 cm neben dem aus Tuerkheim erwarteten Wert, und
  - das Signal zappelt: Median der zweiten Differenz |v[i]-2v[i-1]+v[i-2]|
    im Fenster +-30 min >= 3 cm.
Dazu Uebergangswerte beim Umkippen: ueber 30 cm daneben und hoechstens
30 min von einem verworfenen Punkt entfernt. Der Erwartungswert bekommt den Versatz der zuletzt
akzeptierten Punkte (Median der letzten 200).

Warum das Zappeln entscheidet: Auch echte Ereignisse weichen vom Modell ab
(Wehr-/Bachablass-Eingriffe, z. B. 20.08. 43 -> 74 -> 50 cm), verlaufen aber
glatt. Der Fehlermodus der Sonde springt dagegen alle 5 Minuten um 5-10 cm.
Gegen die Historie (Mai-Nov 2026): Fehlerphase 21./22.11. zu 89 % erkannt,
im Normalbetrieb kein einziger Punkt verworfen. Eine reine Abweichungs-
schwelle (> 45 cm) haette dagegen echte Wellen an 10 Tagen geloescht.

WICHTIG: Der Filter wird nur beim AUSWERTEN angewendet. surfwelle_manual.csv
bleibt unveraendert (Rohdaten) - ein falsch verworfener Wert ist so jederzeit
wiederherstellbar, und spaetere Kalibrierungen koennen selbst entscheiden.
"""

from __future__ import annotations

import bisect
import csv
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
BERLIN = ZoneInfo("Europe/Berlin")
COLLECTED_CSV = Path(__file__).parent / "data" / "collected.csv"

# Gleiche Tabellen wie in index.html
TUE2FAB = [
    (2.05, 3.64), (2.67, 3.60), (3.05, 4.06), (4.58, 5.96), (5.52, 7.42),
    (6.43, 9.00), (7.39, 10.66), (8.45, 12.31), (9.47, 13.31), (10.80, 15.08),
    (13.10, 18.11), (17.40, 22.29), (27.60, 23.01),
]
FAB2WAVE = [
    (0.62, 8.0), (2.89, 20.6), (3.86, 19.3), (5.49, 26.6), (6.79, 33.8),
    (8.11, 41.5), (9.08, 48.0), (10.60, 57.5), (11.30, 62.3), (12.31, 63.5),
    (13.51, 67.5), (14.90, 76.0), (16.80, 81.5), (18.50, 83.5), (23.00, 85.0),
]
TUE2WAVE_T10, TUE2WAVE_B = 5.71, 0.401
BIAS_CM = -2.9

DEV_ROUGH, DEV_NEIGHBOUR = 35.0, 30.0
ROUGH_MIN = 3.0                    # cm, Median |2. Differenz| im Fenster
ROUGH_HALF = timedelta(minutes=30)  # Fenster +-30 min


def _interp(table, x):
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return table[-1][1]


def _lag_hours(q):
    return TUE2WAVE_T10 * (min(max(q, 2), 40) / 10) ** -TUE2WAVE_B


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BERLIN)
    return dt.astimezone(ZoneInfo("UTC"))


def load_expected(path: Path = COLLECTED_CSV) -> list[tuple[datetime, float]]:
    """Erwartete Wellenhoehe (ohne Versatz) zum Ankunftszeitpunkt an der Welle,
    aus Tuerkheim in collected.csv. Sortiert, UTC."""
    out = []
    if not path.exists():
        log.warning("%s fehlt - Plausibilitaetsfilter inaktiv", path)
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            q, t = row.get("tuerkheim_q_m3s"), row.get("tuerkheim_time") or row.get("collected_at")
            try:
                q = float(q)
                dt = _to_utc(datetime.fromisoformat(t.replace("Z", "+00:00")))
            except (TypeError, ValueError, AttributeError):
                continue
            out.append((dt + timedelta(hours=_lag_hours(q)),
                        _interp(FAB2WAVE, _interp(TUE2FAB, q))))
    out.sort(key=lambda p: p[0])
    # doppelte Ankunftszeiten zusammenfassen
    dedup = []
    for p in out:
        if dedup and dedup[-1][0] == p[0]:
            continue
        dedup.append(p)
    return dedup


def plausible_keys(series: dict[str, float],
                   expected: list[tuple[datetime, float]] | None = None) -> tuple[set[str], set[str]]:
    """Teilt {zeitstempel: cm} in (plausibel, verworfen). Punkte ohne
    Erwartungswert (keine Tuerkheim-Daten) gelten als plausibel."""
    if expected is None:
        expected = load_expected()
    pts = []
    for k, v in series.items():
        try:
            pts.append((_to_utc(datetime.fromisoformat(k)), float(v), k))
        except (TypeError, ValueError):
            continue
    pts.sort(key=lambda p: p[0])
    if len(expected) < 2:
        return {p[2] for p in pts}, set()

    et = [e[0] for e in expected]

    def exp_at(t):
        i = bisect.bisect_right(et, t)
        if i == 0 or i >= len(et):
            return None
        (t0, v0), (t1, v1) = expected[i - 1], expected[i]
        if (t1 - t0) > timedelta(hours=3):
            return None
        return v0 + (t - t0).total_seconds() / max(1.0, (t1 - t0).total_seconds()) * (v1 - v0)

    # Zappeln: |zweite Differenz| je Punkt, Median im Fenster +-30 min
    d2 = [0.0, 0.0] + [abs(pts[i][1] - 2 * pts[i - 1][1] + pts[i - 2][1])
                       for i in range(2, len(pts))]
    times = [p[0] for p in pts]
    rough = []
    for i, t in enumerate(times):
        lo = bisect.bisect_left(times, t - ROUGH_HALF)
        hi = bisect.bisect_right(times, t + ROUGH_HALF)
        rough.append(median(d2[max(lo, 2):hi]) if hi > max(lo, 2) else 0.0)

    info = []          # [key, bad, dev]
    res = deque(maxlen=200)
    for i, (t, v, k) in enumerate(pts):
        e0 = exp_at(t)
        if e0 is None:
            info.append([k, False, 0.0])
            continue
        bias = median(res) if len(res) >= 12 else BIAS_CM
        dev = v - (e0 + bias)
        bad = abs(dev) > DEV_ROUGH and rough[i] >= ROUGH_MIN
        info.append([k, bad, dev])
        if not bad:
            res.append(v - e0)

    # Uebergangswerte beim Umkippen: > 30 cm daneben und hoechstens 30 min
    # von einem verworfenen Punkt entfernt
    bad_t = [pts[i][0] for i, x in enumerate(info) if x[1]]
    for i, x in enumerate(info):
        if x[1] or abs(x[2]) <= DEV_NEIGHBOUR or not bad_t:
            continue
        j = bisect.bisect_left(bad_t, pts[i][0])
        near = [bad_t[m] for m in (j - 1, j) if 0 <= m < len(bad_t)]
        if any(abs(t - pts[i][0]) <= ROUGH_HALF for t in near):
            x[1] = True

    good = {x[0] for x in info if not x[1]}
    bad = {x[0] for x in info if x[1]}
    return good, bad
