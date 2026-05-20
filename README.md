# Surfwelle Augsburg — Datensammler

Sammelt automatisch alle 15 Minuten Pegel- und Wetterdaten rund um die
Surfwelle am Senkelbach in Augsburg, um eine Pegel-Prognose zu bauen.

## Was wird gesammelt

### Pegelkette Wertach (von Quelle bis Augsburg)

| Quelle | Daten | Fluss-km | Bedeutung |
|---|---|---|---|
| HND Bayern | Grüntensee Seepegel — Wasserstand (m ü. NN) | ~130 | Pufferspeicher am Anfang |
| HND Bayern | Biessenhofen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~80 | Nach Speicher, vor Mittellauf |
| HND Bayern | Türkheim / Wertach — Abfluss (m³/s) | ~42 | Hauptsignal kurz vor Augsburg |
| HND Bayern | Augsburg-Oberhausen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~3 | Nach Wertachkanal-Abzweig |

### Wetter (DWD-Daten via Open-Meteo)

| Station | Messwerte | Bedeutung |
|---|---|---|
| Oberjoch (1180m) | Niederschlag + Temperatur + Forecast | Wertach-Quellgebiet, Schneeschmelze |
| Kaufbeuren | Niederschlag | Mittellauf, Zwischengebiet |
| Marktoberdorf | Niederschlag | Mittellauf |
| Kempten | Niederschlag + Temperatur + Forecast | Allgäu-Großwetter (Iller-Tal) |
| Augsburg | Niederschlag | Lokaler Einfluss Senkelbach |

Alle 15 Minuten ein Lauf, ein Datenpunkt pro Lauf in `data/collected.csv`.

### Manuell ergänzt

- Pegelstand der Surfwelle aus `surfwelleaugsburg.de` (alle paar Tage)
- Notizen zu Events (Bachablass, Wehrsteuerung, Wartung)

## Setup

### 1. Repo nach GitHub bringen

```bash
cd surfwelle-data
git init
git add .
git commit -m "Initialer Stand"
# Auf GitHub ein leeres Repo anlegen, dann:
git branch -M main
git remote add origin git@github.com:<DEIN-USERNAME>/<REPO-NAME>.git
git push -u origin main
```

### 2. Workflow-Permissions freischalten

In GitHub: **Settings → Actions → General → Workflow permissions**:
- `Read and write permissions` aktivieren
- Speichern

### 3. Erstlauf manuell testen

In GitHub: **Actions** → `Collect water data` → `Run workflow`.

Nach 1-2 Minuten sollte ein neuer Commit `data: ...` auftauchen und
`data/collected.csv` existieren.

### 4. Automatik läuft ab dann

Der Cron-Job läuft alle 15 Minuten von alleine. GitHub Actions ist auf
öffentlichen Repos kostenlos, auf privaten gibt's 2.000 Freiminuten/Monat
— ein Lauf braucht ~30 Sekunden, also ~25 Stunden/Monat, locker drin.

## Datenstruktur

Im Ordner `data/` liegen drei CSV-Dateien mit klarer Aufgabenteilung:

| Datei | Wer pflegt | Inhalt |
|---|---|---|
| `collected.csv` | Bot, alle 15 Min | Pegel, Stausee, Wetter (24 Spalten) |
| `surfwelle_manual.csv` | Mensch, alle 1-2 Wochen | Pegel der Surfwelle aus dem HTML |
| `events.csv` | Mensch, bei Bedarf | Bachablässe, Wehrsteuerungen, Bauarbeiten |

Die strikte Trennung verhindert, dass der Bot beim nächsten Commit
manuelle Änderungen überschreibt oder Git-Konflikte produziert. Bei der
späteren Analyse werden die Dateien einfach über die Zeitstempel
zusammengejoint.

### Spalten in `collected.csv`

