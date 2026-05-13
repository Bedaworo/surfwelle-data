# Surfwelle Augsburg — Datensammler

Sammelt automatisch alle 15 Minuten Pegel- und Wetterdaten rund um die
Surfwelle am Senkelbach in Augsburg, um später eine Pegel-Prognose bauen
zu können.

## Was wird gesammelt

| Quelle | Daten | Intervall |
|---|---|---|
| HND Bayern | Pegel Türkheim / Wertach: Abfluss (m³/s) | 15 min |
| HND Bayern | Pegel Augsburg-Oberhausen / Wertach: Abfluss (m³/s) und Wasserstand (cm) | 15 min |
| Open-Meteo (DWD) | Niederschlag letzte Stunde in Kempten, Marktoberdorf, Augsburg | stündlich |
| Open-Meteo (DWD) | Temperatur in Kempten (Schneeschmelze) | stündlich |
| Open-Meteo (DWD) | Niederschlag-Vorhersage Kempten für nächste 6h und 24h | stündlich |

Manuell ergänzt werden:

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
| `collected.csv` | Bot, alle 15 Min | Türkheim, Oberhausen, Wetter |
| `surfwelle_manual.csv` | Mensch, alle 1-2 Wochen | Pegel der Surfwelle aus dem HTML |
| `events.csv` | Mensch, bei Bedarf | Bachablässe, Wehrsteuerungen, Bauarbeiten |

Die strikte Trennung verhindert, dass der Bot beim nächsten Commit
manuelle Änderungen überschreibt oder Git-Konflikte produziert. Bei der
späteren Analyse werden die Dateien einfach über die Zeitstempel
zusammengejoint.

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

Die Rohdaten stehen im HTML der Vereins-Website
([surfwelleaugsburg.de](https://surfwelleaugsburg.de)) als JSON-Block im
`<script id="historical-swell-data" type="application/json">`-Tag. Alle
paar Tage / Wochen:

1. HTML-Code der Pegelstand-Seite speichern (Browser → "Seitenquelltext
   anzeigen" → alles kopieren)
2. JSON-Block extrahieren und in CSV umwandeln — am einfachsten über
   Claude: "Hier ist der HTML-Code, mach mir eine CSV draus."
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
