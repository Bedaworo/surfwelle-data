"""
Sammelt alle 15 Minuten Daten zu:
- HND Pegel Türkheim (Wertach) — Abfluss
- HND Pegel Augsburg-Oberhausen (Wertach) — Wasserstand und Abfluss
- Open-Meteo (DWD-basiert): Niederschlag in Kempten, Marktoberdorf und Augsburg
  sowie Niederschlags-Vorhersage 24h für Kempten

Jeder Lauf hängt eine Zeile an data/collected.csv an.

Defensiv: Wenn eine Quelle ausfällt, wird für deren Spalten None geschrieben,
aber die Zeile wird trotzdem gespeichert. Fehler werden klar geloggt.
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
USER_AGENT = "surfwelle-augsburg-data-collector/1.1 (research project)"

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
    collected_at: str = ""

    tuerkheim_q_m3s: Optional[float] = None
    tuerkheim_time: Optional[str] = None
    oberhausen_q_m3s: Optional[float] = None
    oberhausen_q_time: Optional[str] = None
    oberhausen_w_cm: Optional[float] = None
    oberhausen_w_time: Optional[str] = None

    rain_kempten_mm: Optional[float] = None
    rain_marktoberdorf_mm: Optional[float] = None
    rain_augsburg_mm: Optional[float] = None
    temp_kempten_c: Optional[float] = None

    forecast_rain_kempten_24h_mm: Optional[float] = None
    forecast_rain_kempten_6h_mm: Optional[float] = None


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

    # Erste Zeile = neuester Wert
    timestamp_str = str(df.iloc[0, 0]).strip()
    raw_value = df.iloc[0, 1]

    try:
        # pandas hat decimal="," schon angewendet → Wert sollte float sein
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
    sample = Sample(collected_at=datetime.now(timezone.utc).isoformat())

    if r := fetch_hnd(HND_TUERKHEIM_URL, "Türkheim Q"):
        sample.tuerkheim_time, sample.tuerkheim_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_Q_URL, "Oberhausen Q"):
        sample.oberhausen_q_time, sample.oberhausen_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_W_URL, "Oberhausen W"):
        sample.oberhausen_w_time, sample.oberhausen_w_cm = r

    for name, (lat, lon) in LOCATIONS.items():
        current = fetch_openmeteo_current(lat, lon, name)
        if current:
            precip = current.get("precipitation")
            if precip is not None:
                setattr(sample, f"rain_{name}_mm", precip)
            if name == "kempten":
                sample.temp_kempten_c = current.get("temperature_2m")

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
