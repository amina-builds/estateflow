"""EstateFlow AI backend.

FastAPI + Supabase (PostgreSQL). Google sign-in is verified here, every data
endpoint needs a valid session token, and only emails in ALLOWED_EMAILS can sign in.
The browser never talks to Supabase: only this backend does, with the secret key.
"""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import jwt
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token
from supabase import create_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(",") if o.strip()]
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Karachi"))
SESSION_HOURS = 8

_missing = [k for k, v in (("GOOGLE_CLIENT_ID", GOOGLE_CLIENT_ID), ("JWT_SECRET", JWT_SECRET),
                           ("ALLOWED_EMAILS", ALLOWED), ("SUPABASE_URL", SUPABASE_URL),
                           ("SUPABASE_SERVICE_KEY", SUPABASE_KEY)) if not v]
if _missing or len(JWT_SECRET) < 32:
    raise RuntimeError("Setup needed in backend/.env: " + ", ".join(
        _missing or ["JWT_SECRET (needs 32 or more characters)"]) + ". See README.md.")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
try:
    sb.table("leads").select("id").limit(1).execute()
except Exception as e:
    raise RuntimeError("Cannot read the Supabase tables. Check SUPABASE_URL and SUPABASE_SERVICE_KEY, "
                       "and that database/schema.sql was run. Details: " + str(e)[:200])

# api field -> database column. The frontend keeps using the short api names.
COLL = {
    "leads": {"table": "leads", "label": "Lead", "created": "Lead Created", "title": "n",
              "map": {"n": "name", "ph": "phone", "em": "email", "src": "source",
                      "int": "interested_property", "bud": "budget", "loc": "preferred_location",
                      "st": "status", "ag": "assigned_agent", "arch": "archived"},
              "req": ["n", "ph"], "num": ["bud"], "int": [],
              "choices": {"st": ["New", "Contacted", "Qualified", "Interested", "Negotiation", "Converted", "Lost"]}},
    "properties": {"table": "properties", "label": "Property", "created": "Property Added", "title": "t",
                   "map": {"t": "title", "ty": "property_type", "loc": "location", "pr": "price",
                           "bd": "bedrooms", "ba": "bathrooms", "ar": "area", "st": "status",
                           "ag": "assigned_agent"},
                   "req": ["t", "loc", "pr"], "num": ["pr"], "int": ["bd", "ba"],
                   "choices": {"ty": ["Residential", "Commercial", "Apartment", "House", "Plot", "Office", "Shop"],
                               "st": ["Available", "Reserved", "Sold", "Rented", "Archived"]}},
    "appointments": {"table": "appointments", "label": "Appointment", "created": "Appointment Scheduled",
                     "title": "lead",
                     "map": {"lead": "client_name", "prop": "property_title", "ag": "assigned_agent",
                             "date": "appointment_date", "time": "start_time", "ty": "appointment_type",
                             "st": "status"},
                     "req": ["lead", "date", "time"], "num": [], "int": [],
                     "choices": {"st": ["Scheduled", "Confirmed", "Completed", "Cancelled", "Rescheduled"]}},
    "followups": {"table": "followups", "label": "Follow-up", "created": "Follow-up Created", "title": "task",
                  "map": {"task": "task", "lead": "lead_name", "ag": "assigned_agent", "due": "due_date",
                          "pri": "priority", "st": "status"},
                  "req": ["task", "due"], "num": [], "int": [],
                  "choices": {"pri": ["Low", "Medium", "High", "Urgent"],
                              "st": ["Pending", "Completed", "Overdue", "Rescheduled"]}},
}

app = FastAPI(title="EstateFlow AI API")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST", "PATCH"],
                   allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- authentication ----------
_hits = {}


def throttle(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < 60]
    if len(recent) >= 10:
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.")
    recent.append(now)
    _hits[ip] = recent


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not signed in")
    try:
        p = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired")
    if p.get("sub") not in ALLOWED:  # removing an email from the list locks it out immediately
        raise HTTPException(401, "Not authorised")
    return {"email": p["sub"], "name": p.get("name") or p["sub"]}


@app.post("/api/auth/google")
def google_login(request: Request, body: dict = Body(...)):
    throttle(request)
    cred = body.get("credential")
    if not isinstance(cred, str) or not cred:
        raise HTTPException(422, "credential is required")
    try:
        info = id_token.verify_oauth2_token(cred, g_requests.Request(), GOOGLE_CLIENT_ID,
                                            clock_skew_in_seconds=10)
    except ValueError:
        raise HTTPException(401, "Google sign-in could not be verified")
    if not info.get("email_verified"):
        raise HTTPException(401, "Your Google email is not verified")
    email = str(info.get("email", "")).lower()
    if email not in ALLOWED:
        raise HTTPException(403, "This Google account is not authorised to use EstateFlow AI")
    name = info.get("name") or email
    now = int(time.time())
    token = jwt.encode({"sub": email, "name": name, "iat": now, "exp": now + SESSION_HOURS * 3600},
                       JWT_SECRET, algorithm="HS256")
    return {"token": token, "user": {"email": email, "name": name}}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}


