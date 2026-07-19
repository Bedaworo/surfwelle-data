"""
Ruft die öffentliche Swellreport-Seite (surfwelleaugsburg.de/swell) automatisch
ab und pflegt die Daten in data/surfwelle_manual.csv ein.

Diese Seite benötigt KEIN Login (anders als buchung.surfwelleaugsburg.de) und
zeigt bis zu 3 Tage Historie über den URL-Parameter ?chart_hours=72. Die Werte
stecken direkt als Chart.js-Datenarray im HTML (serverseitig gerendert), lassen
sich also mit einem einfachen HTTP-GET + Regex extrahieren, ganz ohne
JavaScript-Ausführung oder Browser.

Ersetzt den manuellen Ablauf (Seite speichern -> HTML hochladen -> Skript
konvertiert): dieses Skript kann direkt alle 15 Minuten von GitHub Actions
laufen, genau wie collect.py.

Bonus: die Seite liefert nebenbei auch Wassertemperatur-Daten, die wir separat
in temperature_manual.csv ablegen (optional, kann ignoriert werden falls nicht
gebraucht).

Nutzt bewusst dieselbe Merge-Logik wie convert_surfwelle.py: bestehende Zeilen
bleiben erhalten, neue werden über den Zeitstempel dedupliziert.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SWELL_URL = "https://surfwelleaugsburg.de/swell?chart_hours=72"
TIMEOUT = 30
USER_AGENT = "surfwelle-augsburg-data-collector/1.5 (research project)"

SURFWELLE_CSV = Path(__file__).parent / "data" / "surfwelle_manual.csv"
TEMPERATURE_CSV = Path(__file__).parent / "data" / "temperature_manual.csv"


def fetch_swell_page() -> Optional[str]:
    """Holt die öffentliche Swellreport-Seite. Kein Login nötig."""
    try:
        resp = requests.get(
            SWELL_URL,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning("Swellreport-Seite nicht erreichbar: %s", e)
        return None


def extract_series(html: str, label_name: str) -> Optional[list[float]]:
    """
    Extrahiert ein Chart.js-Datenarray anhand seines Label-Namens, z.B.
    'Swell' oder 'Temperatur (°C)'.
    """
    pattern = re.compile(
        r"label:\s*'" + re.escape(label_name) + r"[^']*',\s*data:\s*(\[.*?\]),",
    )
    m = pattern.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def extract_labels(html: str) -> Optional[list[str]]:
    """Extrahiert die Zeitstempel-Labels (Format 'DD.MM. HH:MM')."""
    m = re.search(r"labels:\s*(\[.*?\]),", html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_label(label: str, reference_time: datetime) -> datetime:
    """
    Wandelt ein Label wie '18.07. 19:51' (ohne Jahr) in ein volles Datum um.
    Nimmt das aktuelle Jahr an; falls das Ergebnis mehr als 1 Tag in der
    Zukunft läge (Jahreswechsel-Randfall), wird das Vorjahr verwendet.
    """
    day_month, time_part = label.split(". ", 1)
    day, month = day_month.split(".")
    hour, minute = time_part.split(":")
    year = reference_time.year
    dt = datetime(year, int(month), int(day), int(hour), int(minute))
    if dt > reference_time + timedelta(days=1):
        dt = dt.replace(year=year - 1)
    return dt


def load_existing_csv(path: Path) -> dict[str, float]:
    """Liest eine bestehende time/value-CSV als Dict {time: value} ein."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        value_col = reader.fieldnames[1]
        return {row["time"]: float(row[value_col]) for row in reader}


def write_merged_csv(path: Path, merged: dict[str, float], value_col: str) -> None:
    """Schreibt ein {time: value}-Dict sortiert nach Zeit in eine CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", value_col])
        for t in sorted(merged.keys()):
            writer.writerow([t, merged[t]])


def process_series(
    labels: list[str],
    values: list[float],
    reference_time: datetime,
    csv_path: Path,
    value_col: str,
    label_for_log: str,
) -> None:
    """Parsed eine Serie, merged sie mit der bestehenden CSV, schreibt sie."""
    new_data = {}
    for label, value in zip(labels, values):
        try:
            dt = parse_label(label, reference_time)
        except (ValueError, IndexError):
            log.warning("Konnte Label nicht parsen: %r", label)
            continue
        # ISO-Format mit Europe/Berlin-Offset waere praeziser, aber die Seite
        # liefert keine Zeitzoneninfo. Wir uebernehmen die Zeit naiv, wie sie
        # auch beim bisherigen manuellen Ablauf (Browser-lokale Zeit) anfiel.
        new_data[dt.isoformat()] = value

    existing = load_existing_csv(csv_path)
    merged = {**existing, **new_data}

    n_before = len(existing)
    n_after = len(merged)
    log.info(
        "%s: %d neue Rohpunkte, %d bereits vorhanden, %d nach Merge (+%d)",
        label_for_log, len(new_data), n_before, n_after, n_after - n_before,
    )

    write_merged_csv(csv_path, merged, value_col)


def main() -> int:
    log.info("=== Swell-Abruf gestartet ===")
    html = fetch_swell_page()
    if html is None:
        log.error("Konnte Swellreport-Seite nicht laden - Abbruch.")
        return 1

    labels = extract_labels(html)
    if labels is None:
        log.error(
            "Konnte 'labels'-Array nicht im HTML finden - hat sich die Seite "
            "strukturell geaendert?"
        )
        return 1

    swell_values = extract_series(html, "Swell")
    if swell_values is None:
        log.error("Konnte Swell-Datenarray nicht im HTML finden.")
        return 1
    if len(swell_values) != len(labels):
        log.error(
            "Anzahl Swell-Werte (%d) passt nicht zur Anzahl Labels (%d) - "
            "Abbruch zur Sicherheit.",
            len(swell_values), len(labels),
        )
        return 1

    reference_time = datetime.now()
    process_series(
        labels, swell_values, reference_time,
        SURFWELLE_CSV, "percent", "Swell",
    )

    # Bonus: Temperatur, falls vorhanden. Nicht kritisch - bei Fehlern nur warnen.
    temp_values = extract_series(html, "Temperatur")
    if temp_values is not None and len(temp_values) == len(labels):
        process_series(
            labels, temp_values, reference_time,
            TEMPERATURE_CSV, "temp_c", "Temperatur",
        )
    else:
        log.warning("Temperatur-Datenarray nicht gefunden oder Laenge weicht ab - übersprungen.")

    log.info("=== Swell-Abruf erfolgreich beendet ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
