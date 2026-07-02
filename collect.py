"""
Sammelt alle 15 Minuten Daten zu:
- HND Pegel Türkheim (Wertach) — Abfluss
- HND Pegel Biessenhofen (Wertach) — Abfluss + Wasserstand
- HND Pegel Augsburg-Oberhausen (Wertach) — Wasserstand und Abfluss
- HND Grüntensee Seepegel — Wasserstand
- HND Singold-Pegel Langerringen — Abfluss + Wasserstand [v1.3]
- HND Regenstationen Hindelang-Unterjoch, Buchloe, Schwabmünchen [v1.4]
- Open-Meteo (DWD-basiert): Niederschlag in Oberjoch, Kaufbeuren, Kempten,
  Marktoberdorf, Augsburg, Bobingen
- Open-Meteo: Temperatur in Kempten + Oberjoch (Schneeschmelze)
- Open-Meteo: Niederschlags-Vorhersage Kempten + Oberjoch

Jeder Lauf hängt eine Zeile an data/collected.csv an.

Defensiv: Wenn eine Quelle ausfällt, wird für deren Spalten None geschrieben,
aber die Zeile wird trotzdem gespeichert. Fehler werden klar geloggt.

Alle bisherigen Spalten bleiben unverändert für Daten-Kontinuität.
Neue Spalten kommen ans Ende und werden per Auto-Migration angefügt.
"""

from __future__ import annotations

import csv
import io
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Konfiguration
# -----------------------------------------------------------------------------

CSV_PATH = Path(__file__).parent / "data" / "collected.csv"
TIMEOUT = 30
USER_AGENT = "surfwelle-augsburg-data-collector/1.4 (research project)"

# HND-Pegel und Stauseen
HND_TUERKHEIM_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/tuerkheim-12406008/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_OBERHAUSEN_Q_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/augsburg-oberhausen-12407000/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_OBERHAUSEN_W_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/augsburg-oberhausen-12407000/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
# NEU: Biessenhofen — sitzt ~50 km nach Grüntensee, ~38 km vor Türkheim
HND_BIESSENHOFEN_Q_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/biessenhofen-12405005/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_BIESSENHOFEN_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/biessenhofen-12405005/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
# NEU: Grüntensee Seepegel — der große Stausee am Anfang der Wertach
HND_GRUENTENSEE_URL = (
    "https://www.hnd.bayern.de/speicher/iller_lech/gruentensee-seepegel-12403000/tabelle"
    "?methode=seewasserstand&setdiskr=15"
)
# NEU v1.3: Singold (Pegel Langerringen) — laut Wikipedia mündet die Singold
# über den ausgeleiteten Kanal "Senkelbach" in Göggingen in die Wertach.
# Das heißt: Singold-Wasser landet möglicherweise direkt am Surfwellen-Bach,
# ohne Umweg über das Ackermannwehr. EZG 101 km², MQ 2.5 m³/s.
HND_SINGOLD_Q_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/langerringen-12483009/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_SINGOLD_W_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/langerringen-12483009/tabelle"
    "?methode=wasserstand&setdiskr=15"
)

# NEU v1.4: HND-Niederschlagsstationen (DWD-Regenmesser, 5-Min-Auflösung).
# Open-Meteo unterschätzt lokale Schauer im Wertach-EZG dramatisch — HND-Bodendaten
# sind erheblich genauer. Wir wählen drei Stationen die alle südlich Augsburgs
# liegen und das gesamte Wertach-Einzugsgebiet abdecken:
# - Hindelang-Unterjoch: Wertach-Quellgebiet (~1015m)
# - Buchloe: Gennach-EZG, direkt südlich Türkheim
# - Schwabmünchen: Wertach-Tal zwischen Türkheim und Augsburg
# Die Werte sind Niederschlagssummen in mm zum jeweiligen Zeitstempel.
HND_RAIN_HINDELANG_URL = (
    "https://www.hnd.bayern.de/niederschlag/iller_lech/"
    "hindelang-unterjoch-untergschwend-2222/tabelle"
)
HND_RAIN_BUCHLOE_URL = (
    "https://www.hnd.bayern.de/niederschlag/iller_lech/buchloe-2387/tabelle"
)
HND_RAIN_SCHWABMUENCHEN_URL = (
    "https://www.hnd.bayern.de/niederschlag/iller_lech/schwabmuenchen-4579/tabelle"
)

