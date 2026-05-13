"""
Sammelt alle 15 Minuten Daten zu:
- HND Pegel Türkheim (Wertach) — Abfluss
- HND Pegel Augsburg-Oberhausen (Wertach) — Wasserstand und Abfluss
- Open-Meteo (DWD-basiert): Niederschlag in Kempten, Marktoberdorf und Augsburg
  sowie Niederschlags-Vorhersage 24h für Kempten

Jeder Lauf hängt eine Zeile an data/collected.csv an.

Defensiv: Wenn eine Quelle ausfällt, wird für deren Spalten None geschrieben,
aber die Zeile wird trotzdem gespeichert.
"""

from __future__ import annotations

import csv
import logging
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
TIMEOUT = 30  # Sekunden pro Request
USER_AGENT = "surfwelle-augsburg-data-collector/1.0 (research project)"

# HND-Pegel
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

# Wetterstationen (Lat/Lon)
# Kempten: Wertach-Oberlauf, Hauptzufluss aus dem Allgäu
# Marktoberdorf: weiter unten im Einzugsgebiet
# Augsburg: Zwischengebiet / lokaler Niederschlag
LOCATIONS = {
    "kempten":       (47.7333, 10.3167),
    "marktoberdorf": (47.7800, 10.6167),
    "augsburg":      (48.3667, 10.8833),
}


# -----------------------------------------------------------------------------
# Datenmodell
# -----------------------------------------------------------------------------


@dataclass
class Sample:
    """Eine Zeile in der CSV. Alle Felder optional; collect() füllt was geht."""
    collected_at: str = ""

    # HND
    tuerkheim_q_m3s: Optional[float] = None
    tuerkheim_time: Optional[str] = None
    oberhausen_q_m3s: Optional[float] = None
    oberhausen_q_time: Optional[str] = None
    oberhausen_w_cm: Optional[float] = None
    oberhausen_w_time: Optional[str] = None

    # Open-Meteo: vergangene Stunde Niederschlag (mm)
    rain_kempten_mm: Optional[float] = None
    rain_marktoberdorf_mm: Optional[float] = None
    rain_augsburg_mm: Optional[float] = None
    temp_kempten_c: Optional[float] = None

    # Open-Meteo Forecast: erwarteter Niederschlag nächste 24h für Kempten (mm)
    forecast_rain_kempten_24h_mm: Optional[float] = None
    # und nächste 6h für früher Vorlauf
    forecast_rain_kempten_6h_mm: Optional[float] = None


# -----------------------------------------------------------------------------
# HND-Scraping
# -----------------------------------------------------------------------------


HND_ROW_RE = re.compile(
    r"<td>\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\s*</td>\s*"
    r"<td>\s*([-\d,\.]+)\s*</td>"
)


def _parse_hnd_table(html: str) -> Optional[tuple[str, float]]:
    """Parst die HND-Tabelle und liefert (timestamp, wert) für die neueste Zeile."""
    matches = HND_ROW_RE.findall(html)
    if not matches:
        return None
    # Neueste Zeile ist oben in der Tabelle
    timestamp_str, value_str = matches[0]
    try:
        value = float(value_str.replace(",", "."))
    except ValueError:
        log.warning("Konnte HND-Wert nicht parsen: %r", value_str)
        return None
    # Format: 13.05.2026 21:00
    dt = datetime.strptime(timestamp_str, "%d.%m.%Y %H:%M")
    return dt.isoformat(), value


def fetch_hnd(url: str, label: str) -> Optional[tuple[str, float]]:
    """Holt die HND-Tabelle und gibt den neuesten Messwert zurück."""
    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        result = _parse_hnd_table(resp.text)
        if result is None:
            log.warning("HND %s: keine Daten in Antwort gefunden", label)
            return None
        log.info("HND %s: %s = %s", label, result[0], result[1])
        return result
    except requests.RequestException as e:
        log.warning("HND %s fehlgeschlagen: %s", label, e)
        return None
    except Exception as e:
        log.warning("HND %s Parse-Fehler: %s", label, e)
        return None


# -----------------------------------------------------------------------------
# Open-Meteo
# -----------------------------------------------------------------------------


def fetch_openmeteo_current(lat: float, lon: float, label: str) -> dict:
    """Holt aktuelle Wetterdaten (letzte Stunde) für einen Standort."""
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
            "Open-Meteo %s: Regen=%.2fmm, Temp=%.1f°C",
            label,
            current.get("precipitation", 0) or 0,
            current.get("temperature_2m", 0) or 0,
        )
        return current
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo %s fehlgeschlagen: %s", label, e)
        return {}


def fetch_openmeteo_forecast(lat: float, lon: float, label: str) -> dict:
    """Holt Niederschlags-Vorhersage für die nächsten 24h."""
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

        # Finde Index der aktuellen Stunde
        now = datetime.now()
        current_hour_str = now.strftime("%Y-%m-%dT%H:00")
        try:
            idx = times.index(current_hour_str)
        except ValueError:
            # Falls aktuelle Stunde nicht exakt drin ist, nimm den ersten Eintrag
            # (Open-Meteo gibt immer ab 00:00 des Tages aus)
            idx = 0

        # Summe der nächsten 6h und 24h ab jetzt
        next_6h = sum(v for v in precip[idx:idx + 6] if v is not None)
        next_24h = sum(v for v in precip[idx:idx + 24] if v is not None)

        log.info(
            "Open-Meteo Forecast %s: 6h=%.2fmm, 24h=%.2fmm",
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
    """Hängt einen Datenpunkt an die CSV an. Schreibt Header, wenn Datei neu."""
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()

    row = asdict(sample)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    log.info("Geschrieben in %s", CSV_PATH)


# -----------------------------------------------------------------------------
# Hauptablauf
# -----------------------------------------------------------------------------


def collect() -> Sample:
    sample = Sample(
        collected_at=datetime.now(timezone.utc).isoformat(),
    )

    # HND
    if r := fetch_hnd(HND_TUERKHEIM_URL, "Türkheim Abfluss"):
        sample.tuerkheim_time, sample.tuerkheim_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_Q_URL, "Oberhausen Abfluss"):
        sample.oberhausen_q_time, sample.oberhausen_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_W_URL, "Oberhausen Wasserstand"):
        sample.oberhausen_w_time, sample.oberhausen_w_cm = r

    # Open-Meteo: aktuelle Beobachtungen
    for name, (lat, lon) in LOCATIONS.items():
        current = fetch_openmeteo_current(lat, lon, name)
        if current:
            precip = current.get("precipitation")
            if precip is not None:
                setattr(sample, f"rain_{name}_mm", precip)
            if name == "kempten":
                sample.temp_kempten_c = current.get("temperature_2m")

    # Open-Meteo: Forecast nur für Kempten (Hauptzufluss-Gebiet)
    forecast = fetch_openmeteo_forecast(*LOCATIONS["kempten"], "kempten")
    if forecast:
        sample.forecast_rain_kempten_6h_mm = forecast.get("next_6h")
        sample.forecast_rain_kempten_24h_mm = forecast.get("next_24h")

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
