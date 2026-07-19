# Ordner für hochgeladene Surfwellen-HTML-Dateien

Lade hier den gespeicherten Seitenquelltext von
`buchung.surfwelleaugsburg.de/swell/` hoch (Browser: Strg+U → alles kopieren
→ als .html speichern → hier hochladen).

Ein GitHub-Actions-Workflow verarbeitet neue Dateien in diesem Ordner
automatisch: er wandelt sie in CSV-Zeilen um, merged sie in
`data/surfwelle_manual.csv` und löscht die HTML-Datei danach wieder.
Dieser Ordner sollte also im Normalfall leer sein — nur kurzzeitig gefüllt,
bis der Workflow durchgelaufen ist (üblicherweise unter einer Minute).
