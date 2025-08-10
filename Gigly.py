import os, math
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

# ---------- Config (env) ----------
AR_TARGET = float(os.getenv("AR_TARGET", "0.72"))
AR_WARN_PCT = float(os.getenv("AR_WARN_PCT", "0.74"))
AR_WARN_DECLINES_LEFT = int(os.getenv("AR_WARN_DECLINES_LEFT", "2"))
CONE_DEG = float(os.getenv("CONE_DEG", "25"))
LATERAL_OFFSET_MI = float(os.getenv("LATERAL_OFFSET_MI", "8"))
MODE = os.getenv("MODE", "SHADOW").upper()  # SHADOW or LIVE

# Pay floors
MIN_PER_MIN = 0.40  # $/min -> $24/hr
MIN_PER_MI = 2.00   # $/mile

# ---------- Models ----------
class Offer(BaseModel):
    id: str
    pay: float
    miles: float
    etaMin: float
    drop_lat: float
    drop_lng: float

class JourneyIn(BaseModel):
    last_drop_lat: float
    last_drop_lng: float
    waypoint_lat: float
    waypoint_lng: float

# ---------- State ----------
class ARState:
    def __init__(self, target=AR_TARGET, warn_pct=AR_WARN_PCT, warn_left=AR_WARN_DECLINES_LEFT):
        self.accepted = 0
        self.declined = 0
        self.target = target
        self.warn_pct = warn_pct
        self.warn_left = warn_left

    @property
    def total(self): return self.accepted + self.declined
    @property
    def current(self): return (self.accepted / self.total) if self.total else 1.0

    def declines_left_before_target(self) -> int:
        # how many more declines can we take before next decline breaks AR < target?
        a, d, t = self.accepted, self.declined, self.target
        left = 0
        while True:
            tot_next = a + d + left + 1
            if a / tot_next < t: break
            left += 1
        return left

    def pill(self) -> str:
        proj10 = self._projected_after_n_offers(10, accept_rate=0.7)
        left = self.declines_left_before_target()
        if proj10 >= self.warn_pct and left >= 3: return "GREEN"
        if proj10 >= self.target or left >= 1:   return "YELLOW"
        return "RED"

    def _projected_after_n_offers(self, n=10, accept_rate=0.7):
        a = self.accepted + int(n*accept_rate)
        t = self.total + n
        return a / t if t else 1.0

AR = ARState()

JOURNEY = {
    "last_drop": None,   # (lat, lng)
    "waypoint":  None    # (lat, lng)
}

# ---------- Geometry helpers ----------
EARTH_MI = 3958.8
def _deg2rad(d): return d * math.pi / 180.0
def haversine_mi(a, b):
    lat1, lon1, lat2, lon2 = map(_deg2rad, [a[0], a[1], b[0], b[1]])
    dlat = lat2 - lat1; dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * EARTH_MI * math.asin(math.sqrt(h))

def _bearing_deg(a, b):
    lat1, lon1, lat2, lon2 = map(_deg2rad, [a[0], a[1], b[0], b[1]])
    y = math.sin(lon2-lon1) * math.cos(lat2)
    x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(lon2-lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def _angle_diff_deg(a, b):
    diff = abs(a-b) % 360
    return diff if diff <= 180 else 360 - diff

def advances_corridor(last_drop, waypoint, candidate_drop, cone_deg=CONE_DEG, lateral_offset_mi=LATERAL_OFFSET_MI):
    # If no journey set yet, treat as pass
    if not last_drop or not waypoint:
        return True
    route_bearing = _bearing_deg(last_drop, waypoint)
    cand_bearing  = _bearing_deg(last_drop, candidate_drop)
    if _angle_diff_deg(route_bearing, cand_bearing) > cone_deg:
        return False
    dist_cand = haversine_mi(last_drop, candidate_drop)
    ang = math.radians(_angle_diff_deg(route_bearing, cand_bearing))
    lateral = dist_cand * math.sin(ang)
    return lateral <= lateral_offset_mi

# ---------- App ----------
app = FastAPI(title="Gig.ly Adventure Mode (single-file)")

@app.get("/")
def home():
    return {
        "status": "ok",
        "mode": MODE,
        "ar": {"accepted": AR.accepted, "declined": AR.declined, "current": round(AR.current,4),
               "declines_left": AR.declines_left_before_target(), "pill": AR.pill()},
        "journey_set": JOURNEY["last_drop"] is not None and JOURNEY["waypoint"] is not None,
        "floors": {"per_min": MIN_PER_MIN, "per_mile": MIN_PER_MI},
        "corridor": {"cone_deg": CONE_DEG, "lateral_offset_mi": LATERAL_OFFSET_MI}
    }

@app.put("/journey")
def set_journey(j: JourneyIn):
    JOURNEY["last_drop"] = (j.last_drop_lat, j.last_drop_lng)
    JOURNEY["waypoint"]  = (j.waypoint_lat, j.waypoint_lng)
    return {"ok": True, "journey": JOURNEY}

@app.get("/ar")
def ar_status():
    return {
        "accepted": AR.accepted, "declined": AR.declined,
        "current": AR.current, "declines_left": AR.declines_left_before_target(),
        "pill": AR.pill(), "target": AR_TARGET
    }

@app.post("/offer")
def consider_offer(offer: Offer):
    # Core metrics
    pph = (offer.pay / max(offer.etaMin, 0.01)) * 60.0
    ppm = offer.pay / max(offer.miles, 0.01)

    # Corridor check
    last_drop = JOURNEY["last_drop"]
    waypoint  = JOURNEY["waypoint"]
    drop      = (offer.drop_lat, offer.drop_lng)
    corridor_ok = advances_corridor(last_drop, waypoint, drop, CONE_DEG, LATERAL_OFFSET_MI)

    # Floors
    floors_ok = (pph >= 24.0) and (ppm >= MIN_PER_MI)

    # AR guard: if declining this would drop AR below 72%, auto-accept & reroute
    ar_would_break = (AR.declines_left_before_target() <= 0)

    if ar_would_break and (not floors_ok or not corridor_ok):
        action = "ACCEPT_REROUTE"
        reason = "Protect AR ≥ 72%"
        accepted = True
    else:
        if corridor_ok and floors_ok:
            action = "ACCEPT"; reason = "All checks passed"; accepted = True
        else:
            action = "DECLINE"
            reason = ("Corridor fail" if not corridor_ok else "Pay floor fail")
            accepted = False

    # Mutate AR only in LIVE mode
    if MODE == "LIVE":
        if accepted: AR.accepted += 1
        else: AR.declined += 1

    return {
        "offer_id": offer.id,
        "action": action,
        "reason": reason,
        "pay_per_hr": round(pph, 2),
        "pay_per_mile": round(ppm, 2),
        "corridor_ok": corridor_ok,
        "ar_pill": AR.pill(),
        "ar_current": round(AR.current, 4),
        "ar_declines_left": AR.declines_left_before_target(),
        "mode": MODE
    }