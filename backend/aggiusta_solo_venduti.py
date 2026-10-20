import json
from pathlib import Path

backup_paths = list(Path("/Users/Lorenzo/Downloads").glob("gestionale-backup-*.json")) + \
               list(Path("/Users/Lorenzo/gestionale").glob("gestionale-backup-*.json"))

if not backup_paths:
    print("❌ Nessun backup trovato.")
    exit(1)

backup_path = max(backup_paths, key=lambda p: p.stat().st_mtime)
print(f"📂 Backup: {backup_path}")

with open(backup_path, "r", encoding="utf-8") as f:
    data = json.load(f)

prodotti = data.get("collections", {}).get("prodotti", [])

# Le tue variazioni esatte sui VENDUTI NEGOZIO:
# AMMS3648: diminuiti di 7
# AMMS3947: diminuiti di 1
# AMMS21264: diminuiti di 1
# AMMS20688: aumentati di 8
variazioni_venduti = {
    "AMMS3648": -7,
    "AMMS3947": -1,
    "AMMS21264": -1,
    "AMMS20688": 8,
}

modificati = 0
for p in prodotti:
    codice = p.get("codice")
    if codice in variazioni_venduti:
        vecchio_venduto = int(p.get("venduti_negozio", 0) or 0)
        delta = variazioni_venduti[codice]
        nuovo_venduto = max(0, vecchio_venduto + delta)
        p["venduti_negozio"] = nuovo_venduto
        
        # Aggiorniamo anche il totale dei venduti
        v_vending = int(p.get("venduti_vending", 0) or 0)
        p["venduti_totale"] = nuovo_venduto + v_vending
        
        print(f"✅ {codice} ({p.get('descrizione')}): venduti negozio {vecchio_venduto} -> {nuovo_venduto} (delta: {delta:+d})")
        modificati += 1

if modificati > 0:
    output_path = backup_path.parent / f"gestionale-backup-SOLO-VENDUTI-{backup_path.name}"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n🎉 File salvato in: {output_path}")
    print("Carica questo file nella webapp per ripristinare i venduti corretti!")
else:
    print("⚠️ Nessun prodotto trovato.")