# Wetterstationen (Lat/Lon)
# Bestehende:
#   Kempten: war im Iller-EZG (falsche Region), bleibt für Kontinuität
#   Marktoberdorf: Mittellauf-Region, bleibt
#   Augsburg: Lokaleinfluss Senkelbach, bleibt
# Neue:
#   Oberjoch: 1180m, direkt am Wertach-Quellgebiet
#   Kaufbeuren: Mittellauf, kurz vor Türkheim
LOCATIONS = {
    "kempten":       (47.7333, 10.3167),
    "marktoberdorf": (47.7800, 10.6167),
    "augsburg":      (48.3667, 10.8833),
    "oberjoch":      (47.5159, 10.4058),  # NEU v1.2
    "kaufbeuren":    (47.8812, 10.6246),  # NEU v1.2
    "bobingen":      (48.2700, 10.8300),  # NEU v1.3: Singold/Wertach-Mündungsbereich
}


# -----------------------------------------------------------------------------
# Datenmodell
# -----------------------------------------------------------------------------


@dataclass
class Sample:
    """
    Reihenfolge der Felder = Reihenfolge der CSV-Spalten.
    NEUE Felder bitte am Ende anhängen, um Kompatibilität mit
    bestehenden CSVs zu wahren.
    """
    collected_at: str = ""

    # Bestehende HND-Felder
    tuerkheim_q_m3s: Optional[float] = None
    tuerkheim_time: Optional[str] = None
    oberhausen_q_m3s: Optional[float] = None
    oberhausen_q_time: Optional[str] = None
    oberhausen_w_cm: Optional[float] = None
    oberhausen_w_time: Optional[str] = None

    # Bestehende Wetter-Felder
    rain_kempten_mm: Optional[float] = None
    rain_marktoberdorf_mm: Optional[float] = None
    rain_augsburg_mm: Optional[float] = None
    temp_kempten_c: Optional[float] = None

    forecast_rain_kempten_24h_mm: Optional[float] = None
    forecast_rain_kempten_6h_mm: Optional[float] = None

    # NEUE Felder ab v1.2
    biessenhofen_q_m3s: Optional[float] = None
    biessenhofen_q_time: Optional[str] = None
    biessenhofen_w_cm: Optional[float] = None
    biessenhofen_w_time: Optional[str] = None

    gruentensee_w_mnn: Optional[float] = None  # Meter über NN, NICHT cm
    gruentensee_w_time: Optional[str] = None

    rain_oberjoch_mm: Optional[float] = None
    rain_kaufbeuren_mm: Optional[float] = None
    temp_oberjoch_c: Optional[float] = None

    forecast_rain_oberjoch_24h_mm: Optional[float] = None
    forecast_rain_oberjoch_6h_mm: Optional[float] = None

    # NEUE Felder ab v1.3 — Singold ist Hauptzufluss des Senkelbachs (laut Wikipedia)
    singold_q_m3s: Optional[float] = None
    singold_q_time: Optional[str] = None
    singold_w_cm: Optional[float] = None
    singold_w_time: Optional[str] = None

    rain_bobingen_mm: Optional[float] = None

    # NEUE Felder ab v1.4 — HND-Regenmessungen (direkte DWD-Bodenmessungen)
    # Diese Werte sind Niederschlagssummen zum Zeitstempel; das genaue
    # Aggregations-Intervall hängt von der Station ab (typisch 5 Min).
    hnd_rain_hindelang_mm: Optional[float] = None
    hnd_rain_hindelang_time: Optional[str] = None
    hnd_rain_buchloe_mm: Optional[float] = None
    hnd_rain_buchloe_time: Optional[str] = None
    hnd_rain_schwabmuenchen_mm: Optional[float] = None
    hnd_rain_schwabmuenchen_time: Optional[str] = None


# -----------------------------------------------------------------------------
# HND-Scraping mit pandas
# -----------------------------------------------------------------------------


def fetch_hnd(url: str, label: str) -> Optional[tuple[str, float]]:
    """
    Holt die HND-Seite und parsed die Tabelle mit pandas.read_html.
    Gibt (ISO-Timestamp, Wert) der neuesten Zeile zurück.
    """
    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("HND %s — HTTP-Fehler: %s", label, e)
        return None

    try:
        tables = pd.read_html(io.StringIO(resp.text), decimal=",", thousands=".")
    except ValueError:
        log.warning("HND %s — keine Tabellen im HTML gefunden", label)
        _log_snippet(resp.text, label)
        return None
    except Exception as e:
        log.warning("HND %s — Parse-Fehler: %s", label, e)
        return None

    # Datentabelle finden: zwei Spalten, erste enthält Datums-Strings
    df = None
    for candidate in tables:
        if candidate.shape[1] != 2:
            continue
        first_col = candidate.iloc[:, 0].astype(str)
        if first_col.str.match(r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}").any():
            df = candidate
            break

    if df is None or df.empty:
        log.warning("HND %s — keine passende Tabelle gefunden", label)
        return None

    timestamp_str = str(df.iloc[0, 0]).strip()
    raw_value = df.iloc[0, 1]

    try:
        if isinstance(raw_value, str):
            value = float(raw_value.replace(",", "."))
        else:
            value = float(raw_value)
    except (ValueError, TypeError):
        log.warning("HND %s — Wert nicht parsbar: %r", label, raw_value)
        return None

    try:
        dt = datetime.strptime(timestamp_str, "%d.%m.%Y %H:%M")
    except ValueError:
        log.warning("HND %s — Zeitstempel nicht parsbar: %r", label, timestamp_str)
        return None

    log.info("HND %s ✓ %s = %s", label, dt.isoformat(), value)
    return dt.isoformat(), value


