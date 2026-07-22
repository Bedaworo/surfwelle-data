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
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

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


def load_existing_csv(path: Path) -> tuple[dict[str, float], int]:
    """
    Liest eine bestehende time/value-CSV als Dict {time: value} ein und
    normalisiert dabei die Zeitstempel. Gibt zusaetzlich zurueck, wie viele
    Zeilen dabei zusammengefallen sind (Dubletten aus zwei Datenquellen, die
    dieselbe Messung mit unterschiedlichen Sekunden gespeichert hatten).
    """
    if not path.exists():
        return {}, 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        value_col = reader.fieldnames[1]
        out: dict[str, float] = {}
        seen = 0
        for row in reader:
            key = canonical_key(row["time"])
            if key is None:
                log.warning("Zeitstempel nicht parsbar, Zeile uebersprungen: %r", row["time"])
                continue
            seen += 1
            out.setdefault(key, float(row[value_col]))   # erster Wert gewinnt
        return out, seen - len(out)


def write_merged_csv(path: Path, merged: dict[str, float], value_col: str) -> None:
    """Schreibt ein {time: value}-Dict sortiert nach Zeit in eine CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def instant(k: str) -> datetime:
        return datetime.fromisoformat(k)

    # Nach dem echten Zeitpunkt sortieren, nicht nach dem String: bei der
    # Zeitumstellung im Herbst wechselt der Offset, dann waere eine
    # String-Sortierung nicht mehr chronologisch.
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", value_col])
        for t in sorted(merged.keys(), key=instant):
            writer.writerow([t, merged[t]])


def canonical_key(value) -> Optional[str]:
    """
    Bringt einen Zeitstempel auf die kanonische Form:
    ISO 8601 mit explizitem Europe/Berlin-Offset, auf volle Minuten gerundet.

        2026-07-14T16:36:07.321415+02:00  ->  2026-07-14T16:36:00+02:00
        2026-07-14T16:36:00               ->  2026-07-14T16:36:00+02:00

    Warum mit Offset und nicht naiv: bei der Zeitumstellung im Oktober gibt es
    die Stunde 02:00-03:00 zweimal. Ohne Offset waeren diese Werte nicht
    unterscheidbar. Warum nicht UTC: die Datei soll ohne Umrechnen lesbar
    bleiben - alle anderen Zeitangaben im Projekt sind Ortszeit.

    Historisch enthielt die Datei zwei Formate: manuelle Importe mit Offset
    und Mikrosekunden, automatische Abrufe ohne Offset auf volle Minuten.
    Beide beschreiben dieselben Messungen; erst durch die Rundung fallen die
    Dubletten zusammen.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None

    if dt.tzinfo is None:
        # Ohne Offset ist Ortszeit gemeint - so hat dieses Skript frueher
        # geschrieben. fold=0 waehlt bei der doppelten Herbststunde die
        # erste (Sommerzeit); eindeutig aufloesen laesst sie sich nicht.
        dt = dt.replace(tzinfo=BERLIN)
    dt = dt.astimezone(BERLIN)

    if dt.second or dt.microsecond:        # auf die naechste Minute runden
        dt += timedelta(seconds=30)
    return dt.replace(second=0, microsecond=0).isoformat()


