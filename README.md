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

## Manuelle Surfwellen-Daten einspielen

In `data/` eine zweite CSV `surfwelle_manual.csv` mit folgenden Spalten:

```
time,percent,temperature_c
2026-05-13T21:49:52+02:00,11.7,12
```

Die Rohdaten kannst du aus dem HTML-Code der Vereins-Website extrahieren —
sie stehen dort als JSON im `<script id="historical-swell-data">`-Tag.
Ein kleines Hilfsskript dazu kannst du dir bei Bedarf später bauen lassen.

## Daten anschauen

Alle Daten landen in `data/collected.csv`. Mit pandas analysierbar:

```python
import pandas as pd
df = pd.read_csv("data/collected.csv", parse_dates=["collected_at"])
print(df.tail())
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
- HND-Werte sind 15-Minuten-Werte; wir scrapen jedes Mal die Tabelle und
  bekommen damit immer den neuesten verfügbaren Wert. Identische
  Zeitstempel in Folge sind normal und kein Fehler.
- Open-Meteo aktualisiert die DWD-Beobachtungen einmal pro Stunde — also
  4 von 4 Läufen pro Stunde sehen denselben Niederschlagswert.
