"""Gemeinsame Helfer für die DVD-Digitalisierung: Ordner, disc.json, Fortschrittsliste.

Zweck: Die Teile einlesen.py, umwandeln.py und einsortieren.py reden nur über Ordner und
die Datei disc.json je Disc miteinander. Dieses Modul legt fest, wo was liegt.

Ordner unter DVD_ARBEIT:
  1-roh/<disc>/       disc.json + tNN.vob (DVD-Video) bzw. kopierte Dateien (Daten-DVD)
  2-fertig/<disc>/    disc.json + tNN.mkv + vorschau/ (nach dem Umwandeln, Rohdaten gelöscht)
  3-problem/<disc>/   Discs mit Lesefehlern oder fehlgeschlagener Prüfung
  fortschritt.csv     eine Zeile je Disc

Status einer Disc (disc.json "status" und Fortschrittsliste):
  eingelesen -> umgewandelt -> einsortiert -> geprueft (erst dann darf die DVD weg)
  problem (mit "problem_grund")

Umgebungsvariablen:
  DVD_ARBEIT   Arbeitsordner (Standard: <repo>/ausgabe/dvd)
  DVD_ZIEL     Wurzel der Jellyfin-Bibliotheken , z.B. \\\\nas\\medien\\dvd (nur einsortieren.py)
  MUSIK_ZIEL, CD_OFFSET  nur für Musik-CDs, siehe musik_cd.py
  Fehlen sie im Prozess, liest das Modul sie unter Windows aus der Benutzer-Umgebung (Registry).

Aufruf (nur zum Ansehen): python -I skripte/gemeinsam.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path



def _benutzer_umgebung() -> None:
    """Fehlende DVD_*-Variablen aus der dauerhaften Benutzer-Umgebung (Registry HKCU\\Environment) holen.

    Nötig, wenn das Terminal vor dem Setzen gestartet wurde, z.B. das Terminal in der Claude-App:
    Es erbt die Umgebung der App und kennt später gesetzte Variablen nicht. Gesetzt wird in
    os.environ, damit auch gestartete Unterprozesse (umwandeln.py, einsortieren.py) die Werte sehen.
    """
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            for name in ("DVD_ARBEIT", "DVD_ZIEL", "DVD_LAUFWERK", "MUSIK_ZIEL", "CD_OFFSET"):
                if os.environ.get(name):
                    continue
                try:
                    wert, _ = winreg.QueryValueEx(k, name)
                except FileNotFoundError:
                    continue
                if wert:
                    os.environ[name] = os.path.expandvars(str(wert))
    except OSError:
        pass


_benutzer_umgebung()
REPO = Path(__file__).resolve().parents[1]  # Repo- bzw. Programmordner über skripte/
ARBEIT =Path(os.environ.get("DVD_ARBEIT") or REPO / "ausgabe" / "dvd")
ROH = ARBEIT / "1-roh"
FERTIG = ARBEIT / "2-fertig"
PROBLEM = ARBEIT / "3-problem"
FORTSCHRITT = ARBEIT / "fortschritt.csv"
STATUS = ("eingelesen", "umgewandelt", "einsortiert", "geprueft", "problem", "leer", "unlesbar", "doppelt")
# leer/unlesbar/doppelt: nur fortschritt.csv (doppelt = zweite Kopie einer schon eingelesenen Disc)
FORTSCHRITT_SPALTEN = ("disc", "beschriftung", "typ", "titel", "stunden", "status", "geaendert", "hinweis")


def jetzt() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def disc_name(nummer: int | str) -> str:
    """Disc-Nummer als Ordnername, z.B. 7 -> 'D0007'."""
    s = str(nummer).strip().upper().lstrip("D")
    return f"D{int(s):04d}"


def disc_json_lesen(ordner: Path) -> dict:
    return json.loads((ordner / "disc.json").read_text(encoding="utf-8"))


def ersetzen(tmp: Path | str, ziel: Path) -> None:
    """os.replace mit Wiederholung: Unter Windows ist das Ziel kurz gesperrt, wenn es gerade jemand
    liest (Programm-Oberfläche, Virenscanner, Indexdienst). Bis ca. 15 s warten, dann Fehler;
    die Temp-Datei wird dabei entfernt."""
    pause = 0.1
    for versuch in range(40):
        try:
            os.replace(tmp, ziel)
            return
        except PermissionError:
            if versuch == 39:
                Path(tmp).unlink(missing_ok=True)
                raise
            time.sleep(pause)
            pause = min(pause * 1.5, 0.5)


def disc_json_schreiben(ordner: Path, daten: dict) -> None:
    """Schreibt disc.json atomar (erst Temp-Datei, dann umbenennen)."""
    ordner.mkdir(parents=True, exist_ok=True)
    daten["geaendert"] = jetzt()
    fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    ersetzen(tmp, ordner / "disc.json")


def fortschritt_lesen() -> dict[str, dict]:
    if not FORTSCHRITT.exists():
        return {}
    with FORTSCHRITT.open(encoding="utf-8", newline="") as f:
        return {z["disc"]: z for z in csv.DictReader(f, delimiter=";")}


class _Sperre:
    """Einfache Dateisperre (einlesen.py und umwandeln.py schreiben fortschritt.csv parallel)."""

    def __init__(self, pfad: Path, warten_s: float = 30):
        self.pfad, self.warten_s = pfad, warten_s

    def __enter__(self):
        ende = time.monotonic() + self.warten_s
        while True:
            try:
                self.fd = os.open(self.pfad, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except (FileExistsError, PermissionError):
                # PermissionError: Windows meldet das, solange die Sperre gerade gelöscht wird
                # verwaiste Sperre (älter als 2 min) entfernen
                try:
                    if time.time() - self.pfad.stat().st_mtime > 120:
                        self.pfad.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > ende:
                    raise TimeoutError(f"Sperre {self.pfad} wird nicht frei")
                time.sleep(0.2)

    def __exit__(self, *_):
        os.close(self.fd)
        try:
            self.pfad.unlink()
        except FileNotFoundError:
            pass


def fortschritt_setzen(daten: dict, hinweis: str = "") -> None:
    """Aktualisiert die Zeile einer Disc in fortschritt.csv aus ihrer disc.json."""
    ARBEIT.mkdir(parents=True, exist_ok=True)
    with _Sperre(FORTSCHRITT.with_suffix(".lock")):
        _fortschritt_schreiben(daten, hinweis)


def _fortschritt_schreiben(daten: dict, hinweis: str) -> None:
    zeilen = fortschritt_lesen()
    titel = daten.get("titel", [])
    zeilen[daten["disc"]] = {
        "disc": daten["disc"],
        "beschriftung": daten.get("beschriftung", ""),
        "typ": daten.get("typ", ""),
        "titel": str(len(titel)),
        "stunden": f"{sum(t.get('dauer_s', 0) for t in titel) / 3600:.2f}",
        "status": daten.get("status", ""),
        "geaendert": jetzt(),
        "hinweis": hinweis or daten.get("problem_grund", ""),
    }
    ARBEIT.mkdir(parents=True, exist_ok=True)
    tmp = FORTSCHRITT.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FORTSCHRITT_SPALTEN, delimiter=";")
        w.writeheader()
        for k in sorted(zeilen):
            w.writerow({s: zeilen[k].get(s, "") for s in FORTSCHRITT_SPALTEN})
    ersetzen(tmp, FORTSCHRITT)


def status_setzen(ordner: Path, status: str, grund: str = "") -> dict:
    assert status in STATUS, status
    daten = disc_json_lesen(ordner)
    daten["status"] = status
    if status == "problem":
        daten["problem_grund"] = grund
    disc_json_schreiben(ordner, daten)
    fortschritt_setzen(daten, grund)
    return daten


if __name__ == "__main__":
    print(f"Arbeitsordner: {ARBEIT}")
    z = fortschritt_lesen()
    zaehler: dict[str, int] = {}
    for zeile in z.values():
        zaehler[zeile["status"]] = zaehler.get(zeile["status"], 0) + 1
    print(f"Discs: {len(z)}  " + "  ".join(f"{k}: {v}" for k, v in sorted(zaehler.items())))
