"""
Konvertiert den JSON-Block von surfwelleaugsburg.de in eine saubere CSV.

Erkennt automatisch drei mögliche Quellformate:
1. Reines JSON-Array (z.B. wenn schon vorverarbeitet)
2. Altes Format: buchung.surfwelleaugsburg.de/swell/ - JSON im
   <script id="historical-swell-data">-Tag (login-geschützt, zeigt aber
   längere Historie, ca. 10+ Tage)
3. NEUES Format (ab v1.5): die öffentliche, login-freie Seite
   surfwelleaugsburg.de/swell - Chart.js-Datenarrays direkt im HTML
   (kein Login nötig, zeigt aber nur bis zu 3 Tage zurück)

Verhalten:
- Filtert offensichtliche Sensor-Spikes raus (isolierte ~29-30er Werte
  zwischen viel niedrigeren Nachbarn).
- Wenn die Output-CSV schon existiert: liest sie ein, merged sie mit den
  neuen Daten und behält ALLE Punkte (deduplized über Zeitstempel).
  So gehen keine alten Daten verloren, auch wenn die HTML-Quelle nur die
  letzten paar Tage zeigt.

Hinweis: Für eine Rückfüllung mehrerer Wochen Historie wird IMMER das ALTE
Format (buchung.surfwelleaugsburg.de/swell/, Login nötig) gebraucht - die
neue öffentliche Seite zeigt grundsätzlich nur maximal 3 Tage zurück, egal
wie sie verarbeitet wird.

Usage:
  python convert_surfwelle.py <input.html-oder-json> <output.csv>
"""
import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _parse_short_label(label: str, reference_time: datetime) -> datetime:
    """
    Wandelt ein Chart.js-Label wie '18.07. 19:51' (ohne Jahr) in ein volles
    Datum um. Nimmt das Jahr von reference_time an; falls das Ergebnis mehr
    als 1 Tag in der Zukunft läge (Jahreswechsel-Randfall), wird das Vorjahr
    verwendet.
    """
    day_month, time_part = label.split(". ", 1)
    day, month = day_month.split(".")
    hour, minute = time_part.split(":")
    year = reference_time.year
    dt = datetime(year, int(month), int(day), int(hour), int(minute))
    if dt > reference_time + timedelta(days=1):
        dt = dt.replace(year=year - 1)
    return dt


def _try_load_new_chartjs_format(text: str) -> list[dict] | None:
    """
    Versucht, das NEUE Format zu lesen (öffentliche Seite, Chart.js-Arrays).
    Gibt None zurück, wenn die erwarteten Strukturen nicht gefunden werden.
    """
    m_labels = re.search(r"labels:\s*(\[.*?\]),", text)
    m_data = re.search(r"label:\s*'Swell',\s*data:\s*(\[.*?\]),", text)
    if not m_labels or not m_data:
        return None

    try:
        labels = json.loads(m_labels.group(1))
        values = json.loads(m_data.group(1))
    except json.JSONDecodeError:
        return None

    if len(labels) != len(values) or not labels:
        return None

    reference_time = datetime.now()
    result = []
    for label, value in zip(labels, values):
        try:
            dt = _parse_short_label(label, reference_time)
        except (ValueError, IndexError):
            continue
        result.append({"time": dt.isoformat(), "percent": value})
    return result if result else None


