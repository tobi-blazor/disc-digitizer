"""DVD-Digitalisierung mit einem Befehl starten: Umwandeln im eigenen Fenster, Einlesen hier.

Zweck:
  Der Nutzer startet nur dieses Skript und legt dann Discs ein. Ablauf je Disc:
  Disc rein -> einlesen.py liest sie ein (Panasonic-Discs ohne Eingabe), wirft sie aus, Ton
  -> nächste Disc rein. Parallel wandelt umwandeln.py in einem zweiten Fenster die Warteschlange
  um und ruft danach "einsortieren.py auto" auf: sicher zugeordnete Discs landen in DVD_ZIEL,
  unsichere warten zum Prüfen (Übersicht beim Start und mit "einsortieren.py liste").

  Läuft umwandeln.py schon (Sperre), startet kein zweites. Beenden: Strg+C hier beendet nur das
  Einlesen, das Umwandeln-Fenster arbeitet die Warteschlange weiter ab (dort Strg+C zum Beenden,
  beim nächsten Start geht es weiter).

Aufruf:
  python -I skripte/start.py
  python -I skripte/start.py --zeitfenster 22-07     Umwandeln nur nachts
  python -I skripte/start.py --fragen                immer nach Nummer/Beschriftung fragen
  python -I skripte/start.py --nummern nie           erst testen, Nummer danach
                                                                            draufschreiben (= --unnummeriert)
  python -I skripte/start.py --nummern immer         Stapel durchnummeriert: leere
                                                                            Discs verbrauchen ihre Nummer

Umgebungsvariablen: DVD_ARBEIT, DVD_ZIEL, DVD_LAUFWERK (siehe README.md)
Nur Standardbibliothek.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER))  # auch mit python -I
import gemeinsam as g  # noqa: E402


def uebersicht() -> None:
    z = g.fortschritt_lesen()
    zaehler: dict[str, int] = {}
    for zeile in z.values():
        zaehler[zeile["status"]] = zaehler.get(zeile["status"], 0) + 1
    print(f"Arbeitsordner {g.ARBEIT}, Ziel {os.environ.get('DVD_ZIEL') or 'NICHT GESETZT'}")
    print(f"Discs: {len(z)}  " + "  ".join(f"{k}: {v}" for k, v in sorted(zaehler.items())))
    pruefen = [zeile for zeile in z.values()
               if zeile["status"] in ("umgewandelt", "problem") and zeile.get("hinweis")]
    for zeile in sorted(pruefen, key=lambda x: x["disc"]):
        print(f"  zum Prüfen: {zeile['disc']} ({zeile['status']}) {zeile['hinweis']}")


def umwandeln_starten(args) -> None:
    cmd = [sys.executable, "-I", str(HIER / "umwandeln.py")]
    if args.zeitfenster:
        cmd += ["--zeitfenster", args.zeitfenster]
    if os.name == "nt":
        # eigenes Konsolenfenster, bleibt nach dem Ende offen (cmd /k), damit Meldungen lesbar bleiben
        befehl = f'cmd /k "title DVD umwandeln && {subprocess.list2cmdline(cmd)}"'
        subprocess.Popen(befehl, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=g.REPO)
    else:
        subprocess.Popen(cmd, cwd=g.REPO)
    print("Umwandeln läuft im eigenen Fenster \"DVD umwandeln\" (startet nicht doppelt).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zeitfenster", help="Umwandeln nur in diesem Zeitfenster anfangen, z.B. 22-07")
    ap.add_argument("--fragen", action="store_true", help="beim Einlesen immer nach Nummer und Beschriftung fragen")
    ap.add_argument("--ohne-umwandeln", action="store_true", help="nur einlesen")
    ap.add_argument("--nummern", choices=("fragen", "immer", "nie"),
                    help="leere Disc: fragen (Standard), immer Nummer vergeben (durchnummeriert), nie (erst testen)")
    ap.add_argument("--unnummeriert", action="store_true", help="wie --nummern nie")
    args = ap.parse_args()

    uebersicht()
    if not os.environ.get("DVD_ZIEL"):
        print("Hinweis: DVD_ZIEL fehlt, fertige Discs bleiben in 2-fertig zum Prüfen liegen.")
    if not args.ohne_umwandeln:
        umwandeln_starten(args)
    print("\nDisc einlegen. Nach dem Ton und dem Auswerfen die nächste einlegen. Strg+C beendet.\n")
    cmd = [sys.executable, "-I", str(HIER / "einlesen.py")] + (["--fragen"] if args.fragen else []) \
        + (["--unnummeriert"] if args.unnummeriert else []) + (["--nummern", args.nummern] if args.nummern else [])
    try:
        return subprocess.call(cmd, cwd=g.REPO)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