def _log_snippet(html: str, label: str) -> None:
    """Schreibt ein 1000-Zeichen-Fenster um '2026' herum ins Log, zur Diagnose."""
    idx = html.find("2026")
    if idx < 0:
        log.info("HND %s — Snippet (Anfang): %s", label, html[:500].replace("\n", " ")[:500])
    else:
        start = max(0, idx - 200)
        log.info("HND %s — Snippet um Datum: %s", label, html[start:start + 800].replace("\n", " ")[:800])


# -----------------------------------------------------------------------------
# Open-Meteo
# -----------------------------------------------------------------------------


def fetch_openmeteo_current(lat: float, lon: float, label: str) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "precipitation,temperature_2m",
        "timezone": "Europe/Berlin",
        "past_hours": 1,
        "forecast_hours": 0,
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})
        log.info(
            "Open-Meteo %s ✓ Regen=%.2fmm, Temp=%.1f°C",
            label,
            current.get("precipitation") or 0,
            current.get("temperature_2m") or 0,
        )
        return current
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo %s fehlgeschlagen: %s", label, e)
        return {}


def fetch_openmeteo_forecast(lat: float, lon: float, label: str) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation",
        "timezone": "Europe/Berlin",
        "forecast_days": 2,
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly", {})
        precip = hourly.get("precipitation", []) or []
        times = hourly.get("time", []) or []
        if not precip or not times:
            return {}

        now = datetime.now()
        current_hour_str = now.strftime("%Y-%m-%dT%H:00")
        try:
            idx = times.index(current_hour_str)
        except ValueError:
            idx = 0

        next_6h = sum(v for v in precip[idx:idx + 6] if v is not None)
        next_24h = sum(v for v in precip[idx:idx + 24] if v is not None)

        log.info(
            "Open-Meteo Forecast %s ✓ 6h=%.2fmm, 24h=%.2fmm",
            label, next_6h, next_24h,
        )
        return {"next_6h": next_6h, "next_24h": next_24h}
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo Forecast %s fehlgeschlagen: %s", label, e)
        return {}


# -----------------------------------------------------------------------------
# CSV-Append
# -----------------------------------------------------------------------------


def append_sample(sample: Sample) -> None:
    """
    Hängt einen Datenpunkt an die CSV an.
    Header wird nur geschrieben wenn Datei neu ist; sonst bleibt der
    bestehende Header und neue Spalten werden einfach unten leer
    angefügt (verträgt sich mit pandas-Einlesen, bestehender Header
    bleibt bis zur manuellen CSV-Migration unverändert).
    """
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()

    row = asdict(sample)
    fieldnames = list(row.keys())

    if not new_file:
        # Bestehende CSV: prüfen ob neue Spalten dazugekommen sind
        with open(CSV_PATH, encoding="utf-8") as f:
            existing_header = f.readline().strip().split(",")
        if existing_header != fieldnames:
            log.info("Neue Spalten erkannt — migriere CSV-Header")
            _migrate_csv_header(fieldnames, existing_header)

    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    log.info("Geschrieben in %s", CSV_PATH)


def _migrate_csv_header(new_fields: list[str], old_fields: list[str]) -> None:
    """
    Wenn neue Felder dazukommen, wird die CSV einmalig umgeschrieben:
    Header bekommt die neuen Spalten angehängt, alte Zeilen bekommen
    leere Werte für die neuen Spalten. Nur additiv — bestehende Spalten
    werden nie entfernt oder umsortiert.
    """
    # Prüfe: sind die alten Felder alle Präfix der neuen?
    if old_fields != new_fields[:len(old_fields)]:
        log.warning(
            "CSV-Header weicht ab — neue Felder werden NICHT automatisch migriert. "
            "Alte Header: %s, neue: %s",
            old_fields, new_fields,
        )
        return

    added_fields = new_fields[len(old_fields):]
    log.info("Füge %d neue Spalten an: %s", len(added_fields), added_fields)

    # Datei einlesen und mit erweitertem Header neu schreiben
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=new_fields)
        writer.writeheader()
        for row in rows:
            # Alte Zeilen: neue Felder bleiben leer
            for field in added_fields:
                row[field] = ""
            writer.writerow(row)


