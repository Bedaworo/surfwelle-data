"""
Füllt die WETTER-Spalten in data/collected.csv aus der Open-Meteo Archive-API
(ERA5-Reanalyse) rückwirkend — Regen, Temperatur, Bodenfeuchte.

Gedacht als Schritt 2 NACH backfill.py:
    1. python backfill.py --inplace          # Pegel (HND) füllen/reparieren
    2. python backfill_weather.py --inplace  # Wetter (Open-Meteo) nachziehen

Warum getrennt von backfill.py: Pegeldaten kommen als HTML-Tabelle (15-Min),
Wetter als JSON von einer anderen API (stündlich). Sauberer als zwei Dinge in
einem Skript.

WICHTIGE GRENZEN (ehrlich):
- Die Archive-API ist ERA5-GITTER-Reanalyse. Sie unterschätzt lokale konvektive
  Starkregen systematisch — dasselbe Problem, das ihr schon von den Open-Meteo-
  Gitterwerten vs. HND-Bodenstationen kennt. Für Trend/Jahres-Analyse trotzdem
  wertvoll, aber die rückwirkenden Regenwerte sind KEIN Ersatz für die HND-
  Bodenstationen (die lassen sich nicht rückwirkend holen — bleiben leer).
- Vorhersage-Spalten (forecast_*) und hnd_rain_* werden NICHT gefüllt: ein
  vergangener „Forecast" existiert nicht rückwirkend, und HND-Regenarchiv ist
  eine andere Quelle. Diese Spalten bleiben in Backfill-Zeilen leer.
- Archive-Daten haben ~5 Tage Verzögerung (ERA5). Die letzten Tage füllt der
  Live-Collector ohnehin.

Nur requests + pandas.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd
import requests

TIMEOUT = 60
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CSV_PATH = Path(__file__).parent / "data" / "collected.csv"

# Standorte identisch zu collect.py (LOCATIONS + SOIL_POINTS)
LOCATIONS = {
    "kempten":       (47.7333, 10.3167),
    "marktoberdorf": (47.7800, 10.6167),
    "augsburg":      (48.3667, 10.8833),
    "oberjoch":      (47.5159, 10.4058),
    "kaufbeuren":    (47.8812, 10.6246),
    "bobingen":      (48.2700, 10.8300),
}
# Für welche Orte Temperatur gefüllt wird (collect.py sammelt nur diese zwei)
TEMP_COLS = {"kempten": "temp_kempten_c", "oberjoch": "temp_oberjoch_c"}
# Regen-Spalte je Ort
RAIN_COLS = {n: f"rain_{n}_mm" for n in LOCATIONS}
# Bodenfeuchte-Punkte + Tiefen -> Spaltennamen
SOIL_POINTS = {"oberjoch": (47.5159, 10.4058), "kaufbeuren": (47.8812, 10.6246)}
SOIL_DEPTHS = {
    "0_to_7cm":     "soil_moisture_0_to_7cm",
    "7_to_28cm":    "soil_moisture_7_to_28cm",
    "28_to_100cm":  "soil_moisture_28_to_100cm",
    "100_to_255cm": "soil_moisture_100_to_255cm",
}


def fetch_archive(lat, lon, start, end, hourly_vars) -> pd.DataFrame | None:
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start, "end_date": end,
        "hourly": ",".join(hourly_vars),
        "timezone": "Europe/Berlin",
    }
    try:
        r = requests.get(ARCHIVE_URL, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        h = r.json().get("hourly", {})
    except (requests.RequestException, ValueError) as e:
        print(f"  ! Archive-Fehler ({lat},{lon}): {e}", file=sys.stderr)
        return None
    if not h.get("time"):
        return None
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"])  # naive Berlin (timezone=Europe/Berlin)
    return df.set_index("time")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(CSV_PATH))
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (Default: frühestes Datum in der CSV)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (Default: spätestes Datum in der CSV)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inplace", action="store_true")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    df = pd.read_csv(csv_path)
    print(f"collected.csv: {len(df)} Zeilen")

    # Slot je Zeile: Stunde in Berlin-Zeit (Wetter ist stündlich)
    ca = pd.to_datetime(df["collected_at"], errors="coerce", utc=True).dt.tz_convert("Europe/Berlin").dt.tz_localize(None)
    df["_hour"] = ca.dt.floor("h")
    start = args.start or df["_hour"].min().strftime("%Y-%m-%d")
    end = args.end or df["_hour"].max().strftime("%Y-%m-%d")
    print(f"Zeitraum: {start} – {end}\n")

    # relevante Spalten auf object casten (leere float-Spalten nehmen sonst keine Werte an; hier egal, aber konsistent)
    filled = {}

    # --- Regen + Temperatur je Standort ---
    for name, (lat, lon) in LOCATIONS.items():
        varlist = ["precipitation"]
        if name in TEMP_COLS:
            varlist.append("temperature_2m")
        print(f"  hole {name} (Regen{'/Temp' if name in TEMP_COLS else ''}) …")
        adf = fetch_archive(lat, lon, start, end, varlist)
        if adf is None:
            continue
        # Regen
        rcol = RAIN_COLS[name]
        if rcol in df.columns and "precipitation" in adf:
            mapped = df["_hour"].map(adf["precipitation"])
            mask = df[rcol].isna() & mapped.notna()
            df.loc[mask, rcol] = mapped[mask]
            filled[rcol] = int(mask.sum())
        # Temperatur
        if name in TEMP_COLS and "temperature_2m" in adf:
            tcol = TEMP_COLS[name]
            if tcol in df.columns:
                mapped = df["_hour"].map(adf["temperature_2m"])
                mask = df[tcol].isna() & mapped.notna()
                df.loc[mask, tcol] = mapped[mask]
                filled[tcol] = int(mask.sum())

    # --- Bodenfeuchte je Punkt ---
    for name, (lat, lon) in SOIL_POINTS.items():
        api_vars = [f"soil_moisture_{d}" for d in SOIL_DEPTHS]
        print(f"  hole {name} (Bodenfeuchte) …")
        adf = fetch_archive(lat, lon, start, end, api_vars)
        if adf is None:
            continue
        for depth in SOIL_DEPTHS:
            api_var = f"soil_moisture_{depth}"
            col = f"soil_moist_{name}_{depth}"
            if col in df.columns and api_var in adf:
                mapped = df["_hour"].map(adf[api_var])
                mask = df[col].isna() & mapped.notna()
                df.loc[mask, col] = mapped[mask]
                filled[col] = int(mask.sum())

    print("\nGefüllte Zellen je Spalte:")
    for col, n in filled.items():
        if n:
            print(f"  {col:36s} {n}")
    total = sum(filled.values())
    print(f"  {'GESAMT':36s} {total}")

    if args.dry_run:
        print("\n(dry-run — nichts geschrieben)")
        return 0
    if total == 0:
        print("\nNichts zu füllen — nichts geschrieben.")
        return 0

    out = df.drop(columns=["_hour"])
    if args.inplace:
        bak = csv_path.with_suffix(".csv.bak2")
        shutil.copy2(csv_path, bak)
        out.to_csv(csv_path, index=False)
        print(f"\ncollected.csv aktualisiert (Backup: {bak.name})")
    else:
        outp = csv_path.parent / "collected_weatherfilled.csv"
        out.to_csv(outp, index=False)
        print(f"\nGeschrieben: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
