"""
Erkennt Fließwellen (flood wave translation) in data/collected.csv und misst die
Peak-Laufzeiten zwischen benachbarten Wertach-Pegeln.

Hintergrund: Nach einem Regenereignis im Oberlauf (z.B. Oberjoch) rollt eine
Abflusswelle die Wertach hinunter. Jeder Pegel steigt zeitversetzt an. Aus dem
zeitlichen Abstand der Peaks lassen sich die realen Laufzeiten zwischen den
Pegeln ableiten — Kalibrierungs-Gold für die Prognose (LAG / CATCHMENT).

PEGEL-AGNOSTISCH: Das Skript nutzt genau die Pegel aus GAUGES, deren Spalte in
der CSV existiert UND Daten enthält. Fehlende Pegel (Wertach, Sebastianskapelle,
Thalhofen — noch nicht im Collector) werden automatisch übersprungen und rutschen
an die richtige Stelle in der Kette, sobald collect.py sie mitsammelt.

Aufruf:
    python detect_flood_waves.py                 # Report auf stdout
    python detect_flood_waves.py --csv PFAD       # andere CSV
    python detect_flood_waves.py --write          # Events zusätzlich als CSV
    python detect_flood_waves.py --min-rise 1.5   # Empfindlichkeit (m³/s über Baseline)

Nur pandas + numpy — bewusst ohne scipy, damit es im GitHub-Actions-Umfeld
leichtgewichtig bleibt.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


# -----------------------------------------------------------------------------
# Pegel-Kette flussabwärts geordnet: (Anzeigename, Q-Spalte, Zeit-Spalte)
# Reihenfolge = geografische Reihenfolge entlang der Wertach. Die Zeit-Spalte ist
# der native HND-Messzeitstempel (naiv, Europe/Berlin) — genauer für die Peak-
# Zeit als collected_at (Scrape-Zeit). Pegel ohne vorhandene Spalte werden
# stillschweigend übersprungen.
# -----------------------------------------------------------------------------
GAUGES = [
    ("Wertach",           "wertach_q_m3s",          "wertach_q_time"),            # noch nicht im Collector
    ("Sebastianskapelle", "sebastianskapelle_q_m3s", "sebastianskapelle_q_time"),  # noch nicht im Collector
    ("Haslach",           "haslach_q_m3s",          "haslach_q_time"),
    ("Thalhofen",         "thalhofen_q_m3s",        "thalhofen_q_time"),          # noch nicht im Collector
    ("Biessenhofen",      "biessenhofen_q_m3s",     "biessenhofen_q_time"),
    ("Türkheim",          "tuerkheim_q_m3s",        "tuerkheim_time"),
    ("Oberhausen",        "oberhausen_q_m3s",       "oberhausen_q_time"),
]

# -----------------------------------------------------------------------------
# Parameter der Ereigniserkennung
# -----------------------------------------------------------------------------
GRID_MIN      = 15      # Raster in Minuten, auf das jeder Pegel resampelt wird
MAX_FILL_GAP  = 4       # Lücken bis N Raster-Schritte (=1h) linear interpolieren; größere bleiben NaN
BASELINE_H    = 24      # Fenster des gleitenden Medians (Baseline) in Stunden
RISE_FACTOR   = 1.25    # "erhöht", wenn Q > Baseline * RISE_FACTOR ...
MIN_RISE_ABS  = 1.0     # ... UND Q > Baseline + MIN_RISE_ABS (m³/s), gegen Rauschen bei kleinen Pegeln
MERGE_GAP_H   = 3       # erhöhte Phasen, die < N h auseinanderliegen, zu EINEM Event verschmelzen
MIN_EVENT_H   = 1       # Events kürzer als N h verwerfen
MAX_LAG_H     = 30      # max. plausible Laufzeit zwischen zwei benachbarten Pegeln (Grüntensee-Puffer!)


def load_gauge_series(df: pd.DataFrame, qcol: str, tcol: str) -> pd.Series | None:
    """Baut aus einer Q-Spalte eine saubere, regelmäßig gerasterte Zeitreihe."""
    if qcol not in df.columns:
        return None
    sub = df[[qcol]].copy()
    # Zeitstempel wählen: bevorzugt der HND-eigene Messzeitpunkt, sonst collected_at
    if tcol in df.columns and df[tcol].notna().any():
        t = pd.to_datetime(df[tcol], format="mixed", errors="coerce")
    else:
        t = pd.to_datetime(df["collected_at"], errors="coerce", utc=True).dt.tz_convert(
            "Europe/Berlin"
        ).dt.tz_localize(None)
    sub["t"] = t
    sub["q"] = pd.to_numeric(sub[qcol], errors="coerce")
    sub = sub.dropna(subset=["t", "q"])
    if sub.empty:
        return None
    # Doppelte HND-Zeitstempel (mehrfaches Scrapen desselben Werts) entfernen
    sub = sub.sort_values("t").drop_duplicates(subset="t", keep="last")
    s = sub.set_index("t")["q"]
    # Auf regelmäßiges Raster bringen, kleine Lücken interpolieren, große offen lassen
    grid = pd.date_range(s.index.min().floor("h"), s.index.max().ceil("h"), freq=f"{GRID_MIN}min")
    s = s.reindex(s.index.union(grid)).interpolate(method="time", limit=MAX_FILL_GAP).reindex(grid)
    return s


def detect_events(s: pd.Series) -> list[dict]:
    """Findet Event-Fenster (erhöhter Abfluss) und je Fenster den Peak."""
    baseline = s.rolling(f"{BASELINE_H}h", min_periods=3).median()
    thr = np.maximum(baseline * RISE_FACTOR, baseline + MIN_RISE_ABS)
    elevated = (s > thr) & s.notna()
    if not elevated.any():
        return []
    # zusammenhängende erhöhte Phasen bilden, kleine Unterbrechungen überbrücken
    idx = s.index
    events, run_start, last_true = [], None, None
    merge_gap = pd.Timedelta(hours=MERGE_GAP_H)
    for t, is_el in elevated.items():
        if is_el:
            if run_start is None:
                run_start = t
            elif last_true is not None and (t - last_true) > merge_gap:
                events.append((run_start, last_true))
                run_start = t
            last_true = t
    if run_start is not None:
        events.append((run_start, last_true))

    out = []
    for a, b in events:
        if (b - a) < pd.Timedelta(hours=MIN_EVENT_H):
            continue
        window = s.loc[a:b]
        if window.dropna().empty:
            continue
        peak_t = window.idxmax()
        out.append({"start": a, "end": b, "peak_t": peak_t, "peak_q": float(window.max()),
                    "base_q": float(baseline.loc[peak_t]) if pd.notna(baseline.loc[peak_t]) else np.nan})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(Path(__file__).parent / "data" / "collected.csv"))
    ap.add_argument("--write", action="store_true", help="Events zusätzlich nach data/flood_events.csv schreiben")
    ap.add_argument("--min-rise", type=float, default=MIN_RISE_ABS)
    args = ap.parse_args()

    globals()["MIN_RISE_ABS"] = args.min_rise

    df = pd.read_csv(args.csv)
    if "collected_at" not in df.columns:
        print("FEHLER: collected_at fehlt in der CSV", file=sys.stderr)
        return 1

    # aktive Pegel bestimmen (Spalte vorhanden + Daten)
    active = []
    for name, qcol, tcol in GAUGES:
        s = load_gauge_series(df, qcol, tcol)
        if s is not None and s.notna().sum() >= 10:
            active.append((name, s))
    missing = [name for name, qcol, _ in GAUGES if qcol not in df.columns or df[qcol].notna().sum() < 10]

    print("=" * 64)
    print("FLIESSWELLEN-ERKENNUNG — Wertach")
    print("=" * 64)
    print(f"Aktive Pegel (flussabwärts): {', '.join(n for n, _ in active)}")
    if missing:
        print(f"Noch nicht im Datensatz:     {', '.join(missing)}")
    print()

    # Events je Pegel
    per_gauge = {name: detect_events(s) for name, s in active}
    for name, evs in per_gauge.items():
        print(f"  {name:16s}: {len(evs)} Event(s)")
    print()

    # Wellen verketten: für jeden Event-Peak am Pegel i den passenden Peak am
    # nächsten aktiven Pegel flussabwärts suchen (nächster Peak in [t, t+MAX_LAG]).
    names = [n for n, _ in active]
    pair_lags = {}  # (upstream, downstream) -> Liste von Laufzeiten (h)
    waves = []      # verkettete Wellen: Liste von {gauge: (peak_t, peak_q)}

    if len(active) >= 2:
        # Verkettung startet am OBERSTEN Pegel, der überhaupt ein Event hat.
        # Fehlt an einem Pegel der passende Peak (Lücke/kein Event), wird er
        # übersprungen und flussabwärts weitergesucht — die Kette reißt nicht ab,
        # nur das betroffene Pegelpaar liefert für diese Welle keine Laufzeit.
        used_down = {n: set() for n in names}  # schon vergebene Peaks je Pegel (kein Doppel-Match)
        for si, top_name in enumerate(names[:-1]):
            for ev in per_gauge[top_name]:
                key = ev["peak_t"]
                if key in used_down[top_name]:
                    continue
                chain = {top_name: (ev["peak_t"], ev["peak_q"])}
                t_prev, prev_name = ev["peak_t"], top_name
                for k in range(si + 1, len(names)):
                    dn = names[k]
                    cands = [e for e in per_gauge[dn]
                             if e["peak_t"] not in used_down[dn]
                             and t_prev <= e["peak_t"] <= t_prev + pd.Timedelta(hours=MAX_LAG_H)]
                    if not cands:
                        continue  # diesen Pegel überspringen, weiter flussabwärts
                    nxt = min(cands, key=lambda e: e["peak_t"])
                    lag_h = (nxt["peak_t"] - t_prev).total_seconds() / 3600
                    pair_lags.setdefault((prev_name, dn), []).append(lag_h)
                    chain[dn] = (nxt["peak_t"], nxt["peak_q"])
                    used_down[dn].add(nxt["peak_t"])
                    t_prev, prev_name = nxt["peak_t"], dn
                if len(chain) >= 2:
                    used_down[top_name].add(key)
                    waves.append(chain)

    # Report der verketteten Wellen
    print("-" * 64)
    print(f"VERKETTETE WELLEN: {len(waves)}")
    print("-" * 64)
    for i, ch in enumerate(waves, 1):
        span = None
        items = list(ch.items())
        if len(items) >= 2:
            span = (items[-1][1][0] - items[0][1][0]).total_seconds() / 3600
        print(f"\nWelle #{i}" + (f"  (gesamt {span:.1f} h vom obersten bis untersten Pegel)" if span else ""))
        prev_t = None
        for name, (pt, pq) in items:
            flag = ""
            if prev_t is not None:
                lag_h = (pt - prev_t).total_seconds() / 3600
                lag = f"  (+{lag_h:5.2f} h)"
                # Warnhinweis: sehr lange Nachbar-Laufzeit deutet oft auf eine
                # Fehlverkettung zweier getrennter Ereignisse hin (fehlende
                # Zwischenpegel!). Nicht verwerfen, aber sichtbar markieren.
                if lag_h > 15:
                    flag = "  ⚠ ungewöhnlich lang — evtl. zwei Ereignisse verkettet"
            else:
                lag = ""
            print(f"    {name:16s} {pt:%d.%m %H:%M}   {pq:5.2f} m³/s{lag}{flag}")
            prev_t = pt

    # Zusammenfassung: mediane Laufzeit je Pegelpaar
    print("\n" + "=" * 64)
    print("LAUFZEITEN JE PEGELPAAR (Median über alle Wellen)")
    print("=" * 64)
    if pair_lags:
        for (up, dn), lags in pair_lags.items():
            arr = np.array(lags)
            print(f"  {up:16s} → {dn:16s}  Median {np.median(arr):5.2f} h "
                  f"(min {arr.min():.2f}, max {arr.max():.2f}, n={len(arr)})")
    else:
        print("  (noch keine verketteten Wellen — mehr Daten/Pegel nötig)")

    if args.write and waves:
        rows = []
        for i, ch in enumerate(waves, 1):
            for name, (pt, pq) in ch.items():
                rows.append({"wave": i, "gauge": name, "peak_time": pt, "peak_q": pq})
        out = Path(args.csv).parent / "flood_events.csv"
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f"\nGeschrieben: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
