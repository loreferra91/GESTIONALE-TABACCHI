import json
import os
from pathlib import Path

backup_paths = list(Path("/Users/Lorenzo/gestionale").glob("gestionale-backup-*.json")) + \
               list(Path("/Users/Lorenzo/Downloads").glob("gestionale-backup-*.json")) + \
               list(Path("/Users/Lorenzo/Desktop").glob("gestionale-backup-*.json"))

if not backup_paths:
    print("❌ Nessun file di backup trovato in gestionale/, Downloads/ o Desktop/")
    exit(1)

backup_path = max(backup_paths, key=lambda p: p.stat().st_mtime)
print(f"📂 Trovato backup più recente: {backup_path}")

with open(backup_path, "r", encoding="utf-8") as f:
    data = json.load(f)

prodotti = data.get("collections", {}).get("prodotti", [])

# Correzioni giacenza negozio per i 4 prodotti
correzioni = {
    "AMMS3648": 7,
    "AMMS3947": 1,
    "AMMS21264": 1,
    "AMMS20688": -8,
}

modificati = 0
for p in prodotti:
    codice = p.get("codice")
    if codice in correzioni:
        vecchia_giacenza = int(p.get("giacenza_negozio", 0) or 0)
        delta = correzioni[codice]
        nuova_giacenza = vecchia_giacenza + delta
        p["giacenza_negozio"] = nuova_giacenza
        print(f"✅ Trovato {codice} ({p.get('descrizione')}): giacenza negozio {vecchia_giacenza} -> {nuova_giacenza}")
        modificati += 1

if modificati > 0:
    output_path = backup_path.parent / f"gestionale-backup-corretto-{backup_path.name}"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n🎉 Backup corretto salvato in: {output_path}")
    print("Ora puoi caricarlo e ripristinarlo direttamente dalla sezione Backup della webapp del gestionale!")
else:
    print("⚠️ Nessuno dei 4 codici specificati è stato trovato nel file di backup.")
