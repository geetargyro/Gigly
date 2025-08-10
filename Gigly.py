from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Offer(BaseModel):
    id: str
    pay: float
    miles: float
    etaMin: float

@app.get("/")
def home():
    return {"status": "Gig.ly Adventure Mode online", "mode": "SHADOW"}

@app.post("/offer")
def consider_offer(offer: Offer):
    # TEMP stub: just calculates pay/hr and $/mile
    pay_per_hr = (offer.pay / offer.etaMin) * 60
    pay_per_mile = offer.pay / offer.miles if offer.miles > 0 else 0
    decision = "ACCEPT" if pay_per_hr >= 24 and pay_per_mile >= 2 else "DECLINE"
    return {
        "offer_id": offer.id,
        "pay_per_hr": round(pay_per_hr, 2),
        "pay_per_mile": round(pay_per_mile, 2),
        "decision": decision
    }
