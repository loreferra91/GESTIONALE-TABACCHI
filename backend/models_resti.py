from pydantic import BaseModel
from typing import List, Optional

class RestModel(BaseModel):
    prelievo_id: Optional[str] = None
    importo: float
    data: str
    descrizione: str