def load_data(path: Path) -> list[dict]:
    """Liest das JSON entweder aus reiner JSON-Datei oder aus HTML-Quelltext."""
    text = path.read_text(encoding="utf-8")
    text_stripped = text.strip()

    # Fall 1: reines JSON
    if text_stripped.startswith("["):
        return json.loads(text_stripped)

    # Fall 2: altes Format - aus dem <script>-Tag extrahieren
    # Toleriert beliebig viel Whitespace zwischen Tags und JSON
    # (Browser fuegen beim "Seite speichern" oft Umbrueche ein).
    pattern = re.compile(
        r'<script[^>]*id=["\']historical-swell-data["\'][^>]*>'
        r'\s*(\[.*?\])\s*'
        r'</script>',
        re.DOTALL,
    )
    m = pattern.search(text)
    if m:
        return json.loads(m.group(1))

    # Fall 3: NEUES Format - Chart.js-Arrays der öffentlichen Seite
    new_format_result = _try_load_new_chartjs_format(text)
    if new_format_result is not None:
        return new_format_result

    # Fallback: vielleicht ist das alte Tag anders strukturiert.
    # Suche das JSON-Array direkt am ersten Vorkommen von '"percent"'.
    idx = text.find('"percent"')
    if idx > 0:
        # Vorlaeufer "[" finden
        start = text.rfind("[", 0, idx)
        if start > 0:
            # Passendes "]" finden (Bracket-Matching mit String-Awareness)
            depth = 0
            in_string = False
            escape = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        json_text = text[start:i + 1]
                        return json.loads(json_text)

    # Wenn auch das fehlschlaegt: ausfuehrliche Fehlermeldung
    snippet_start = text.find("historical-swell-data")
    if snippet_start > 0:
        snippet = text[snippet_start:snippet_start + 300].replace("\n", " ")
        raise ValueError(
            "JSON-Block konnte nicht extrahiert werden, obwohl "
            "'historical-swell-data' im HTML vorkommt.\n"
            f"Umgebung (300 Zeichen): {snippet}"
        )
    raise ValueError(
        "Im Input wurde weder das alte Format (historical-swell-data-Block) "
        "noch das neue Format (Chart.js-Arrays der öffentlichen Seite) noch "
        "ein generisches JSON-Array mit 'percent'-Eintraegen gefunden.\n"
        "Pruefe: hast du wirklich den Seitenquelltext gespeichert (Strg+U "
        "im Browser) und nicht ein leeres Frame-HTML?"
    )


def filter_spikes(data: list[dict], threshold: float = 15.0) -> tuple[list[dict], int]:
    """
    Entfernt isolierte Spikes: Punkt, dessen Wert >threshold höher oder niedriger
    liegt als beide direkten Nachbarn, wobei die Nachbarn sich gegenseitig ähnlich
    sind. Klassische Sensor-Glitches (z.B. die ~29-Werte zwischen 7er-Phasen).
    """
    if len(data) < 3:
        return data, 0

    keep = [True] * len(data)
    removed = 0

    for i in range(1, len(data) - 1):
        prev_v = data[i - 1]["percent"]
        curr_v = data[i]["percent"]
        next_v = data[i + 1]["percent"]
        neighbor_mean = (prev_v + next_v) / 2
        neighbor_diff = abs(prev_v - next_v)

        if abs(curr_v - neighbor_mean) > threshold and neighbor_diff < 5:
            keep[i] = False
            removed += 1

    return [d for d, k in zip(data, keep) if k], removed


def load_existing_csv(path: Path) -> list[dict]:
    """Liest die bestehende CSV ein, falls vorhanden."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [{"time": row["time"], "percent": float(row["percent"])} for row in reader]


def merge_data(existing: list[dict], new: list[dict]) -> list[dict]:
    """
    Fügt bestehende und neue Daten zusammen, dedupliziert über den Zeitstempel,
    sortiert chronologisch.
    Bei doppelten Zeitstempeln gewinnt der neue Wert (Quelle wurde frisch geholt).
    """
    merged = {d["time"]: d["percent"] for d in existing}
    for d in new:
        merged[d["time"]] = d["percent"]
    result = [{"time": t, "percent": v} for t, v in merged.items()]
    result.sort(key=lambda d: d["time"])
    return result


def main(input_path: str, output_path: str) -> None:
    new_data = load_data(Path(input_path))
    print(f"Aus HTML/JSON eingelesen: {len(new_data)} Datenpunkte")

    new_filtered, n_removed = filter_spikes(new_data)
    print(f"Spikes entfernt: {n_removed}")

    existing = load_existing_csv(Path(output_path))
    if existing:
        print(f"Bestehende CSV: {len(existing)} Datenpunkte")
        merged = merge_data(existing, new_filtered)
        n_added = len(merged) - len(existing)
        print(f"Neue Punkte hinzugefuegt: {n_added}")
        print(f"Gesamt nach Merge: {len(merged)}")
    else:
        print("Keine bestehende CSV gefunden - neue wird angelegt")
        merged = new_filtered

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "percent"])
        for d in merged:
            writer.writerow([d["time"], d["percent"]])

    vals = [d["percent"] for d in merged]
    print(f"Wertebereich: {min(vals):.1f} - {max(vals):.1f}")
    print(f"Erster Zeitstempel: {merged[0]['time']}")
    print(f"Letzter Zeitstempel: {merged[-1]['time']}")
    print(f"Geschrieben nach: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
