"""EstateFlow AI backend (Phase 2).

FastAPI + SQLite for local use. The storage functions below are the only part
that touches the database, so Supabase can replace them in Phase 6.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = os.environ.get(
    "ESTATEFLOW_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "estateflow.db"),
)

# name: (id prefix, first number, allowed fields, required fields, numeric fields, label, title field)
COLLECTIONS = {
    "leads": ("L", 1001, ["n", "ph", "em", "src", "int", "bud", "loc", "st", "ag", "arch"],
              ["n", "ph"], ["bud"], "Lead", "n"),
    "properties": ("P", 2001, ["t", "ty", "loc", "pr", "bd", "ba", "ar", "st", "ag"],
                   ["t", "loc", "pr"], ["pr", "bd", "ba"], "Property", "t"),
    "appointments": ("A", 3001, ["lead", "prop", "ag", "date", "time", "ty", "st"],
                     ["lead", "date", "time"], [], "Appointment", "lead"),
    "followups": ("F", 4001, ["task", "lead", "ag", "due", "pri", "st"],
                  ["task", "due"], [], "Follow-up", "task"),
}
CREATED = {"leads": "Lead Created", "properties": "Property Added",
           "appointments": "Appointment Scheduled", "followups": "Follow-up Created"}

app = FastAPI(title="EstateFlow AI API")
# Open for local development. Restrict allow_origins to your real site before deploying.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


with conn() as _c:
    _c.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, coll TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL)")
    _c.execute("CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT, d TEXT, time TEXT)")


def log_activity(c, t, d):
    c.execute("INSERT INTO activity (t, d, time) VALUES (?,?,?)", (t, d, datetime.now().strftime("%H:%M")))


def rows(c, coll):
    cur = c.execute("SELECT id, data FROM records WHERE coll=? ORDER BY seq", (coll,))
    return [{"id": r["id"], **json.loads(r["data"])} for r in cur]


def one(c, coll, rid):
    r = c.execute("SELECT id, data FROM records WHERE coll=? AND id=?", (coll, rid)).fetchone()
    if not r:
        raise HTTPException(404, "Record not found")
    return {"id": r["id"], **json.loads(r["data"])}


def clean(coll, body, partial):
    _, _, fields, required, numeric, _, _ = COLLECTIONS[coll]
    out = {}
    for k, v in body.items():
        if k not in fields:
            continue
        if isinstance(v, str):
            v = v.strip()
        if k in numeric and v not in ("", None):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise HTTPException(422, f"{k} must be a positive number")
        out[k] = v
    for k in required:
        if (not partial or k in out) and not out.get(k) and out.get(k) != 0:
            raise HTTPException(422, f"{k} is required")
    return out


def update_event(coll, old, new):
    label, title = COLLECTIONS[coll][5], COLLECTIONS[coll][6]
    if coll == "leads" and old.get("st") != new.get("st"):
        return "Lead Status Changed", f"{new['n']} moved to {new['st']}"
    if coll == "appointments" and new.get("st") == "Completed" and old.get("st") != "Completed":
        return "Appointment Completed", new["lead"]
    if coll == "followups" and new.get("st") == "Completed" and old.get("st") != "Completed":
        return "Follow-up Completed", new["task"]
    return f"{label} Updated", str(new.get(title, ""))


def register(coll):
    prefix, start = COLLECTIONS[coll][0], COLLECTIONS[coll][1]
    title = COLLECTIONS[coll][6]

    @app.get(f"/api/{coll}", name=f"list_{coll}")
    def list_all():
        with conn() as c:
            return rows(c, coll)

    @app.post(f"/api/{coll}", status_code=201, name=f"create_{coll}")
    def create(body: dict = Body(...)):
        data = clean(coll, body, partial=False)
        with conn() as c:
            m = c.execute("SELECT MAX(seq) AS m FROM records WHERE coll=?", (coll,)).fetchone()["m"]
            seq = (m + 1) if m else start
            rid = f"{prefix}-{seq}"
            c.execute("INSERT INTO records (id, coll, seq, data) VALUES (?,?,?,?)",
                      (rid, coll, seq, json.dumps(data)))
            log_activity(c, CREATED[coll], str(data.get(title, "")))
            return {"id": rid, **data}

    @app.get(f"/api/{coll}/{{rid}}", name=f"get_{coll}")
    def get_one(rid: str):
        with conn() as c:
            return one(c, coll, rid)

    @app.patch(f"/api/{coll}/{{rid}}", name=f"update_{coll}")
    def update(rid: str, body: dict = Body(...)):
        changes = clean(coll, body, partial=True)
        with conn() as c:
            old = one(c, coll, rid)
            new = {**old, **changes}
            data = {k: v for k, v in new.items() if k != "id"}
            c.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(data), rid))
            t, d = update_event(coll, old, new)
            log_activity(c, t, d)
            return new


for _name in COLLECTIONS:
    register(_name)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/state")
def state():
    with conn() as c:
        s = {name: rows(c, name) for name in COLLECTIONS}
        s["activity"] = [dict(r) for r in c.execute(
            "SELECT t, d, time FROM activity ORDER BY id DESC LIMIT 50")]
        return s


@app.post("/api/chat")
def chat(body: dict = Body(...)):
    msg = str(body.get("message", "")).strip()
    if not msg:
        raise HTTPException(422, "message is required")
    # Phase 3 replaces this with the OpenAI Responses API + tool calling.
    return {"status": "not_configured",
            "reply": "The AI agent is not connected yet (Phase 3). The backend received your message, but no AI model was called."}
