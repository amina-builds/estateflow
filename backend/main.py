"""EstateFlow AI backend.

FastAPI + Supabase (PostgreSQL). Google sign-in is done by Supabase Auth in the browser.
This backend checks the Supabase session token on every data endpoint, and only emails
in ALLOWED_EMAILS can use the app. The browser only uses the public anon key to sign in;
all data goes through this backend with the secret key.
"""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(",") if o.strip()]
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Karachi"))

_missing = [k for k, v in (("ALLOWED_EMAILS", ALLOWED), ("SUPABASE_URL", SUPABASE_URL),
                           ("SUPABASE_SERVICE_KEY", SUPABASE_KEY)) if not v]
if _missing:
    raise RuntimeError("Setup needed in backend/.env (or the Render Environment page): "
                       + ", ".join(_missing) + ". See README.md.")

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
# The browser signs in with Google through Supabase Auth and sends the Supabase access
# token as "Authorization: Bearer <token>". We ask Supabase who the token belongs to.
_cache = {}  # token -> (expires_at, user): avoids calling Supabase on every request


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not signed in")
    token = authorization[7:].strip()
    now = time.time()
    hit = _cache.get(token)
    if hit and hit[0] > now:
        user = hit[1]
    else:
        try:
            u = sb.auth.get_user(token).user
        except Exception:
            raise HTTPException(401, "Session expired")
        if not u or not u.email:
            raise HTTPException(401, "Session expired")
        email = u.email.lower()
        meta = u.user_metadata or {}
        user = {"email": email, "name": meta.get("full_name") or meta.get("name") or email}
        if len(_cache) > 500:
            _cache.clear()
        _cache[token] = (now + 60, user)
    if user["email"] not in ALLOWED:  # removing an email from the list locks it out
        raise HTTPException(401, "Not authorised")
    return user


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