Die ersten 13 Spalten sind Bestandsdaten (Skript-Version 1.x), die ab
Spalte 14 sind Erweiterungen aus Version 1.2. Das Skript migriert die
CSV automatisch beim ersten Lauf nach einem Update: alte Zeilen bekommen
leere Werte für die neuen Spalten, niemand muss von Hand eingreifen.

## Manuelle Datenpflege

### Events (`data/events.csv`)

Format:

```csv
time,event,note
2026-05-11T12:30:00+02:00,bachablass_ende,Wehr nach Wartung geöffnet
2026-05-13T18:30:00+02:00,wehrsteuerung,Pegel sinkt sichtbar
2026-05-20T08:00:00+02:00,bachablass_start,zweiwöchige Wartung angekündigt
```

Wenn die `note` ein Komma enthält, muss sie in Anführungszeichen:

```csv
2026-05-25T14:00:00+02:00,beobachtung,"starker Regen, Pegel +30%"
```

Sinnvolle Werte für `event` (können aber auch frei vergeben werden):
- `bachablass_start` / `bachablass_ende`
- `wehrsteuerung` — wenn das Wehr sichtbar bedient wurde
- `wartung` — Bauarbeiten am Bach
- `beobachtung` — alles andere was auffällt

Eintragen am einfachsten direkt auf GitHub: `data/events.csv` öffnen →
Stift-Symbol (Edit) → Zeile anhängen → Commit.

### Surfwellen-Pegel (`data/surfwelle_manual.csv`)

Format:

```csv
time,percent
2026-05-04T09:34:53+02:00,7.5
2026-05-04T09:39:52+02:00,6.9
```

Die Rohdaten stehen im HTML der Vereins-Buchungsseite
(`buchung.surfwelleaugsburg.de/swell/`) als JSON-Block im
`<script id="historical-swell-data" type="application/json">`-Tag.
Alle paar Tage / Wochen:

1. Pegelstand-Seite (`/swell/`) im Browser öffnen und als HTML speichern
2. JSON-Block extrahieren und in CSV umwandeln — am einfachsten über
   das mitgelieferte `convert_surfwelle.py` (Doppelklick auf
   `Surfwelle_aktualisieren.bat` unter Windows)
3. Die fertige CSV auf GitHub hochladen und die bestehende ersetzen

## Daten anschauen

Alle Daten landen in `data/*.csv`. Mit pandas analysierbar:

```python
import pandas as pd
collected = pd.read_csv("data/collected.csv", parse_dates=["collected_at"])
surfwelle = pd.read_csv("data/surfwelle_manual.csv", parse_dates=["time"])
events = pd.read_csv("data/events.csv", parse_dates=["time"])
```

## Robustheit

Das Skript ist defensiv geschrieben: Wenn eine einzelne Datenquelle ausfällt
(HND-Wartung, Open-Meteo-Timeout), werden die anderen trotzdem gespeichert
und die fehlende Spalte bleibt leer. Das macht später keine Probleme beim
Einlesen mit pandas.

Hinweise zu Lücken:
- GitHub Actions hat keine harte Garantie für 15-Minuten-Pünktlichkeit;
  bei hoher Plattform-Last können einzelne Läufe um 5-10 Minuten verspätet
  oder gelegentlich gar nicht starten.
- HND-Werte sind 15-Minuten-Werte; das Skript scrapt jedes Mal die Tabelle
  und bekommt damit immer den neuesten verfügbaren Wert. Identische
  Zeitstempel in Folge sind normal und kein Fehler.
- Open-Meteo aktualisiert die DWD-Beobachtungen einmal pro Stunde — also
  4 von 4 Läufen pro Stunde sehen denselben Niederschlagswert.

## Erweiterung des Skripts

Neue Datenquellen können am Ende der `Sample`-Dataclass in `collect.py`
ergänzt werden. Das Skript erkennt das beim nächsten Lauf automatisch und
migriert die CSV (neue Spalten werden hinten angehängt, alte Zeilen
bekommen leere Werte). Bestehende Spalten dürfen nicht umsortiert oder
umbenannt werden, sonst greift die Auto-Migration nicht.
