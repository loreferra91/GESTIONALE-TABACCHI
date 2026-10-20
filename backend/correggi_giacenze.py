import asyncio
import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

async def correggi_giacenze_passate():
    env_path = Path("/Users/Lorenzo/gestionale/backend/.env")
    if env_path.exists():
        load_dotenv(env_path)
    
    # Se mongo_url punta a localhost ma siamo in produzione o vogliamo connetterci al DB online/remoto, verifichiamo se c'è una variabile alternativa o se usiamo mongodb://127.0.0.1:27017
    mongo_url = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("DB_NAME", "gestionale")
    print(f"Connessione a MongoDB URL: {mongo_url} | DB: {db_name}")

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = client[db_name]

    correzioni = {
        "AMMS3648": 7,
        "AMMS3947": 1,
        "AMMS21264": 1,
        "AMMS20688": -8,
    }

    try:
        await client.admin.command('ping')
        print("✅ Connessione al database stabilita con successo.")
    except Exception as e:
        print(f"❌ Impossibile connettersi a MongoDB ({mongo_url}): {e}")
        return

    for codice, delta in correzioni.items():
        doc = await db.prodotti.find_one({"codice": codice}, {"_id": 0, "codice": 1, "descrizione": 1, "giacenza_negozio": 1})
        if not doc:
            print(f"❌ Prodotto {codice} non trovato nel database.")
            continue
        
        vecchia_giacenza = int(doc.get("giacenza_negozio", 0) or 0)
        nuova_giacenza = vecchia_giacenza + delta
        
        res = await db.prodotti.update_one(
            {"codice": codice},
            {"$set": {"giacenza_negozio": nuova_giacenza}}
        )
        print(f"✅ {codice} ({doc.get('descrizione')}): giacenza negozio passata da {vecchia_giacenza} a {nuova_giacenza} (delta: {delta:+d}, modificati: {res.modified_count})")

    client.close()

if __name__ == "__main__":
    asyncio.run(correggi_giacenze_passate())