# ---------- database helpers ----------
def run(query):
    try:
        return query.execute().data
    except Exception as e:  # details stay in the terminal, not in the browser
        print("Supabase error:", e)
        raise HTTPException(502, "Database error. Check the backend terminal for details.")


def hhmm(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")
    except Exception:
        return ""


def to_api(coll, row):
    c = COLL[coll]
    out = {"id": row["id"]}
    for key, col in c["map"].items():
        v = row.get(col)
        if col == "start_time" and v:
            v = str(v)[:5]
        if v is None:
            v = False if col == "archived" else (0 if key in c["num"] + c["int"] else "")
        out[key] = v
    return out


def clean(coll, body, partial):
    c = COLL[coll]
    out = {}
    for k, v in body.items():
        if k not in c["map"]:
            continue
        if isinstance(v, str):
            v = v.strip()[:500]
        if k in c["num"] + c["int"] and v not in ("", None):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise HTTPException(422, f"{k} must be a positive number")
            if k in c["int"] and not isinstance(v, int):
                raise HTTPException(422, f"{k} must be a whole number")
        if k in c["choices"] and v not in c["choices"][k]:
            raise HTTPException(422, f"{k} has an invalid value")
        out[k] = v
    for k in c["req"]:
        if (not partial or k in out) and not out.get(k) and out.get(k) != 0:
            raise HTTPException(422, f"{k} is required")
    return out


def to_db(coll, data):
    c = COLL[coll]
    return {c["map"][k]: v for k, v in data.items()
            if not (k in c["num"] + c["int"] and v in ("", None))}


def log_activity(t, d, user):
    try:
        sb.table("activity").insert({"type": t, "description": d, "user_name": user}).execute()
    except Exception as e:
        print("Activity log failed:", e)


def update_event(coll, old, new):
    c = COLL[coll]
    if coll == "leads" and old.get("st") != new.get("st"):
        return "Lead Status Changed", f"{new['n']} moved to {new['st']}"
    if coll == "appointments" and new.get("st") == "Completed" and old.get("st") != "Completed":
        return "Appointment Completed", new["lead"]
    if coll == "followups" and new.get("st") == "Completed" and old.get("st") != "Completed":
        return "Follow-up Completed", new["task"]
    return f"{c['label']} Updated", str(new.get(c["title"], ""))


def fetch_one(coll, rid):
    rows = run(sb.table(COLL[coll]["table"]).select("*").eq("id", rid))
    if not rows:
        raise HTTPException(404, "Record not found")
    return to_api(coll, rows[0])


def register(coll):
    c = COLL[coll]

    @app.get(f"/api/{coll}", name=f"list_{coll}")
    def list_all(user: dict = Depends(current_user)):
        return [to_api(coll, r) for r in run(sb.table(c["table"]).select("*").order("seq"))]

    @app.post(f"/api/{coll}", status_code=201, name=f"create_{coll}")
    def create(body: dict = Body(...), user: dict = Depends(current_user)):
        data = clean(coll, body, partial=False)
        rows = run(sb.table(c["table"]).insert(to_db(coll, data)))
        rec = to_api(coll, rows[0])
        log_activity(c["created"], str(rec.get(c["title"], "")), user["name"])
        return rec

    @app.get(f"/api/{coll}/{{rid}}", name=f"get_{coll}")
    def get_one(rid: str, user: dict = Depends(current_user)):
        return fetch_one(coll, rid)

    @app.patch(f"/api/{coll}/{{rid}}", name=f"update_{coll}")
    def update(rid: str, body: dict = Body(...), user: dict = Depends(current_user)):
        changes = clean(coll, body, partial=True)
        old = fetch_one(coll, rid)
        db_changes = to_db(coll, changes)
        if not db_changes:
            return old
        rows = run(sb.table(c["table"]).update(db_changes).eq("id", rid))
        if not rows:
            raise HTTPException(404, "Record not found")
        new = to_api(coll, rows[0])
        t, d = update_event(coll, old, new)
        log_activity(t, d, user["name"])
        return new


for _name in COLL:
    register(_name)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/state")
def state(user: dict = Depends(current_user)):
    s = {name: [to_api(name, r) for r in run(sb.table(c["table"]).select("*").order("seq"))]
         for name, c in COLL.items()}
    acts = run(sb.table("activity").select("type,description,user_name,created_at")
               .order("id", desc=True).limit(50))
    s["activity"] = [{"t": a["type"], "d": a["description"], "time": hhmm(a["created_at"]),
                      "u": a["user_name"] or ""} for a in acts]
    return s


@app.post("/api/chat")
def chat(body: dict = Body(...), user: dict = Depends(current_user)):
    msg = str(body.get("message", "")).strip()
    if not msg:
        raise HTTPException(422, "message is required")
    # The OpenAI phase replaces this with the Responses API + tool calling.
    return {"status": "not_configured",
            "reply": "The AI agent is not connected yet. The backend received your message, but no AI model was called."}
