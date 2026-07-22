"""
Füllt Lücken in data/collected.csv aus den HISTORISCHEN HND-Daten.

Zwei Anwendungsfälle:
  A) Neu hinzugefügte Spalten rückwirkend füllen (z.B. Wertach/Sebastianskapelle/
     Thalhofen/Türkheim-W/Haslach — überall dort, wo die Zelle bisher leer ist).
  B) Ausgefallene Collect-Läufe reparieren: fehlende 15-Minuten-Zeilen aus der
     Historie neu einsetzen.

Datenquelle: dieselbe HND-„tabelle"-URL wie beim Live-Scraping, nur mit
„days=N" — dann liefert HND bis zu ein Jahr 15-Minuten-Werte im GLEICHEN
HTML-Tabellenformat. Der Parser ist daher 1:1 der aus collect.py.

SICHERHEIT: Schreibt standardmäßig in eine NEUE Datei (collected_backfilled.csv),
nicht in collected.csv. So kannst du das Ergebnis erst prüfen (diff) und dann
manuell übernehmen. Mit --inplace wird collected.csv direkt überschrieben
(vorher wird eine .bak-Kopie angelegt).

Aufruf:
    python backfill.py --dry-run            # nur zeigen was passieren würde
    python backfill.py                      # -> data/collected_backfilled.csv
    python backfill.py --days 60            # nur die letzten 60 Tage holen
    python backfill.py --only wertach,thalhofen   # nur bestimmte Pegel
    python backfill.py --inplace            # collected.csv direkt ersetzen (+ .bak)

Nur requests + pandas — wie collect.py.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

TIMEOUT = 30
USER_AGENT = "surfwelle-augsburg-backfill/1.0 (research project)"
CSV_PATH = Path(__file__).parent / "data" / "collected.csv"

# Toleranz beim Zuordnen eines historischen Werts zu einem CSV-Slot
SLOT = pd.Timedelta(minutes=15)
MATCH_TOL = pd.Timedelta(minutes=8)

# -----------------------------------------------------------------------------
# Pegel-Konfiguration: welche CSV-Spalten aus welcher HND-Seite/Methode kommen.
# base = Pegel-Basis-URL (ohne /tabelle...). Für jede Methode wird die passende
# Spalte + Zeitspalte in collected.csv befüllt.
# -----------------------------------------------------------------------------
GAUGES = {
    "wertach": {
        "base": "https://www.hnd.bayern.de/pegel/iller_lech/wertach-12401004",
        "abfluss":     ("wertach_q_m3s", "wertach_q_time"),
        "wasserstand": ("wertach_w_cm",  "wertach_w_time"),
    },
    "sebastianskapelle": {
        "base": "https://www.hnd.bayern.de/pegel/iller_lech/sebastianskapelle-12402007",
        "abfluss":     ("sebastianskapelle_q_m3s", "sebastianskapelle_q_time"),
        "wasserstand": ("sebastianskapelle_w_cm",  "sebastianskapelle_w_time"),
    },
    "thalhofen": {
        "base": "https://www.hnd.bayern.de/pegel/iller_lech/thalhofen-12404705",
        "abfluss":     ("thalhofen_q_m3s", "thalhofen_q_time"),
        "wasserstand": ("thalhofen_w_cm",  "thalhofen_w_time"),
    },
    "haslach": {
        "base": "https://www.hnd.bayern.de/pegel/iller_lech/haslach-werksabfluss-12404002",
        "abfluss":     ("haslach_q_m3s", "haslach_q_time"),
        "wasserstand": ("haslach_w_cm",  "haslach_w_time"),
    },
    "biessenhofen": {
        "base": "https://www.hnd.bayern.de/pegel/iller_lech/biessenhofen-12405005",
        "abfluss":     ("biessenhofen_q_m3s", "biessenhofen_q_time"),
        "wasserstand": ("biessenhofen_w_cm",  "biessenhofen_w_time"),
    },
    "tuerkheim": {
        "base": "https://www.hnd.bayern.de/pegel/iller_lech/tuerkheim-12406008",
        "abfluss":     ("tuerkheim_q_m3s", "tuerkheim_time"),
        "wasserstand": ("tuerkheim_w_cm",  "tuerkheim_w_time"),
    },
    "oberhausen": {
        "base": "https://www.hnd.bayern.de/pegel/donau_bis_kelheim/augsburg-oberhausen-12407000",
        "abfluss":     ("oberhausen_q_m3s", "oberhausen_q_time"),
        "wasserstand": ("oberhausen_w_cm",  "oberhausen_w_time"),
    },
}


def fetch_hnd_history(base_url: str, methode: str, days: int) -> pd.Series | None:
    """
    Holt die komplette historische HND-Tabelle (bis zu `days` Tage) und gibt eine
    Zeitreihe (Index = naive Berlin-Zeit, Werte = float) zurück.

    Nutzt exakt denselben Tabellen-Parser wie collect.py (bewährtes Format),
    liest aber ALLE Zeilen statt nur der neuesten.
    """
    url = f"{base_url}/tabelle?methode={methode}&days={days}&setdiskr=15"
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! HTTP-Fehler ({methode}): {e}", file=sys.stderr)
        return None

    try:
        tables = pd.read_html(io.StringIO(resp.text), decimal=",", thousands=".")
    except ValueError:
        print(f"  ! keine Tabelle gefunden ({methode})", file=sys.stderr)
        return None

    # Tabellen-Auswahl: Die Seite kann mehrere 2-Spalten-Tabellen enthalten
    # (z.B. eine "Durchschnitt 2016->2026"-Vergleichstabelle neben der echten
    # Zeitreihe). Ein einzelnes ".any()"-Match auf das Datumsmuster reicht nicht
    # — das kann zufällig auch eine kleine Deko-/Vergleichstabelle treffen und
    # deren (völlig anders skalierte) Werte einlesen. Deshalb: (a) fast die
    # GESAMTE erste Spalte muss dem Datumsformat entsprechen (>=95%), und (b)
    # bei mehreren Kandidaten gewinnt die mit den MEISTEN Zeilen — die echte
    # Jahres-Zeitreihe hat tausende Einträge, eine Vergleichstabelle nur wenige.
    date_re = r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}"
    all_candidates = []   # (index, n_rows, match_ratio) — für Diagnose
    good = []             # (n_rows, dataframe) — erfüllen die 95%-Schwelle
    for i, cand in enumerate(tables):
        if cand.shape[1] != 2:
            continue
        match_ratio = cand.iloc[:, 0].astype(str).str.match(date_re).mean()
        all_candidates.append((i, len(cand), round(float(match_ratio), 2)))
        if match_ratio >= 0.95:
            good.append((len(cand), cand))

    if not good:
        print(f"  ! keine passende Tabelle ({methode}) — 2-Spalten-Kandidaten "
              f"(Index, Zeilen, Datums-Trefferquote): {all_candidates}", file=sys.stderr)
        return None
    # Bei mehreren Treffern: die mit den meisten Zeilen nehmen
    _, df = max(good, key=lambda c: c[0])

    if df is None or df.empty:
        print(f"  ! keine passende Tabelle ({methode})", file=sys.stderr)
        return None

    df = df.copy()
    df.columns = ["t", "v"]
    df["t"] = pd.to_datetime(df["t"].astype(str).str.strip(), format="%d.%m.%Y %H:%M", errors="coerce")
    # WICHTIG: pd.read_html(..., decimal=",", thousands=".") oben hat die Werte-
    # Spalte in aller Regel schon korrekt zu float konvertiert (z.B. "6,51" -> 6.51).
    # Nochmaliges manuelles Parsen (Punkt entfernen, Komma->Punkt) auf einem
    # bereits-numerischen float würde "6.51" -> "651" korrumpieren, weil der
    # Dezimalpunkt fälschlich als Tausendertrennzeichen behandelt wird. Der
    # manuelle Regex-Fallback greift daher NUR, wenn die Spalte noch als Text
    # vorliegt (z.B. wenn read_html die Auto-Konvertierung nicht anwenden konnte).
    if pd.api.types.is_numeric_dtype(df["v"]):
        pass  # bereits korrekt geparst — nichts tun
    else:
        df["v"] = pd.to_numeric(
            df["v"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        )
    df = df.dropna(subset=["t", "v"]).drop_duplicates(subset="t", keep="last")
    return df.set_index("t")["v"].sort_index()


def row_slot(row: pd.Series) -> pd.Timestamp | None:
    """Bestimmt den 15-Minuten-Messslot einer Zeile (Berlin-naiv)."""
    # bevorzugt die HND-Messzeitpunkte in der Zeile (häufigster Wert), sonst collected_at
    time_cols = [c for c in row.index if c.endswith("_time") and pd.notna(row[c])]
    cands = []
    for c in time_cols:
        t = pd.to_datetime(row[c], format="mixed", errors="coerce")
        if pd.notna(t):
            cands.append(t)
    if cands:
        slot = pd.Series(cands).mode().iloc[0]
    else:
        t = pd.to_datetime(row["collected_at"], errors="coerce", utc=True)
        if pd.isna(t):
            return None
        slot = t.tz_convert("Europe/Berlin").tz_localize(None)
    # auf 15 Min runden
    return slot.round("15min")


def lookup(series: pd.Series, slot: pd.Timestamp):
    """Historischen Wert am Slot holen (± Toleranz)."""
    if series is None or slot is None:
        return None
    if slot in series.index:
        return float(series.loc[slot])
    pos = series.index.get_indexer([slot], method="nearest")
    if pos[0] == -1:
        return None
    nearest_t = series.index[pos[0]]
    if abs(nearest_t - slot) <= MATCH_TOL:
        return float(series.loc[nearest_t])
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(CSV_PATH))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--only", default="", help="Kommaliste von Pegeln (sonst alle)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inplace", action="store_true", help="collected.csv direkt ersetzen (+ .bak)")
    ap.add_argument("--no-insert", action="store_true", help="nur leere Zellen füllen, keine neuen Zeilen")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    df = pd.read_csv(csv_path)
    print(f"collected.csv: {len(df)} Zeilen, {df.shape[1]} Spalten")

    which = [g.strip() for g in args.only.split(",") if g.strip()] or list(GAUGES)
    which = [g for g in which if g in GAUGES]
    print(f"Pegel: {', '.join(which)}  |  Historie: {args.days} Tage\n")

    # 1) Historische Reihen holen
    hist = {}  # (gauge, methode) -> Series
    for g in which:
        cfg = GAUGES[g]
        for methode in ("abfluss", "wasserstand"):
            if methode not in cfg:
                continue
            print(f"  hole {g} / {methode} …")
            s = fetch_hnd_history(cfg["base"], methode, args.days)
            if s is not None and len(s):
                hist[(g, methode)] = s
                print(f"    ✓ {len(s)} Werte  {s.index.min():%d.%m.%Y %H:%M} – {s.index.max():%d.%m.%Y %H:%M}")
    if not hist:
        print("Keine historischen Daten geholt — Abbruch (HND erreichbar? days-Param korrekt?).")
        return 1

    # Slot je bestehender Zeile
    df["_slot"] = df.apply(row_slot, axis=1)
    existing_slots = set(df["_slot"].dropna())

    # Zeit-Zielspalten müssen String (object) sein — sonst weigert sich pandas,
    # in eine leer eingelesene float64-Spalte einen ISO-String zu schreiben.
    for (g, methode) in hist:
        vcol, tcol = GAUGES[g][methode]
        if tcol in df.columns:
            df[tcol] = df[tcol].astype(object)
        if vcol in df.columns:
            df[vcol] = df[vcol].astype(object)

    # 2A) leere Zellen in bestehenden Zeilen füllen
    filled = 0
    for (g, methode), series in hist.items():
        vcol, tcol = GAUGES[g][methode]
        if vcol not in df.columns:
            continue
        for i, row in df.iterrows():
            if pd.notna(row.get(vcol)):
                continue  # schon vorhanden -> nie überschreiben
            val = lookup(series, row["_slot"])
            if val is not None:
                df.at[i, vcol] = val
                df.at[i, tcol] = row["_slot"].isoformat()
                filled += 1

    # 2B) fehlende Zeilen einsetzen (Gap-Reparatur)
    inserted = 0
    new_rows = []
    if not args.no_insert:
        all_hist_slots = sorted(set().union(*[set(s.index) for s in hist.values()]))
        for slot in all_hist_slots:
            if slot in existing_slots:
                continue
            r = {c: None for c in df.columns}
            # collected_at = Slot als UTC ISO (Berlin -> UTC)
            # ambiguous/nonexistent: bei der Zeitumstellung gibt es Stunden, die
            # doppelt (Okt) bzw. gar nicht (Mär) existieren. Ohne diese Argumente
            # wirft pandas dort einen ValueError und der ganze Lauf bricht ab.
            # ambiguous=True -> im Zweifel Sommerzeit; shift_forward -> nicht
            # existierende Zeit auf die nächste gültige schieben.
            utc = slot.tz_localize(
                "Europe/Berlin", ambiguous=True, nonexistent="shift_forward"
            ).tz_convert("UTC")
            r["collected_at"] = utc.isoformat()
            r["_slot"] = slot
            got = False
            for (g, methode), series in hist.items():
                vcol, tcol = GAUGES[g][methode]
                if vcol not in df.columns:
                    continue
                val = lookup(series, slot)
                if val is not None:
                    r[vcol] = val
                    r[tcol] = slot.isoformat()
                    got = True
            if got:
                new_rows.append(r)
                inserted += 1

    print(f"\nErgebnis:")
    print(f"  Zellen gefüllt (leere Spalten in bestehenden Zeilen): {filled}")
    print(f"  Neue Zeilen aus Historie eingesetzt:                  {inserted}")

    if args.dry_run:
        print("\n(dry-run — nichts geschrieben)")
        return 0

    out = df
    if new_rows:
        out = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    out = out.sort_values("collected_at").drop(columns=["_slot"])

    if args.inplace:
        bak = csv_path.with_suffix(".csv.bak")
        shutil.copy2(csv_path, bak)
        out.to_csv(csv_path, index=False)
        print(f"\ncollected.csv ersetzt (Backup: {bak.name})")
    else:
        outp = csv_path.parent / "collected_backfilled.csv"
        out.to_csv(outp, index=False)
        print(f"\nGeschrieben: {outp}")
        print("Prüfen und bei Bedarf über collected.csv kopieren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
