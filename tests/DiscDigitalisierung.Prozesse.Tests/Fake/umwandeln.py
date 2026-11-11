"""Test-Fake fuer umwandeln.py: stuerzt mit Exit 3 und stderr-Ausgabe ab."""
import sys

print("vor dem Absturz", flush=True)
for i in range(40):
    print(f"Fehlerzeile {i}", file=sys.stderr, flush=True)
sys.exit(3)
