# Surfwelle Augsburg — Datensammler

Sammelt automatisch alle 15 Minuten Pegel- und Wetterdaten rund um die
Surfwelle am Senkelbach in Augsburg, um eine Pegel-Prognose zu bauen.

## 🌊 Live-Prognose ansehen

**→ [bedaworo.github.io/surfwelle-data](https://bedaworo.github.io/surfwelle-data/)**

Interaktives Chart: aktuelle Welle, 8-Stunden-Abflussprognose (bias-korrigiert),
2–3-Tage-Regen-Ausblick über 7 Einzugs-Punkte, plus Skill-Auswertung (MAE,
Treffergenauigkeit) gegen die real gemessenen Werte. Zieht sich die Daten live
aus `data/collected.csv` und `data/surfwelle_manual.csv` in diesem Repo.

## Was wird gesammelt

### Pegelkette Wertach (von Quelle bis Augsburg)

| Quelle | Daten | Fluss-km | Bedeutung |
|---|---|---|---|
| HND Bayern | Grüntensee Seepegel — Wasserstand (m ü. NN) | ~130 | Pufferspeicher am Anfang |
| HND Bayern | Biessenhofen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~80 | Nach Speicher, vor Mittellauf |
| HND Bayern | Türkheim / Wertach — Abfluss (m³/s) | ~42 | Hauptsignal kurz vor Augsburg |
| HND Bayern | Augsburg-Oberhausen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~3 | Nach Wertachkanal-Abzweig |

### Wetter — aktuelle Beobachtungen (DWD-Daten via Open-Meteo)

| Station | Messwerte | Bedeutung |
|---|---|---|
| Oberjoch (1180m) | Niederschlag + Temperatur | Wertach-Quellgebiet, Schneeschmelze |
| Kaufbeuren | Niederschlag | Mittellauf, Zwischengebiet |
| Marktoberdorf | Niederschlag | Mittellauf |
| Kempten | Niederschlag + Temperatur | Allgäu-Großwetter (Iller-Tal) |
| Augsburg | Niederschlag | Lokaler Einfluss Senkelbach |

### Regen-Bodenmessungen (HND-Stationen, genauer als Open-Meteo-Grid)

Open-Meteo unterschätzt lokale Schauer im Wertach-EZG deutlich; die HND-Regenmesser
liefern erheblich genauere Bodenwerte.

| Station | Bedeutung |
|---|---|
| Hindelang-Unterjoch | Wertach-Quellgebiet (~1015 m) |
| Buchloe | Gennach-EZG, südlich Türkheim |
| Schwabmünchen | Wertach-Tal zwischen Türkheim und Augsburg |

HND-Regenwerte kommen in **Zehntel-mm als Ganzzahl** und werden im Skript durch 10
geteilt (sonst entstünden unplausible Werte wie „74 mm in 5 Minuten").

### Regen-Vorhersage Einzugsgebiet (v1.5) — Basis für den 2–3-Tage-Ausblick

Für sieben Punkte entlang der Wertach wird die Niederschlags-Vorhersage (nächste
6 h und 24 h) in **einer einzigen Multi-Location-Anfrage** geholt. Jeder Punkt hat
eine geschätzte Fließzeit „Regen → Welle in Augsburg"; das Forecast-Chart gewichtet
die Vorhersagen damit **zeitversetzt** — ein Regenpeak in Oberjoch schlägt später
auf die Welle als einer in Bobingen.

| Punkt | Fließzeit Regen→Welle (geschätzt) |
|---|---|
| Oberjoch | ~30 h |
| Nesselwang | ~27 h |
| Marktoberdorf | ~22 h |
| Bad Wörishofen | ~16 h |
| Türkheim | ~13 h |
| Schwabmünchen | ~9 h |
| Bobingen | ~6 h |

Die Laufzeiten sind **nicht kalibriert**, sondern aus Flusslauf/Geografie geschätzt
(Definition in `collect.py`, Dict `CATCHMENT`). Sie werden nachjustiert, sobald echte
Regenereignisse mit der Wellen-Reaktion abgeglichen sind. Spalten je Punkt:
`forecast_rain_<punkt>_6h_mm` und `forecast_rain_<punkt>_24h_mm`.

Kempten ist als Forecast-Quelle **raus** (liegt im Iller-EZG, speist die Wertach
nicht); die alten `forecast_rain_kempten_*` Spalten bleiben aus Kontinuitätsgründen
erhalten, werden aber nicht mehr befüllt.

### Bodenfeuchte als Zustandsvariable (v1.6)

Wie schnell und wie stark Regen im Fluss ankommt, hängt stark vom Sättigungs­zustand
des Gebiets ab (trockener Boden schluckt, nasser leitet fast 1:1 weiter). Als Proxy
dafür wird die volumetrische Bodenfeuchte (m³/m³) aus dem ECMWF-IFS-Modell in vier
Tiefen an zwei repräsentativen Punkten geloggt:

| Punkt | Rolle |
|---|---|
| Oberjoch | alpines Quellgebiet, Hauptabflussbildung |
| Kaufbeuren | Mittellauf / Tallage |

Tiefen: `0_to_7cm` (Infiltrationskapazität), `7_to_28cm`, `28_to_100cm` und
`100_to_255cm` (die tiefen Schichten als träger Grundwasser-/Sättigungs-Proxy).
Spalten: `soil_moist_<punkt>_<tiefe>` plus ein gemeinsamer `soil_moisture_time`.

**Dies ist Schritt 0 einer schrittweisen Prognose-Roadmap: vorerst nur Logging,
noch kein Modell-Einbau.** Ob die Bodenfeuchte den variablen Abflussbeiwert
tatsächlich erklärt, wird nach ein paar Regenereignissen ausgewertet — erst dann
wird entschieden, ob sie ins Prognosemodell aufgenommen wird.

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
| `collected.csv` | Bot, alle 15 Min | Pegel, Stausee, Wetter, Regen-Forecast, Bodenfeuchte (56 Spalten) |
| `surfwelle_manual.csv` | Mensch, alle 1-2 Wochen | Pegel der Surfwelle aus dem HTML |
| `events.csv` | Mensch, bei Bedarf | Bachablässe, Wehrsteuerungen, Bauarbeiten |

Die strikte Trennung verhindert, dass der Bot beim nächsten Commit
manuelle Änderungen überschreibt oder Git-Konflikte produziert. Bei der
späteren Analyse werden die Dateien einfach über die Zeitstempel
zusammengejoint.

### Spalten in `collected.csv`

Die CSV ist über mehrere Skript-Versionen gewachsen (aktuell 56 Spalten,
Stand v1.6): Pegelkette und erstes Wetter (v1.x), Biessenhofen/Grüntensee/
Oberjoch (v1.2), Singold/Bobingen (v1.3), HND-Regenstationen (v1.4), die
Regen-Vorhersage je Einzugs-Punkt (v1.5) und Bodenfeuchte (v1.6). Das Skript migriert die CSV
automatisch beim ersten Lauf nach einem Update: neue Spalten werden hinten
angehängt, alte Zeilen bekommen leere Werte, niemand muss von Hand eingreifen
(siehe `_migrate_csv_header` in `collect.py`).

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

**Automatisierter Ablauf (seit v1.5):** Alle paar Tage / Wochen:

1. Pegelstand-Seite (`/swell/`) im Browser öffnen und als HTML speichern
   (Strg+U → Seitenquelltext kopieren → als `.html` speichern)
2. Die HTML-Datei in den Ordner `data/incoming/` auf GitHub hochladen und
   committen
3. Fertig — ein GitHub-Actions-Workflow (`Convert surfwelle HTML`)
   erkennt den Upload automatisch, wandelt die Datei in CSV-Zeilen um,
   merged sie in `data/surfwelle_manual.csv` (alte Daten bleiben erhalten,
   Zeitstempel-Dedup) und löscht die HTML-Datei danach selbst wieder

Kein lokales Python-Skript und kein manueller CSV-Upload mehr nötig.
`convert_surfwelle.py` und `Surfwelle_aktualisieren.bat` bleiben im Repo
als Fallback, falls der Workflow mal nicht verfügbar ist (z.B. GitHub-Ausfall)
und man lokal konvertieren möchte.

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