def find_spikes(
    series: dict[str, float],
    jump: float = 30.0,
    max_block_minutes: float = 60.0,
    return_tol: float = 12.0,
    max_step_minutes: float = 10.0,
    only_after: Optional[datetime] = None,
) -> set[str]:
    """
    Findet Sensor-Artefakte: die Messung springt sprunghaft auf ein falsches
    Niveau, bleibt kurz dort und kehrt genauso sprunghaft zurueck.

    Echtes Beispiel (14.07.2026, 5-Minuten-Takt):
        21.7 -> 84.7 -> 84.7 -> 84.7 -> 83.4 -> 17.6
    Der Fabrikkanal lag in diesen 20 Minuten unveraendert bei 3.9 m3/s - eine
    echte 85-cm-Welle ist dabei ausgeschlossen.

    Bewusst als BLOCK und nicht als Einzelpunkt gesucht: das Artefakt dauerte
    vier Messwerte, ein 3-Punkt-Filter haette es nicht gefunden.

    Die Schwelle von 30 cm je Messschritt liegt deutlich ueber dem, was echte
    Wellen tun - ueber den gesamten Datenbestand liegt das 99.9-Perzentil der
    Schrittaenderung bei 21 cm, und nur das oben gezeigte Ereignis
    ueberschreitet 30 cm ueberhaupt. Eine unruhige, aber echte Welle bleibt
    damit erhalten.

    max_step_minutes verhindert, dass ueber eine Datenluecke hinweg gefiltert
    wird - dort sind grosse Spruenge voellig normal.

    only_after begrenzt die Pruefung auf einen Zeitraum; aeltere Werte bleiben
    unangetastet, damit ein Routinelauf nicht die ganze Historie umschreibt.
    """
    if len(series) < 3:
        return set()

    # Die CSV enthaelt historisch gewachsen zwei Formate: aeltere Importe mit
    # Zeitzonen-Offset ("...+02:00"), neuere ohne. Beide muessen vergleichbar
    # gemacht werden, sonst schlaegt schon die Subtraktion fehl. Aware-Werte
    # werden nach Europe/Berlin umgerechnet und der Offset abgestreift - das
    # entspricht genau dem, was dieses Skript selbst schreibt.
    parsed: list[tuple[str, datetime, float]] = []
    for k in series:
        try:
            dt = datetime.fromisoformat(k)
        except ValueError:
            log.warning("Zeitstempel nicht parsbar, wird uebersprungen: %r", k)
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(BERLIN).replace(tzinfo=None)
        parsed.append((k, dt, series[k]))

    parsed.sort(key=lambda p: p[1])

    spikes: set[str] = set()
    i = 0
    while i < len(parsed) - 1:
        _, t0, v0 = parsed[i]              # letzter Wert vor dem Sprung
        _, t1, v1 = parsed[i + 1]

        step = (t1 - t0).total_seconds() / 60.0
        rise = v1 - v0
        if step > max_step_minutes or abs(rise) <= jump:
            i += 1
            continue

        # Sprung erkannt - Rueckkehr auf das Ausgangsniveau suchen
        end = None
        for k in range(i + 2, len(parsed)):
            _, tk, vk = parsed[k]
            if (tk - t1).total_seconds() / 60.0 > max_block_minutes:
                break
            if (tk - parsed[k - 1][1]).total_seconds() / 60.0 > max_step_minutes:
                break                      # Luecke im Block - nicht beurteilbar
            back = vk - parsed[k - 1][2]
            if abs(back) > jump and back * rise < 0 and abs(vk - v0) <= return_tol:
                end = k
                break

        if end is None:
            i += 1                         # kein Rueckweg - echte Aenderung
            continue

        for j in range(i + 1, end):
            key, t, _ = parsed[j]
            if only_after is None or t >= only_after:
                spikes.add(key)
        i = end

    return spikes


def process_series(
    labels: list[str],
    values: list[float],
    reference_time: datetime,
    csv_path: Path,
    value_col: str,
    label_for_log: str,
    despike: bool = False,
    despike_window_days: Optional[int] = 14,
) -> None:
    """Parsed eine Serie, merged sie mit der bestehenden CSV, schreibt sie."""
    new_data = {}
    for label, value in zip(labels, values):
        try:
            dt = parse_label(label, reference_time)
        except (ValueError, IndexError):
            log.warning("Konnte Label nicht parsen: %r", label)
            continue
        # Die Seite liefert keine Zeitzoneninfo; die Labels sind Ortszeit.
        # canonical_key haengt den passenden Offset an und rundet auf Minuten.
        key = canonical_key(dt)
        if key is None:
            log.warning("Zeitstempel nicht normalisierbar: %r", dt)
            continue
        new_data[key] = value

    existing, collapsed = load_existing_csv(csv_path)
    if collapsed:
        log.info(
            "%s: %d doppelte Zeitstempel beim Normalisieren zusammengefasst",
            label_for_log, collapsed,
        )
    merged = {**existing, **new_data}

    n_before = len(existing)
    n_after = len(merged)
    log.info(
        "%s: %d neue Rohpunkte, %d bereits vorhanden, %d nach Merge (+%d)",
        label_for_log, len(new_data), n_before, n_after, n_after - n_before,
    )

    if despike:
        # Auf dem GESAMTEN Merge pruefen, nicht nur auf den neuen Punkten:
        # ein Wert am Rand des 72h-Fensters hat beim ersten Abruf noch keinen
        # rechten Nachbarn. Beim naechsten Lauf ist er da und der Punkt wird
        # nachtraeglich beurteilt.
        only_after = None
        if despike_window_days is not None:
            only_after = reference_time - timedelta(days=despike_window_days)
        spikes = find_spikes(merged, only_after=only_after)
        if spikes:
            for k in sorted(spikes):
                log.info("  Ausreisser verworfen: %s = %s", k, merged[k])
            for k in spikes:
                del merged[k]
            log.info("%s: %d Ausreisser entfernt", label_for_log, len(spikes))

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
    # --clean-all prueft einmalig die gesamte Historie statt nur der letzten
    # 14 Tage. Fuer den Routinelauf nicht noetig und unnoetig invasiv.
    window = None if "--clean-all" in sys.argv else 14
    if window is None:
        log.info("Aufraeum-Modus: Ausreisser werden in der GESAMTEN Historie geprueft")

    process_series(
        labels, swell_values, reference_time,
        SURFWELLE_CSV, "percent", "Swell",
        despike=True, despike_window_days=window,
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
