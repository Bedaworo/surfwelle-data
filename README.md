# Surfwelle Augsburg — Datensammler

Sammelt automatisch alle 15 Minuten Pegel- und Wetterdaten rund um die
Surfwelle am Senkelbach in Augsburg, um eine Pegel-Prognose zu bauen.

## 🌊 Live-Prognose ansehen

**→ [bedaworo.github.io/surfwelle-data](https://bedaworo.github.io/surfwelle-data/)**

Zweistufiges Modell: Türkheim → geschätzter Fabrikkanal-Abfluss (Kennlinie aus
322 Tagen echter Kanaldaten) → Wellenhöhe. Die Laufzeit zwischen den Stationen
ist **abflussabhängig** — bei wenig Wasser fließt es langsamer — und folgt in
jedem Flussabschnitt demselben Potenzgesetz `T = T₁₀ · (Q/10)^-b`, gemessen an
sechs Kaskadenstufen von Sebastianskapelle bis zur Welle.

Das Modell **kalibriert sich laufend selbst**: aus dem Abgleich der letzten
Stunde, sechs Stunden und drei Tage mit den echten Wellenmessungen ergibt sich
ein Korrekturwert, der automatisch nachgeführt wird — keine feste Konstante.

Enthalten:
- **Rückblick/Ausblick** einzeln wählbar (1 h bis 72 h zurück, 1 h bis 24 h
  voraus), inklusive Anzeige der Schätzgüte (mittlere Abweichung, Trefferquote
  der Surfbar-Entscheidung) im jeweils sichtbaren Fenster
- **Zweiter Ausblick-Ast über Sebastianskapelle** an der jungen Wertach —
  reicht weiter voraus als Türkheim allein, ist aber relativ zum aktuellen
  Türkheim-Stand verankert (die Jahres-Kennlinie zwischen beiden Pegeln
  überschätzt sonst in Trockenphasen deutlich)
- **Grüntensee-Füllstand in Prozent** der Bewirtschaftungslamelle
  (875,50–876,50 m ü. NN) als Kontext, wie viel Puffer der Speicher noch hat
- **7-Tage-Regenaussicht** mit zwei Kacheln — *frühestens möglich* und
  *wahrscheinlichster Tag* — samt Tagesstreifen mit Regenmenge und
  Eintreffwahrscheinlichkeit; berücksichtigt, dass die Welle nur von 8 bis
  20 Uhr gesurft werden darf
- Sensor-Ausreißer-Filter für die angezeigte Wellenkurve (siehe
  `fetch_swell.py` unten — dieselbe Filterlogik läuft serverseitig)

Zieht sich die Daten aus `data/recent.csv` und `data/recent_swell.csv` in
diesem Repo (kleine, vom Workflow bei jedem Lauf neu geschriebene Ausschnitte
— die Volldateien sind inzwischen zu groß für Live-Abrufe im Browser).

## Was wird gesammelt

### Pegelkette Wertach (von der Quelle bis Augsburg)

| Quelle | Daten | Fluss-km | Bedeutung |
|---|---|---|---|
| HND Bayern | Sebastianskapelle / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~155 | Junge Wertach, oberhalb Grüntensee |
| HND Bayern | Grüntensee Seepegel — Wasserstand (m ü. NN) | ~130 | Pufferspeicher, Bewirtschaftungslamelle 875,50–876,50 m |
| HND Bayern | Haslach Werksabfluss / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~128 | Gesteuerte Kraftwerksausleitung direkt unterhalb Grüntensee |
| HND Bayern | Thalhofen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~95 | Zwischenstufe vor Biessenhofen |
| HND Bayern | Biessenhofen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~80 | Nach Speicher, vor Mittellauf |
| HND Bayern | Wertach (Ortspegel) — Abfluss (m³/s) + Wasserstand (cm) | ~55 | Mittellauf |
| HND Bayern | Türkheim / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~42 | Hauptsignal kurz vor Augsburg |
| HND Bayern | Augsburg-Oberhausen / Wertach — Abfluss (m³/s) + Wasserstand (cm) | ~3 | Mutterbett, nach Fabrikkanal-Abzweig |
| HND Bayern | Singold / Langerringen — Abfluss (m³/s) + Wasserstand (cm) | — | Mündet erst **hinter** der Fabrikkanal-Messung in den Senkelbach; für die Wasserbilanz zur Welle also nachrangig |

An allen Q+W-Stellen lässt sich pro Messstelle ein grober
Fließgeschwindigkeits-Proxy `v_relativ = Q / W` bilden — wegen des
unbekannten, nicht-linearen Bachbett-Querschnitts kein exakter physikalischer
Wert, sondern nur als **relativer Trend an derselben Messstelle** aussagekräftig.

### Fabrikkanal — manuell vom Kraftwerksbetreiber

Der Fabrikkanal führt das Wasser, das die Welle tatsächlich speist, und
erklärt die gemessene Wellenhöhe mit r ≈ 0,93 — deutlich besser als jeder
Wertach-Pegel allein. Es gibt dafür **keinen öffentlichen HND-Pegel**; die
Werte kommen als CSV-Export vom Betreiber und liegen unter
`data/fabrikkanal_2025.csv` und `data/fabrikkanal_2026.csv` (15-Minuten-Werte,
Ortszeit, Spalten `fabrikkanal_pegel_oberwasser_m` und `fabrikkanal_q_m3s`).

Da die Werte nicht live vorliegen, schätzt das Chart den Kanalabfluss aus
Türkheim über eine Kennlinie, die aus diesen historischen Daten gefittet ist
(`TUE2FAB` in `index.html`). Kommt der Betreiber irgendwann auf eine
automatisierbare Quelle, ließe sich daraus ein echter Nowcast mit deutlich
höherer Genauigkeit bauen als die aktuelle Schätzung.

### Wetter — aktuelle Beobachtungen (DWD-Daten via Open-Meteo)

| Station | Messwerte | Bedeutung |
|---|---|---|
| Oberjoch (1180m) | Niederschlag + Temperatur | Wertach-Quellgebiet, Schneeschmelze |
| Kaufbeuren | Niederschlag | Mittellauf, Zwischengebiet |
| Marktoberdorf | Niederschlag | Mittellauf |
| Kempten | Niederschlag + Temperatur | Allgäu-Großwetter (Iller-Tal, speist die Wertach nicht direkt) |
| Augsburg | Niederschlag | Lokaler Einfluss Senkelbach |

### Regen-Bodenmessungen (HND-Stationen, genauer als Open-Meteo-Grid)

Open-Meteo unterschätzt lokale Schauer im Wertach-EZG teils drastisch (belegt:
Faktor 16 bei einem Ereignis im Juli 2026); die HND-Regenmesser liefern
erheblich genauere Bodenwerte.

| Station | Bedeutung |
|---|---|
| Hindelang-Unterjoch | Wertach-Quellgebiet (~1015 m) |
| Buchloe | Gennach-EZG, südlich Türkheim |
| Schwabmünchen | Wertach-Tal zwischen Türkheim und Augsburg |

HND-Regenwerte kommen in **Zehntel-mm als Ganzzahl** und werden im Skript durch
10 geteilt (sonst entstünden unplausible Werte wie „74 mm in 5 Minuten").

### Regen-Vorhersage Einzugsgebiet — Basis für den 2–3-Tage-Ausblick (v1.5)

Für sieben Punkte entlang der Wertach wird die Niederschlags-Vorhersage
(nächste 6 h und 24 h) in **einer einzigen Multi-Location-Anfrage** geholt.
Jeder Punkt hat eine geschätzte Fließzeit „Regen → Welle in Augsburg"; das
Forecast-Chart gewichtet die Vorhersagen damit **zeitversetzt**.

| Punkt | Fließzeit Regen→Welle (geschätzt) |
|---|---|
| Oberjoch | ~30 h |
| Nesselwang | ~27 h |
| Marktoberdorf | ~22 h |
| Bad Wörishofen | ~16 h |
| Türkheim | ~13 h |
| Schwabmünchen | ~9 h |
| Bobingen | ~6 h |

Die Laufzeiten sind **nicht kalibriert**, sondern aus Flusslauf/Geografie
geschätzt (Dict `CATCHMENT` in `collect.py`). Spalten je Punkt:
`forecast_rain_<punkt>_6h_mm` und `forecast_rain_<punkt>_24h_mm`.

### Gebietsniederschlag 7 Tage — Basis für die "Wann surfbar?"-Kacheln (v1.11)

Für vier CATCHMENT-Punkte (Oberjoch 35 %, Bad Wörishofen 30 %, Marktoberdorf
20 %, Nesselwang 15 % — Schwerpunkt auf Ober-/Mittellauf, da Regen unterhalb
Türkheim kaum zur Welle beiträgt, r ≈ 0,1) wird die **Tagessumme für heute
plus sieben Tage** geholt und direkt serverseitig gebietsgewichtet.

Spalten: `forecast_area_rain_start_date` (Startdatum zur Verankerung) sowie
`forecast_area_rain_d0_mm` bis `forecast_area_rain_d7_mm`. Fällt einzelner
Punkt aus, normiert sich die Gewichtung auf die verbliebenen — ein Ausfall
macht die Summe ungenauer, nicht kleiner. Kompletter Ausfall der Anfrage lässt
die Spalten schlicht leer; der 2–3-Tage-Ausblick oben ist davon unabhängig.

### Bodenfeuchte als Zustandsvariable (v1.6)

Volumetrische Bodenfeuchte (m³/m³) aus dem ECMWF-IFS-Modell in vier Tiefen an
zwei Punkten (Oberjoch, Kaufbeuren). **Bislang nur Logging, kein Modell-
Einbau**: Ein Rückwärtstest über den Jahresdatensatz zeigte einen U-förmigen,
nicht monotonen Zusammenhang mit dem Abflussbeiwert (Minimum bei mittlerer
Feuchte), der sich mit den vorhandenen ~50 Ereignissen nicht sauber fitten
lässt. Bräuchte deutlich mehr Regenereignisse mit HND-Stationsdaten (statt
Open-Meteo-Grid) für eine belastbare Kalibrierung.

Alle 15 Minuten ein Lauf, ein Datenpunkt pro Lauf in `data/collected.csv`.

### Manuell ergänzt

- Fabrikkanal-Daten vom Kraftwerksbetreiber (siehe oben)
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

### 3. GitHub Pages für die Live-Prognose aktivieren

In GitHub: **Settings → Pages** → Branch `main`, Ordner `/ (root)` auswählen.
`index.html` liegt im Repo-Root, damit GitHub Pages sie automatisch ausliefert.

### 4. Erstlauf manuell testen

In GitHub: **Actions** → `Collect water data` → `Run workflow`.

Nach 1-2 Minuten sollte ein neuer Commit `data: ...` auftauchen und
`data/collected.csv` existieren.

### 5. Automatik läuft ab dann

Der Cron-Job läuft alle 15 Minuten von alleine. GitHub Actions ist auf
öffentlichen Repos kostenlos, auf privaten gibt's 2.000 Freiminuten/Monat
— ein Lauf braucht ~30 Sekunden, also ~25 Stunden/Monat, locker drin.

## Datenstruktur

Im Ordner `data/` liegen mehrere CSV-Dateien mit klarer Aufgabenteilung:

| Datei | Wer pflegt | Inhalt |
|---|---|---|
| `collected.csv` | Bot, alle 15 Min | Pegelkette, Stausee, Wetter, Regen-Forecast, Bodenfeuchte (83 Spalten) |
| `surfwelle_manual.csv` | Bot, alle 15 Min | Pegel der Surfwelle, automatisch von der öffentlichen Seite geholt |
| `temperature_manual.csv` | Bot, alle 15 Min | Wassertemperatur, Nebenprodukt desselben Abrufs |
| `recent.csv` | Bot, alle 15 Min | Letzte ~350 Zeilen von `collected.csv` — Ausschnitt fürs Live-Chart |
| `recent_swell.csv` | Bot, alle 15 Min | Letzte ~1100 Zeilen von `surfwelle_manual.csv` — Ausschnitt fürs Live-Chart |
| `fabrikkanal_2025.csv` / `fabrikkanal_2026.csv` | Mensch, unregelmäßig | Fabrikkanal-Abfluss vom Kraftwerksbetreiber |
| `events.csv` | Mensch, bei Bedarf | Bachablässe, Wehrsteuerungen, Bauarbeiten |

`recent.csv` und `recent_swell.csv` sind reine Ableitungen der Volldateien
(Kopfzeile + letzte N Zeilen) — gehen sie verloren, entstehen sie beim
nächsten Lauf von selbst neu. Grund für den Umweg: `collected.csv` ist
inzwischen über 17 MB groß; ein Live-Chart kann das nicht bei jedem
Seitenaufruf laden. HTTP-Range-Requests scheiden aus, weil
`raw.githubusercontent.com` CORS-Preflights ablehnt und `Content-Length` die
komprimierte statt der tatsächlichen Dateigröße meldet.

Die strikte Trennung zwischen Bot- und Mensch-Dateien verhindert, dass der Bot
beim nächsten Commit manuelle Änderungen überschreibt oder Git-Konflikte
produziert. Bei der Analyse werden die Dateien einfach über die Zeitstempel
zusammengejoint.

### Spalten in `collected.csv`

Die CSV ist über mehrere Skript-Versionen gewachsen (aktuell 83 Spalten,
Stand v1.11): Pegelkette und erstes Wetter (v1.x), Biessenhofen/Grüntensee/
Oberjoch (v1.2), Singold/Bobingen (v1.3), HND-Regenstationen (v1.4), die
Regen-Vorhersage je Einzugs-Punkt (v1.5), Bodenfeuchte (v1.6), Türkheim-
Wasserstand (v1.7), Haslach Werksabfluss Q+W (v1.8), drei weitere
Wertach-Pegel — Wertach, Sebastianskapelle, Thalhofen, je Q+W (v1.9),
robusteres HND-Parsing via lxml (v1.10) und der 7-Tage-Gebietsniederschlag
für die "Wann surfbar?"-Kacheln (v1.11). Das Skript migriert die CSV
automatisch beim ersten Lauf nach einem Update: neue Spalten werden hinten
angehängt, alte Zeilen bekommen leere Werte, niemand muss von Hand eingreifen.

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

### Fabrikkanal-Daten (`data/fabrikkanal_2025.csv`, `data/fabrikkanal_2026.csv`)

Kommen als CSV-Export vom Kraftwerksbetreiber, 15-Minuten-Werte in Ortszeit.
Format:

```csv
time,fabrikkanal_pegel_oberwasser_m,fabrikkanal_q_m3s
2026-01-01T00:00:00,486.747,14.94
2026-01-01T00:15:00,486.747,14.947
```

(`fabrikkanal_2026.csv` enthält nur die Abfluss-Spalte, kein Pegel-Oberwasser.)
Datei ersetzen bzw. ergänzen und committen — es gibt dafür keinen
automatisierten Abruf, siehe Abschnitt oben.

### Surfwellen-Pegel (`data/surfwelle_manual.csv`) — vollautomatisch

Format:

```csv
time,percent
2026-07-14T16:36:00+02:00,29.3
2026-07-14T16:41:00+02:00,29.5
```

`fetch_swell.py` läuft als Schritt in `collect.yml` bei jedem 15-Minuten-Zyklus
mit und ruft die **öffentliche, login-freie** Seite
`https://surfwelleaugsburg.de/swell?chart_hours=72` ab. Die Chart.js-Daten
(Swell + Wassertemperatur als Bonus) stecken direkt im server-gerenderten
HTML — kein Login, kein Browser, kein manueller Schritt nötig.

Zwei Dinge passieren beim Merge automatisch:

- **Zeitstempel-Normalisierung**: alle Zeitstempel werden auf das kanonische
  Format `YYYY-MM-DDTHH:MM:SS±HH:MM` (Ortszeit, volle Minute) gebracht.
  Historisch gewachsen enthielt die Datei zwei Formate (ältere manuelle
  Importe mit Offset und Mikrosekunden, automatische Abrufe ohne Offset);
  beide werden dabei vereinheitlicht und Dubletten aus derselben Messung
  fallen zusammen.
- **Sensor-Ausreißer-Filter**: erkennt Blöcke, in denen der Wert sprunghaft
  auf ein falsches Niveau springt und binnen einer Stunde genauso sprunghaft
  zurückkehrt (Schwelle 30 cm je Messschritt — das 99,9-Perzentil der
  normalen Schrittänderung liegt bei 21 cm). Prüft standardmäßig nur die
  letzten 14 Tage; `python fetch_swell.py --clean-all` geht einmalig über die
  gesamte Historie.

Für den Fall, dass die öffentliche Seite mal offline ist oder sich strukturell
ändert, gibt es weiterhin den manuellen Fallback-Weg über einen zweiten
Workflow (`Convert surfwelle HTML`): HTML-Datei der (ggf. login-geschützten)
Seite unter `buchung.surfwelleaugsburg.de/swell/` speichern (Strg+U →
Seitenquelltext kopieren, **nicht** mit einer "Seite speichern"-Erweiterung
wie SingleFile — die friert das Chart als Bild ein und die Rohdaten gehen
verloren) und in `data/incoming/` hochladen. `convert_surfwelle.py` erkennt
beide Seitenformate automatisch.

## Daten anschauen

Alle Daten landen in `data/*.csv`. Mit pandas analysierbar:

```python
import pandas as pd
collected = pd.read_csv("data/collected.csv", parse_dates=["collected_at"])
surfwelle = pd.read_csv("data/surfwelle_manual.csv", parse_dates=["time"])
events = pd.read_csv("data/events.csv", parse_dates=["time"])
fabrikkanal = pd.read_csv("data/fabrikkanal_2026.csv", parse_dates=["time"])
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

Ändert sich dabei etwas an den Kennlinien oder Spalten, die `index.html`
nutzt (z. B. `TUE2FAB`, `FAB2WAVE`, die Potenzgesetz-Konstanten der Laufzeit
oder die `forecast_area_rain_*`-Spalten), muss das Chart entsprechend
nachgezogen werden — es liest die Rohdaten direkt und rechnet die
Kalibrierung clientseitig, es gibt keine gemeinsame Konfigurationsdatei.
