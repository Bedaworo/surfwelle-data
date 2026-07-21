"""
Sammelt alle 15 Minuten Daten zu:
- HND Pegel Türkheim (Wertach) — Abfluss
- HND Pegel Biessenhofen (Wertach) — Abfluss + Wasserstand
- HND Pegel Augsburg-Oberhausen (Wertach) — Wasserstand und Abfluss
- HND Grüntensee Seepegel — Wasserstand
- HND Singold-Pegel Langerringen — Abfluss + Wasserstand [v1.3]
- HND Regenstationen Hindelang-Unterjoch, Buchloe, Schwabmünchen [v1.4]
- Open-Meteo (DWD-basiert): Niederschlag in Oberjoch, Kaufbeuren, Kempten,
  Marktoberdorf, Augsburg, Bobingen
- Open-Meteo: Temperatur in Kempten + Oberjoch (Schneeschmelze)
- Open-Meteo: Niederschlags-Vorhersage je Einzugs-Punkt [v1.5]
  (Oberjoch, Nesselwang, Marktoberdorf, Bad Wörishofen, Türkheim,
  Schwabmünchen, Bobingen) — Basis für den laufzeitgewichteten
  2-3-Tage-Ausblick im Forecast-Chart. Kempten ist raus (Iller-EZG).
- Open-Meteo: Bodenfeuchte in vier Tiefen an zwei Punkten [v1.6]
  (Oberjoch, Kaufbeuren) — Zustandsvariable für den Abflussbeiwert.
  Schritt 0 der Prognose-Roadmap: vorerst nur Logging.
- HND Pegel Türkheim (Wertach) — zusätzlich Wasserstand [v1.7]
  Bisher wurde an der wichtigsten Messstelle (Kombi-Regel >7 m³/s) nur Q
  erfasst. Zusammen mit W lässt sich v_relativ = Q/W bilden, um zu prüfen,
  ob die Fließgeschwindigkeit die Laufzeit bis zur Welle beeinflusst
  (analog bereits vorhanden bei Biessenhofen, Oberhausen, Singold).
- HND Pegel Haslach Werksabfluss (Wertach) — Abfluss + Wasserstand [v1.8]
  Gesteuerter Kraftwerksausleitung-Abfluss direkt unterhalb des Grüntensees.
  Ergänzt den reinen Seepegel um einen echten Q-Wert an derselben Stelle.

Jeder Lauf hängt eine Zeile an data/collected.csv an.

Defensiv: Wenn eine Quelle ausfällt, wird für deren Spalten None geschrieben,
aber die Zeile wird trotzdem gespeichert. Fehler werden klar geloggt.

Alle bisherigen Spalten bleiben unverändert für Daten-Kontinuität.
Neue Spalten kommen ans Ende und werden per Auto-Migration angefügt.
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
USER_AGENT = "surfwelle-augsburg-data-collector/1.9 (research project)"

# HND-Pegel und Stauseen
HND_TUERKHEIM_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/tuerkheim-12406008/tabelle"
    "?methode=abfluss&setdiskr=15"
)
# NEU v1.7: Türkheim Wasserstand — bisher fehlte hier W, obwohl Türkheim die
# wichtigste Messstelle für die Kombi-Regel (>7 m³/s) ist. Zusammen mit Q lässt
# sich daraus v_relativ = Q/W bilden, um zu testen ob die Fließgeschwindigkeit
# einen Einfluss auf die Laufzeit "Wasser an der Welle" hat (an allen Stellen
# getrackt, an denen W verfügbar ist: Türkheim, Biessenhofen, Oberhausen, Singold).
HND_TUERKHEIM_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/tuerkheim-12406008/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
HND_OBERHAUSEN_Q_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/augsburg-oberhausen-12407000/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_OBERHAUSEN_W_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/augsburg-oberhausen-12407000/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
# NEU: Biessenhofen — sitzt ~50 km nach Grüntensee, ~38 km vor Türkheim
HND_BIESSENHOFEN_Q_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/biessenhofen-12405005/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_BIESSENHOFEN_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/biessenhofen-12405005/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
# NEU: Grüntensee Seepegel — der große Stausee am Anfang der Wertach
HND_GRUENTENSEE_URL = (
    "https://www.hnd.bayern.de/speicher/iller_lech/gruentensee-seepegel-12403000/tabelle"
    "?methode=seewasserstand&setdiskr=15"
)
# NEU v1.8: Haslach Werksabfluss — der gesteuerte Abfluss direkt unterhalb des
# Grüntensees (Kraftwerksausleitung). Ergänzt den reinen Seepegel um einen
# echten Q-Wert an derselben Stelle, sodass sich auch hier v_relativ = Q/W
# bilden lässt (auf Vorschlag des Nutzers, analog zu Türkheim/Biessenhofen).
HND_HASLACH_Q_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/haslach-werksabfluss-12404002/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_HASLACH_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/haslach-werksabfluss-12404002/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
# NEU v1.9: drei weitere Wertach-Pegel, um die Fließwellen-Kette lückenlos zu
# machen (für detect_flood_waves.py). Wertach + Sebastianskapelle liegen OBERHALB
# des Grüntensees (scharfe, ungepufferte Welle), Thalhofen unterhalb. Damit wird
# die Peak-Verkettung engmaschig genug, dass Fehlzuordnungen wegfallen.
HND_WERTACH_Q_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/wertach-12401004/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_WERTACH_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/wertach-12401004/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
HND_SEBASTIANSKAPELLE_Q_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/sebastianskapelle-12402007/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_SEBASTIANSKAPELLE_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/sebastianskapelle-12402007/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
HND_THALHOFEN_Q_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/thalhofen-12404705/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_THALHOFEN_W_URL = (
    "https://www.hnd.bayern.de/pegel/iller_lech/thalhofen-12404705/tabelle"
    "?methode=wasserstand&setdiskr=15"
)
# NEU v1.3: Singold (Pegel Langerringen) — laut Wikipedia mündet die Singold
# über den ausgeleiteten Kanal "Senkelbach" in Göggingen in die Wertach.
# Das heißt: Singold-Wasser landet möglicherweise direkt am Surfwellen-Bach,
# ohne Umweg über das Ackermannwehr. EZG 101 km², MQ 2.5 m³/s.
HND_SINGOLD_Q_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/langerringen-12483009/tabelle"
    "?methode=abfluss&setdiskr=15"
)
HND_SINGOLD_W_URL = (
    "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/langerringen-12483009/tabelle"
    "?methode=wasserstand&setdiskr=15"
)

# NEU v1.4: HND-Niederschlagsstationen (DWD-Regenmesser, 5-Min-Auflösung).
# Open-Meteo unterschätzt lokale Schauer im Wertach-EZG dramatisch — HND-Bodendaten
# sind erheblich genauer. Wir wählen drei Stationen die alle südlich Augsburgs
# liegen und das gesamte Wertach-Einzugsgebiet abdecken:
# - Hindelang-Unterjoch: Wertach-Quellgebiet (~1015m)
# - Buchloe: Gennach-EZG, direkt südlich Türkheim
# - Schwabmünchen: Wertach-Tal zwischen Türkheim und Augsburg
# Die Werte sind Niederschlagssummen in mm zum jeweiligen Zeitstempel.
HND_RAIN_HINDELANG_URL = (
    "https://www.hnd.bayern.de/niederschlag/iller_lech/"
    "hindelang-unterjoch-untergschwend-2222/tabelle"
)
HND_RAIN_BUCHLOE_URL = (
    "https://www.hnd.bayern.de/niederschlag/iller_lech/buchloe-2387/tabelle"
)
HND_RAIN_SCHWABMUENCHEN_URL = (
    "https://www.hnd.bayern.de/niederschlag/iller_lech/schwabmuenchen-4579/tabelle"
)

# Wetterstationen (Lat/Lon)
# Bestehende:
#   Kempten: war im Iller-EZG (falsche Region), bleibt für Kontinuität
#   Marktoberdorf: Mittellauf-Region, bleibt
#   Augsburg: Lokaleinfluss Senkelbach, bleibt
# Neue:
#   Oberjoch: 1180m, direkt am Wertach-Quellgebiet
#   Kaufbeuren: Mittellauf, kurz vor Türkheim
LOCATIONS = {
    "kempten":       (47.7333, 10.3167),
    "marktoberdorf": (47.7800, 10.6167),
    "augsburg":      (48.3667, 10.8833),
    "oberjoch":      (47.5159, 10.4058),  # NEU v1.2
    "kaufbeuren":    (47.8812, 10.6246),  # NEU v1.2
    "bobingen":      (48.2700, 10.8300),  # NEU v1.3: Singold/Wertach-Mündungsbereich
}

# NEU v1.5: Einzugsgebiets-Punkte für den Regen-VORHERSAGE-Ausblick (2-3 Tage).
# Reihenfolge = flussaufwärts -> flussabwärts. Der dritte Wert ist die GESCHÄTZTE
# Fließzeit "Regen an diesem Punkt -> Welle in Augsburg" in Stunden.
#
# WICHTIG: Diese Laufzeiten sind NICHT kalibriert, sondern grob aus Flusslauf und
# Geografie geschätzt. Sie definieren, wie stark ein Regenpeak zeitversetzt in den
# Ausblick eingeht. Sobald echte Regenereignisse durchgelaufen sind, können sie
# gegen die tatsächliche Wellen-Reaktion nachjustiert werden. Das Forecast-Chart
# nutzt exakt dieselben Punkte und Laufzeiten (dort live gezogen), deshalb hier
# als eine Quelle der Wahrheit mitgepflegt und geloggt.
#
# Kempten fehlt bewusst: liegt im Iller-Einzugsgebiet, speist die Wertach nicht.
CATCHMENT = {
    # name:            (lat,      lon,     laufzeit_h)
    "oberjoch":        (47.5159, 10.4058, 30),
    "nesselwang":      (47.6197, 10.5006, 27),
    "marktoberdorf":   (47.7800, 10.6167, 22),
    "bad_woerishofen": (48.0058, 10.5969, 16),
    "tuerkheim":       (48.0619, 10.6386, 13),
    "schwabmuenchen":  (48.1786, 10.7594,  9),
    "bobingen":        (48.2700, 10.8300,  6),
}

# NEU v1.6 (Schritt 0 der Prognose-Roadmap): Bodenfeuchte als Zustandsvariable.
# Open-Meteo liefert volumetrische Bodenfeuchte (m³/m³) in vier Tiefen aus dem
# ECMWF-IFS-Modell. Die tiefen Schichten (28-100, 100-255 cm) sind ein träger
# Grundwasser-/Sättigungs-Proxy, die obere (0-7 cm) die momentane Infiltrations-
# kapazität. Wir loggen an zwei repräsentativen Punkten (Hauptabflussbildung oben,
# Mittellauf), NICHT an allen sieben — Bodenfeuchte ist eine langsam variierende
# Gebietsgröße, ein Gradient oben/mitte reicht. Vorerst nur Logging; der Einbau
# ins Modell erfolgt erst, wenn die Analyse zeigt dass es den Abflussbeiwert erklärt.
SOIL_POINTS = {
    "oberjoch":   (47.5159, 10.4058),  # alpines Quellgebiet
    "kaufbeuren": (47.8812, 10.6246),  # Mittellauf/Tallage
}
SOIL_LAYERS = ["0_to_7cm", "7_to_28cm", "28_to_100cm", "100_to_255cm"]


# -----------------------------------------------------------------------------
# Datenmodell
# -----------------------------------------------------------------------------


@dataclass
class Sample:
    """
    Reihenfolge der Felder = Reihenfolge der CSV-Spalten.
    NEUE Felder bitte am Ende anhängen, um Kompatibilität mit
    bestehenden CSVs zu wahren.
    """
    collected_at: str = ""

    # Bestehende HND-Felder
    tuerkheim_q_m3s: Optional[float] = None
    tuerkheim_time: Optional[str] = None
    oberhausen_q_m3s: Optional[float] = None
    oberhausen_q_time: Optional[str] = None
    oberhausen_w_cm: Optional[float] = None
    oberhausen_w_time: Optional[str] = None

    # Bestehende Wetter-Felder
    rain_kempten_mm: Optional[float] = None
    rain_marktoberdorf_mm: Optional[float] = None
    rain_augsburg_mm: Optional[float] = None
    temp_kempten_c: Optional[float] = None

    forecast_rain_kempten_24h_mm: Optional[float] = None
    forecast_rain_kempten_6h_mm: Optional[float] = None

    # NEUE Felder ab v1.2
    biessenhofen_q_m3s: Optional[float] = None
    biessenhofen_q_time: Optional[str] = None
    biessenhofen_w_cm: Optional[float] = None
    biessenhofen_w_time: Optional[str] = None

    gruentensee_w_mnn: Optional[float] = None  # Meter über NN, NICHT cm
    gruentensee_w_time: Optional[str] = None

    rain_oberjoch_mm: Optional[float] = None
    rain_kaufbeuren_mm: Optional[float] = None
    temp_oberjoch_c: Optional[float] = None

    forecast_rain_oberjoch_24h_mm: Optional[float] = None
    forecast_rain_oberjoch_6h_mm: Optional[float] = None

    # NEUE Felder ab v1.3 — Singold ist Hauptzufluss des Senkelbachs (laut Wikipedia)
    singold_q_m3s: Optional[float] = None
    singold_q_time: Optional[str] = None
    singold_w_cm: Optional[float] = None
    singold_w_time: Optional[str] = None

    rain_bobingen_mm: Optional[float] = None

    # NEUE Felder ab v1.4 — HND-Regenmessungen (direkte DWD-Bodenmessungen)
    # Diese Werte sind Niederschlagssummen zum Zeitstempel; das genaue
    # Aggregations-Intervall hängt von der Station ab (typisch 5 Min).
    hnd_rain_hindelang_mm: Optional[float] = None
    hnd_rain_hindelang_time: Optional[str] = None
    hnd_rain_buchloe_mm: Optional[float] = None
    hnd_rain_buchloe_time: Optional[str] = None
    hnd_rain_schwabmuenchen_mm: Optional[float] = None
    hnd_rain_schwabmuenchen_time: Optional[str] = None

    # NEUE Felder ab v1.5 — Regen-VORHERSAGE je Einzugs-Punkt (Open-Meteo).
    # Speisen den laufzeitgewichteten 2-3-Tage-Ausblick im Forecast-Chart.
    # Fließzeit je Punkt siehe CATCHMENT oben. Hinweis: oberjoch nutzt weiter
    # die bestehenden forecast_rain_oberjoch_* Spalten (ab v1.2), taucht hier
    # also NICHT nochmal auf, um Doppelspalten zu vermeiden.
    forecast_rain_nesselwang_6h_mm: Optional[float] = None
    forecast_rain_nesselwang_24h_mm: Optional[float] = None
    forecast_rain_marktoberdorf_6h_mm: Optional[float] = None
    forecast_rain_marktoberdorf_24h_mm: Optional[float] = None
    forecast_rain_bad_woerishofen_6h_mm: Optional[float] = None
    forecast_rain_bad_woerishofen_24h_mm: Optional[float] = None
    forecast_rain_tuerkheim_6h_mm: Optional[float] = None
    forecast_rain_tuerkheim_24h_mm: Optional[float] = None
    forecast_rain_schwabmuenchen_6h_mm: Optional[float] = None
    forecast_rain_schwabmuenchen_24h_mm: Optional[float] = None
    forecast_rain_bobingen_6h_mm: Optional[float] = None
    forecast_rain_bobingen_24h_mm: Optional[float] = None

    # NEUE Felder ab v1.6 — Bodenfeuchte (m³/m³, volumetrisch) als Zustandsvariable.
    # Schritt 0 der Prognose-Roadmap: nur Logging, noch kein Modell-Einbau.
    # Zwei Punkte × vier Tiefen + ein gemeinsamer Zeitstempel (gleiches Stundenraster).
    soil_moist_oberjoch_0_to_7cm: Optional[float] = None
    soil_moist_oberjoch_7_to_28cm: Optional[float] = None
    soil_moist_oberjoch_28_to_100cm: Optional[float] = None
    soil_moist_oberjoch_100_to_255cm: Optional[float] = None
    soil_moist_kaufbeuren_0_to_7cm: Optional[float] = None
    soil_moist_kaufbeuren_7_to_28cm: Optional[float] = None
    soil_moist_kaufbeuren_28_to_100cm: Optional[float] = None
    soil_moist_kaufbeuren_100_to_255cm: Optional[float] = None
    soil_moisture_time: Optional[str] = None

    # NEUE Felder ab v1.7 — Türkheim Wasserstand (siehe HND_TUERKHEIM_W_URL oben)
    tuerkheim_w_cm: Optional[float] = None
    tuerkheim_w_time: Optional[str] = None

    # NEUE Felder ab v1.8 — Haslach Werksabfluss (siehe HND_HASLACH_*_URL oben)
    haslach_q_m3s: Optional[float] = None
    haslach_q_time: Optional[str] = None
    haslach_w_cm: Optional[float] = None
    haslach_w_time: Optional[str] = None

    # NEUE Felder ab v1.9 — drei weitere Wertach-Pegel (Q+W)
    wertach_q_m3s: Optional[float] = None
    wertach_q_time: Optional[str] = None
    wertach_w_cm: Optional[float] = None
    wertach_w_time: Optional[str] = None
    sebastianskapelle_q_m3s: Optional[float] = None
    sebastianskapelle_q_time: Optional[str] = None
    sebastianskapelle_w_cm: Optional[float] = None
    sebastianskapelle_w_time: Optional[str] = None
    thalhofen_q_m3s: Optional[float] = None
    thalhofen_q_time: Optional[str] = None
    thalhofen_w_cm: Optional[float] = None
    thalhofen_w_time: Optional[str] = None


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

    timestamp_str = str(df.iloc[0, 0]).strip()
    raw_value = df.iloc[0, 1]

    try:
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


def fetch_catchment_forecast() -> dict:
    """
    Holt die Niederschlags-Vorhersage für ALLE CATCHMENT-Punkte in EINER
    Multi-Location-Anfrage: Open-Meteo akzeptiert kommaseparierte latitude/
    longitude und liefert dann ein JSON-Array in derselben Reihenfolge zurück.
    Das spart 6 zusätzliche HTTP-Requests pro Lauf gegenüber Einzelabfragen.

    Rückgabe: {name: {"next_6h": mm, "next_24h": mm}}.
    Kompletter Ausfall -> leeres Dict; einzelne fehlende Punkte fehlen im Dict.
    """
    names = list(CATCHMENT.keys())
    lats = ",".join(str(CATCHMENT[n][0]) for n in names)
    lons = ",".join(str(CATCHMENT[n][1]) for n in names)
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "precipitation",
        "timezone": "Europe/Berlin",
        "forecast_days": 2,
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo Einzugsgebiets-Forecast fehlgeschlagen: %s", e)
        return {}

    # Multi-Location -> Liste; bei nur einem Punkt gäbe Open-Meteo ein Dict
    # zurück (defensiv beides zulassen).
    locs = data if isinstance(data, list) else [data]
    now = datetime.now()
    current_hour_str = now.strftime("%Y-%m-%dT%H:00")

    out: dict = {}
    for name, loc in zip(names, locs):
        hourly = loc.get("hourly", {})
        precip = hourly.get("precipitation", []) or []
        times = hourly.get("time", []) or []
        if not precip or not times:
            log.warning("Open-Meteo Forecast %s — keine Stundenwerte", name)
            continue
        try:
            idx = times.index(current_hour_str)
        except ValueError:
            idx = 0
        next_6h = sum(v for v in precip[idx:idx + 6] if v is not None)
        next_24h = sum(v for v in precip[idx:idx + 24] if v is not None)
        out[name] = {"next_6h": next_6h, "next_24h": next_24h}
        log.info(
            "Open-Meteo Forecast %s ✓ (Laufzeit ~%dh) 6h=%.2fmm, 24h=%.2fmm",
            name, CATCHMENT[name][2], next_6h, next_24h,
        )
    return out


def fetch_soil_moisture() -> dict:
    """
    Holt die volumetrische Bodenfeuchte (m³/m³) für alle SOIL_POINTS in EINER
    Multi-Location-Anfrage, jeweils den Wert zur aktuellen Stunde.

    Rückgabe: {name: {layer: wert, ..., "time": iso}}.
    Ausfall -> leeres Dict; einzelne Punkte ohne Werte fehlen im Dict.
    """
    names = list(SOIL_POINTS.keys())
    lats = ",".join(str(SOIL_POINTS[n][0]) for n in names)
    lons = ",".join(str(SOIL_POINTS[n][1]) for n in names)
    variables = ",".join(f"soil_moisture_{layer}" for layer in SOIL_LAYERS)
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": variables,
        "timezone": "Europe/Berlin",
        "forecast_days": 1,
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Open-Meteo Bodenfeuchte fehlgeschlagen: %s", e)
        return {}

    locs = data if isinstance(data, list) else [data]
    now = datetime.now()
    current_hour_str = now.strftime("%Y-%m-%dT%H:00")

    out: dict = {}
    for name, loc in zip(names, locs):
        hourly = loc.get("hourly", {})
        times = hourly.get("time", []) or []
        if not times:
            log.warning("Open-Meteo Bodenfeuchte %s — keine Stundenwerte", name)
            continue
        try:
            idx = times.index(current_hour_str)
        except ValueError:
            idx = 0
        entry = {"time": times[idx]}
        for layer in SOIL_LAYERS:
            series = hourly.get(f"soil_moisture_{layer}", []) or []
            entry[layer] = series[idx] if idx < len(series) else None
        out[name] = entry
        log.info(
            "Open-Meteo Bodenfeuchte %s ✓ 0-7cm=%s, 28-100cm=%s (m³/m³)",
            name, entry.get("0_to_7cm"), entry.get("28_to_100cm"),
        )
    return out


# -----------------------------------------------------------------------------
# CSV-Append
# -----------------------------------------------------------------------------


def append_sample(sample: Sample) -> None:
    """
    Hängt einen Datenpunkt an die CSV an.
    Header wird nur geschrieben wenn Datei neu ist; sonst bleibt der
    bestehende Header und neue Spalten werden einfach unten leer
    angefügt (verträgt sich mit pandas-Einlesen, bestehender Header
    bleibt bis zur manuellen CSV-Migration unverändert).
    """
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()

    row = asdict(sample)
    fieldnames = list(row.keys())

    if not new_file:
        # Bestehende CSV: prüfen ob neue Spalten dazugekommen sind
        with open(CSV_PATH, encoding="utf-8") as f:
            existing_header = f.readline().strip().split(",")
        if existing_header != fieldnames:
            log.info("Neue Spalten erkannt — migriere CSV-Header")
            _migrate_csv_header(fieldnames, existing_header)

    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    log.info("Geschrieben in %s", CSV_PATH)


def _migrate_csv_header(new_fields: list[str], old_fields: list[str]) -> None:
    """
    Wenn neue Felder dazukommen, wird die CSV einmalig umgeschrieben:
    Header bekommt die neuen Spalten angehängt, alte Zeilen bekommen
    leere Werte für die neuen Spalten. Nur additiv — bestehende Spalten
    werden nie entfernt oder umsortiert.
    """
    # Prüfe: sind die alten Felder alle Präfix der neuen?
    if old_fields != new_fields[:len(old_fields)]:
        log.warning(
            "CSV-Header weicht ab — neue Felder werden NICHT automatisch migriert. "
            "Alte Header: %s, neue: %s",
            old_fields, new_fields,
        )
        return

    added_fields = new_fields[len(old_fields):]
    log.info("Füge %d neue Spalten an: %s", len(added_fields), added_fields)

    # Datei einlesen und mit erweitertem Header neu schreiben
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=new_fields)
        writer.writeheader()
        for row in rows:
            # Alte Zeilen: neue Felder bleiben leer
            for field in added_fields:
                row[field] = ""
            writer.writerow(row)


# -----------------------------------------------------------------------------
# Hauptablauf
# -----------------------------------------------------------------------------


def collect() -> Sample:
    sample = Sample(collected_at=datetime.now(timezone.utc).isoformat())

    # Bestehende HND-Pegel
    if r := fetch_hnd(HND_TUERKHEIM_URL, "Türkheim Q"):
        sample.tuerkheim_time, sample.tuerkheim_q_m3s = r
    if r := fetch_hnd(HND_TUERKHEIM_W_URL, "Türkheim W"):
        sample.tuerkheim_w_time, sample.tuerkheim_w_cm = r
    if r := fetch_hnd(HND_OBERHAUSEN_Q_URL, "Oberhausen Q"):
        sample.oberhausen_q_time, sample.oberhausen_q_m3s = r
    if r := fetch_hnd(HND_OBERHAUSEN_W_URL, "Oberhausen W"):
        sample.oberhausen_w_time, sample.oberhausen_w_cm = r

    # NEU: Biessenhofen
    if r := fetch_hnd(HND_BIESSENHOFEN_Q_URL, "Biessenhofen Q"):
        sample.biessenhofen_q_time, sample.biessenhofen_q_m3s = r
    if r := fetch_hnd(HND_BIESSENHOFEN_W_URL, "Biessenhofen W"):
        sample.biessenhofen_w_time, sample.biessenhofen_w_cm = r

    # NEU: Grüntensee Seepegel
    if r := fetch_hnd(HND_GRUENTENSEE_URL, "Grüntensee W"):
        sample.gruentensee_w_time, sample.gruentensee_w_mnn = r

    # NEU v1.8: Haslach Werksabfluss (gesteuerter Abfluss unterhalb Grüntensee)
    if r := fetch_hnd(HND_HASLACH_Q_URL, "Haslach Q"):
        sample.haslach_q_time, sample.haslach_q_m3s = r
    if r := fetch_hnd(HND_HASLACH_W_URL, "Haslach W"):
        sample.haslach_w_time, sample.haslach_w_cm = r

    # NEU v1.9: drei weitere Wertach-Pegel (Fließwellen-Kette vervollständigen)
    if r := fetch_hnd(HND_WERTACH_Q_URL, "Wertach Q"):
        sample.wertach_q_time, sample.wertach_q_m3s = r
    if r := fetch_hnd(HND_WERTACH_W_URL, "Wertach W"):
        sample.wertach_w_time, sample.wertach_w_cm = r
    if r := fetch_hnd(HND_SEBASTIANSKAPELLE_Q_URL, "Sebastianskapelle Q"):
        sample.sebastianskapelle_q_time, sample.sebastianskapelle_q_m3s = r
    if r := fetch_hnd(HND_SEBASTIANSKAPELLE_W_URL, "Sebastianskapelle W"):
        sample.sebastianskapelle_w_time, sample.sebastianskapelle_w_cm = r
    if r := fetch_hnd(HND_THALHOFEN_Q_URL, "Thalhofen Q"):
        sample.thalhofen_q_time, sample.thalhofen_q_m3s = r
    if r := fetch_hnd(HND_THALHOFEN_W_URL, "Thalhofen W"):
        sample.thalhofen_w_time, sample.thalhofen_w_cm = r

    # NEU v1.3: Singold (Pegel Langerringen) — wichtiger Direkt-Zubringer
    # zum Senkelbach via "Singold-Senkelbach"-Überleitung
    if r := fetch_hnd(HND_SINGOLD_Q_URL, "Singold Q"):
        sample.singold_q_time, sample.singold_q_m3s = r
    if r := fetch_hnd(HND_SINGOLD_W_URL, "Singold W"):
        sample.singold_w_time, sample.singold_w_cm = r

    # NEU v1.4: HND-Regenstationen (direkte DWD-Bodenmessungen)
    # Diese sind erheblich genauer als Open-Meteo-Gridwerte für lokale Schauer.
    #
    # WICHTIG: Die HND-Tabelle liefert den Wert in Zehntel-Millimetern als
    # Ganzzahl (Standard-Auflösung deutscher Niederschlagssensoren), nicht in
    # mm mit Dezimalstelle wie bei den Pegel-Tabellen. Deshalb wird hier durch
    # 10 geteilt. Ohne diese Korrektur entstehen unplausible Werte wie "74 mm
    # in 5 Minuten" statt korrekt 7,4 mm.
    if r := fetch_hnd(HND_RAIN_HINDELANG_URL, "Regen Hindelang"):
        sample.hnd_rain_hindelang_time, raw = r
        sample.hnd_rain_hindelang_mm = raw / 10
    if r := fetch_hnd(HND_RAIN_BUCHLOE_URL, "Regen Buchloe"):
        sample.hnd_rain_buchloe_time, raw = r
        sample.hnd_rain_buchloe_mm = raw / 10
    if r := fetch_hnd(HND_RAIN_SCHWABMUENCHEN_URL, "Regen Schwabmünchen"):
        sample.hnd_rain_schwabmuenchen_time, raw = r
        sample.hnd_rain_schwabmuenchen_mm = raw / 10

    # Open-Meteo: aktuelle Beobachtungen
    for name, (lat, lon) in LOCATIONS.items():
        current = fetch_openmeteo_current(lat, lon, name)
        if current:
            precip = current.get("precipitation")
            if precip is not None:
                setattr(sample, f"rain_{name}_mm", precip)
            if name == "kempten":
                sample.temp_kempten_c = current.get("temperature_2m")
            elif name == "oberjoch":  # NEU
                sample.temp_oberjoch_c = current.get("temperature_2m")

    # NEU v1.5: Open-Meteo Regen-Vorhersage über das gesamte Wertach-Einzugsgebiet
    # in EINER Multi-Location-Anfrage. Jeder Punkt bekommt forecast_rain_<name>_6h_mm
    # und _24h_mm. Das Forecast-Chart gewichtet diese Werte anschließend mit der
    # jeweiligen Fließzeit (CATCHMENT) zum 2-3-Tage-Ausblick.
    #
    # Die alten forecast_rain_kempten_* Spalten bleiben aus Kontinuitätsgründen
    # erhalten, werden aber nicht mehr befüllt (Kempten ist kein Wertach-Zufluss).
    forecasts = fetch_catchment_forecast()
    for name, fc in forecasts.items():
        setattr(sample, f"forecast_rain_{name}_6h_mm", fc.get("next_6h"))
        setattr(sample, f"forecast_rain_{name}_24h_mm", fc.get("next_24h"))

    # NEU v1.6: Bodenfeuchte als Zustandsvariable (Schritt 0 der Roadmap, nur Logging).
    soil = fetch_soil_moisture()
    for name, entry in soil.items():
        for layer in SOIL_LAYERS:
            setattr(sample, f"soil_moist_{name}_{layer}", entry.get(layer))
        sample.soil_moisture_time = entry.get("time")

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
