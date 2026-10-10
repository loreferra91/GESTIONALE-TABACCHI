from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from models_resti import RestModel
from datetime import datetime

router = APIRouter()

async def get_db():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    try:
        yield db
    finally:
        client.close()

@router.post("/crea-resto/{prelievo_id}")
async def crea_resto(prelievo_id: str, resto: RestModel, db=Depends(get_db)):
    # Verifica prelievo esistente
    prelievo = await db.prelievi_vending.find_one({"_id": prelievo_id})
    if not prelievo:
        raise HTTPException(404, "Prelievo non trovato")

    # Crea scontrino resto
    nuovo_resto = {
        "prelievo_id": prelievo_id,
        "importo": resto.importo,
        "data": datetime.now().isoformat(),
        "descrizione": resto.descrizione
    }
    result = await db.resti_vending.insert_one(nuovo_resto)

    # Aggiorna prelievo
    await db.prelievi_vending.update_one(
        {"_id": prelievo_id},
        {"$inc": {"importo_netto": -resto.importo},
         "$push": {"scontrini_associati": result.inserted_id}}
    )

    return {"status": "success", "resto_id": result.inserted_id}
