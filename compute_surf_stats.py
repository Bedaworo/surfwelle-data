"""
Berechnet serverseitig, an wie vielen Tagen die Welle in den letzten 365 Tagen
surfbar war, und schreibt das Ergebnis nach data/surf_days_365.json.

Warum serverseitig (wie schon bei last_surf_slot.json / find_last_surf_slot):
Die Berechnung braucht drei Quellen ueber ein volles Jahr -
surfwelle_manual.csv (echte Pegelmessung, seit 10.05.2026), sowie
fabrikkanal_2025.csv und fabrikkanal_2026.csv (echte Kanal-Abflussmessung
des Kraftwerksbetreibers, 15-Min-Werte seit 01.01.2025) als Fallback fuer
Zeitraeume ohne Wellenpegel-Messung. Das waeren im Browser mehrere Megabyte
und zehntausende Zeilen JEDES Mal beim Laden der Seite - unnoetig, wenn das
Ergebnis am Ende zwei Zahlen sind.

Prioritaet pro Tag: echte Wellenpegel-Messung schlaegt Kanal-Schaetzung.
Grund: gegen die echte Messung validiert erreicht die reine Kanal->Welle-
Kurve (FAB2WAVE, ohne die fuer den Tuerkheim-Pfad kalibrierte BIAS_CM-
Korrektur) r=0.92 / MAE~4.4cm im Ueberlappungszeitraum Mai-Jul 2026 - klar
besser als der zweistufige Tuerkheim-Weg (r=0.72 / MAE~7.7cm), aber trotzdem
nur eine Schaetzung. Deshalb: wo echte Wellenmessung vorliegt, wird die
genutzt; die Kanalkurve fuellt nur die Luecke davor (aktuell: alles vor dem
10.05.2026).
"""

from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

DATA_DIR = Path(__file__).parent / "data"
MANUAL_CSV = DATA_DIR / "surfwelle_manual.csv"
FAB_CSVS = [DATA_DIR / "fabrikkanal_2025.csv", DATA_DIR / "fabrikkanal_2026.csv"]
OUT_JSON = DATA_DIR / "surf_days_365.json"

# Gleiche Kurve wie FAB2WAVE in index.html (Kanalabfluss -> Wellenhoehe cm),
# neu kalibriert auf die 57-cm-Schwelle. Bewusst OHNE die BIAS_CM=-2.9-
# Korrektur aus dem Tuerkheim-Pfad - siehe Docstring oben.
FAB2WAVE = [
    (0.62, 8.0), (2.89, 20.6), (3.86, 19.3), (5.49, 26.6), (6.79, 33.8),
    (8.11, 41.5), (9.08, 48.0), (10.60, 57.5), (11.30, 62.3), (12.31, 63.5),
    (13.51, 67.5), (14.90, 76.0), (16.80, 81.5),
]
SURF_CM = 57
SURF_VON, SURF_BIS = 8, 20  # Nutzungszeit der Welle
WINDOW_DAYS = 365


def interp_wave(fab_q: float) -> float:
    pts = FAB2WAVE
    if fab_q <= pts[0][0]:
        return max(0.0, pts[0][1])
    if fab_q >= pts[-1][0]:
        return max(0.0, pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= fab_q <= x1:
            return max(0.0, y0 + (fab_q - x0) / (x1 - x0) * (y1 - y0))
    return max(0.0, pts[-1][1])


def load_manual_days(path: Path) -> dict[str, bool]:
    """Tag (YYYY-MM-DD, Berlin) -> war zu >=1 Zeitpunkt 08-20h surfbar (>=57cm)."""
    days: dict[str, bool] = {}
    seen_days: set[str] = set()
    if not path.exists():
        return days
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(row["time"])
                cm = float(row["percent"])
            except (ValueError, KeyError, TypeError):
                continue
            dt = dt.astimezone(BERLIN) if dt.tzinfo else dt.replace(tzinfo=BERLIN)
            key = dt.date().isoformat()
            seen_days.add(key)
            if SURF_VON <= dt.hour < SURF_BIS and cm >= SURF_CM:
                days[key] = True
    # Tage ohne einen einzigen surfbaren Messpunkt explizit auf False setzen,
    # damit sie unten als "durch Messung abgedeckt" erkannt werden (nicht nur
    # als fehlend, was sie an den Kanal-Fallback weiterreichen wuerde).
    for key in seen_days:
        days.setdefault(key, False)
    return days


def load_fab_days(paths: list[Path]) -> dict[str, bool]:
    """Tag (YYYY-MM-DD, lokale Zeit wie in der CSV) -> war 08-20h surfbar laut Kanalkurve."""
    days: dict[str, bool] = {}
    for path in paths:
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(row["time"])
                    q = float(row["fabrikkanal_q_m3s"])
                except (ValueError, KeyError, TypeError):
                    continue
                key = dt.date().isoformat()
                if SURF_VON <= dt.hour < SURF_BIS and interp_wave(q) >= SURF_CM:
                    days[key] = True
                else:
                    days.setdefault(key, False)
    return days


def compute() -> dict:
    now = datetime.now(BERLIN)
    window_start = (now - timedelta(days=WINDOW_DAYS)).date()
    window_end = now.date()

    manual_days = load_manual_days(MANUAL_CSV)
    fab_days = load_fab_days(FAB_CSVS)

    surfable = 0
    covered = 0
    measured_days = 0
    estimated_days = 0
    d = window_start
    while d <= window_end:
        key = d.isoformat()
        if key in manual_days:
            covered += 1
            measured_days += 1
            if manual_days[key]:
                surfable += 1
        elif key in fab_days:
            covered += 1
            estimated_days += 1
            if fab_days[key]:
                surfable += 1
        d += timedelta(days=1)

    return {
        "days_surfable": surfable,
        "days_covered": covered,
        "days_measured": measured_days,
        "days_estimated": estimated_days,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "computed_at": now.isoformat(),
    }


def main() -> int:
    try:
        stats = compute()
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(stats, f)
        log.info(
            "Surfbare Tage (365 Tage): %d von %d abgedeckten Tagen (%d gemessen, %d geschaetzt)",
            stats["days_surfable"], stats["days_covered"],
            stats["days_measured"], stats["days_estimated"],
        )
        return 0
    except Exception as e:
        log.exception("Konnte 365-Tage-Statistik nicht berechnen: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