# -----------------------------------------------------------------------------
# Hauptablauf
# -----------------------------------------------------------------------------


def collect() -> Sample:
    sample = Sample(collected_at=datetime.now(timezone.utc).isoformat())

    # Bestehende HND-Pegel
    if r := fetch_hnd(HND_TUERKHEIM_URL, "Türkheim Q"):
        sample.tuerkheim_time, sample.tuerkheim_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_Q_URL, "Oberhausen Q"):
        sample.oberhausen_q_time, sample.oberhausen_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_W_URL, "Oberhausen W"):
        sample.oberhausen_w_time, sample.oberhausen_w_cm = r

    # NEU: Biessenhofen
    if r := fetch_hnd(HND_BIESSENHOFEN_Q_URL, "Biessenhofen Q"):
        sample.biessenhofen_q_time, sample.biessenhofen_q_m3s = r
    if r := fetch_hnd(HND_BIESSENHOFEN_W_URL, "Biessenhofen W"):
        sample.biessenhofen_w_time, sample.biessenhofen_w_cm = r

    # NEU: Grüntensee Seepegel
    if r := fetch_hnd(HND_GRUENTENSEE_URL, "Grüntensee W"):
        sample.gruentensee_w_time, sample.gruentensee_w_mnn = r

    # NEU v1.3: Singold (Pegel Langerringen) — wichtiger Direkt-Zubringer
    # zum Senkelbach via "Singold-Senkelbach"-Überleitung
    if r := fetch_hnd(HND_SINGOLD_Q_URL, "Singold Q"):
        sample.singold_q_time, sample.singold_q_m3s = r
    if r := fetch_hnd(HND_SINGOLD_W_URL, "Singold W"):
        sample.singold_w_time, sample.singold_w_cm = r

    # NEU v1.4: HND-Regenstationen (direkte DWD-Bodenmessungen)
    # Diese sind erheblich genauer als Open-Meteo-Gridwerte für lokale Schauer
    if r := fetch_hnd(HND_RAIN_HINDELANG_URL, "Regen Hindelang"):
        sample.hnd_rain_hindelang_time, sample.hnd_rain_hindelang_mm = r
    if r := fetch_hnd(HND_RAIN_BUCHLOE_URL, "Regen Buchloe"):
        sample.hnd_rain_buchloe_time, sample.hnd_rain_buchloe_mm = r
    if r := fetch_hnd(HND_RAIN_SCHWABMUENCHEN_URL, "Regen Schwabmünchen"):
        sample.hnd_rain_schwabmuenchen_time, sample.hnd_rain_schwabmuenchen_mm = r

    # Open-Meteo: aktuelle Beobachtungen
    for name, (lat, lon) in LOCATIONS.items():
        current = fetch_openmeteo_current(lat, lon, name)
        if current:
            precip = current.get("precipitation")
            if precip is not None:
                setattr(sample, f"rain_{name}_mm", precip)
            if name == "kempten":
                sample.temp_kempten_c = current.get("temperature_2m")
            elif name == "oberjoch":  # NEU
                sample.temp_oberjoch_c = current.get("temperature_2m")

    # Open-Meteo Forecast: für Kempten (bestehend) und Oberjoch (NEU)
    forecast_kempten = fetch_openmeteo_forecast(*LOCATIONS["kempten"], "kempten")
    if forecast_kempten:
        sample.forecast_rain_kempten_6h_mm = forecast_kempten.get("next_6h")
        sample.forecast_rain_kempten_24h_mm = forecast_kempten.get("next_24h")

    forecast_oberjoch = fetch_openmeteo_forecast(*LOCATIONS["oberjoch"], "oberjoch")
    if forecast_oberjoch:
        sample.forecast_rain_oberjoch_6h_mm = forecast_oberjoch.get("next_6h")
        sample.forecast_rain_oberjoch_24h_mm = forecast_oberjoch.get("next_24h")

    return sample


def main() -> int:
    log.info("=== Sammler-Lauf gestartet ===")
    try:
        sample = collect()
        append_sample(sample)
        log.info("=== Lauf erfolgreich beendet ===")
        return 0
    except Exception as e:
        log.exception("Unerwarteter Fehler: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
