"""Test-Fake fuer einlesen.py: spricht das Ereignis-Protokoll, ohne etwas Echtes zu tun."""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")


def senden(**d):
    d = {"v": 1, "zeit": datetime.now(timezone.utc).isoformat(), **d}
    print("@@ " + json.dumps(d, ensure_ascii=False), flush=True)


senden(typ="start", skript="einlesen", pid=os.getpid(), argumente=sys.argv[1:])
print("Hallo Ümlaut ✓", flush=True)
senden(typ="meldung", stufe="info", text="laufwerk=" + os.environ.get("DVD_LAUFWERK", "") +
       " modus=" + os.environ.get("DVD_EREIGNISSE", ""))

if "ignoriere_stopp" in sys.argv:
    while True:
        time.sleep(0.2)

senden(typ="frage", id="q1", art="beschriftung", text="Beschriftung für Ä?", optionen=[],
       freitext=True, vorgabe="x", disc="D0001")

for zeile in sys.stdin:
    try:
        n = json.loads(zeile)
    except ValueError:
        continue
    if n.get("typ") == "antwort" and n.get("id") == "q1":
        senden(typ="frage_erledigt", id="q1", wert=n.get("wert"), durch="programm")
    elif n.get("typ") == "stopp":
        senden(typ="ende", code=0)
        sys.exit(0)
