"""EstateFlow AI backend.
2

3
FastAPI + Supabase (PostgreSQL). Google sign-in is verified here, every data
4
endpoint needs a valid session token, and only emails in ALLOWED_EMAILS can sign in.
5
The browser never talks to Supabase: only this backend does, with the secret key.
6
"""
7
import os
8
import time
9
from datetime import datetime
10
from zoneinfo import ZoneInfo
11

12
import jwt
13
from dotenv import load_dotenv
14
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
15
from fastapi.middleware.cors import CORSMiddleware
16
from google.auth.transport import requests as g_requests
17
from google.oauth2 import id_token
18
from supabase import create_client
19

20
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
21
load_dotenv(os.path.join(BASE_DIR, ".env"))
22

23
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
24
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
25
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
26
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
27
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
28
ORIGINS = [o.strip() for o in os.environ.get(
29
    "CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(",") if o.strip()]
30
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Karachi"))
31
SESSION_HOURS = 8
32

33
_missing = [k for k, v in (("GOOGLE_CLIENT_ID", GOOGLE_CLIENT_ID), ("JWT_SECRET", JWT_SECRET),
34
                           ("ALLOWED_EMAILS", ALLOWED), ("SUPABASE_URL", SUPABASE_URL),
35
                           ("SUPABASE_SERVICE_KEY", SUPABASE_KEY)) if not v]
36
if _missing or len(JWT_SECRET) < 32:
37
    raise RuntimeError("Setup needed in backend/.env: " + ", ".join(
38
        _missing or ["JWT_SECRET (needs 32 or more characters)"]) + ". See README.md.")
39

40
sb = create_client(SUPABASE_URL, SUPABASE_KEY)
41
try:
42
    sb.table("leads").select("id").limit(1).execute()
43
except Exception as e:
44
    raise RuntimeError("Cannot read the Supabase tables. Check SUPABASE_URL and SUPABASE_SERVICE_KEY, "
45
                       "and that database/schema.sql was run. Details: " + str(e)[:200])
46

47
# api field -> database column. The frontend keeps using the short api names.
48
COLL = {
49
    "leads": {"table": "leads", "label": "Lead", "created": "Lead Created", "title": "n",
50
              "map": {"n": "name", "ph": "phone", "em": "email", "src": "source",
51
                      "int": "interested_property", "bud": "budget", "loc": "preferred_location",
52
                      "st": "status", "ag": "assigned_agent", "arch": "archived"},
53
              "req": ["n", "ph"], "num": ["bud"], "int": [],
54
              "choices": {"st": ["New", "Contacted", "Qualified", "Interested", "Negotiation", "Converted", "Lost"]}},
55
    "properties": {"table": "properties", "label": "Property", "created": "Property Added", "title": "t",
56
                   "map": {"t": "title", "ty": "property_type", "loc": "location", "pr": "price",
57
                           "bd": "bedrooms", "ba": "bathrooms", "ar": "area", "st": "status",
58
                           "ag": "assigned_agent"},
59
                   "req": ["t", "loc", "pr"], "num": ["pr"], "int": ["bd", "ba"],
60
                   "choices": {"ty": ["Residential", "Commercial", "Apartment", "House", "Plot", "Office", "Shop"],
61
                               "st": ["Available", "Reserved", "Sold", "Rented", "Archived"]}},
62
    "appointments": {"table": "appointments", "label": "Appointment", "created": "Appointment Scheduled",
63
                     "title": "lead",
64
                     "map": {"lead": "client_name", "prop": "property_title", "ag": "assigned_agent",
65
                             "date": "appointment_date", "time": "start_time", "ty": "appointment_type",
66
                             "st": "status"},
67
                     "req": ["lead", "date", "time"], "num": [], "int": [],
68
                     "choices": {"st": ["Scheduled", "Confirmed", "Completed", "Cancelled", "Rescheduled"]}},
69
    "followups": {"table": "followups", "label": "Follow-up", "created": "Follow-up Created", "title": "task",
70
                  "map": {"task": "task", "lead": "lead_name", "ag": "assigned_agent", "due": "due_date",
71
                          "pri": "priority", "st": "status"},
72
                  "req": ["task", "due"], "num": [], "int": [],
73
                  "choices": {"pri": ["Low", "Medium", "High", "Urgent"],
74
                              "st": ["Pending", "Completed", "Overdue", "Rescheduled"]}},
75
}
76

77
app = FastAPI(title="EstateFlow AI API")
78
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST", "PATCH"],
79
                   allow_headers=["Authorization", "Content-Type"])
80

81

82
@app.middleware("http")
83
async def security_headers(request, call_next):
84
    resp = await call_next(request)
85
    resp.headers["X-Content-Type-Options"] = "nosniff"
86
    resp.headers["Cache-Control"] = "no-store"
87
    return resp
88

89

90
# ---------- authentication ----------
91
_hits = {}
92

93

94
def throttle(request: Request):
95
    ip = request.client.host if request.client else "unknown"
96
    now = time.time()
97
    recent = [t for t in _hits.get(ip, []) if now - t < 60]
98
    if len(recent) >= 10:
99
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.")
100
    recent.append(now)
101
    _hits[ip] = recent
102

103

104
def current_user(authorization: str = Header(default="")):
105
    if not authorization.startswith("Bearer "):
106
        raise HTTPException(401, "Not signed in")
107
    try:
108
        p = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
109
    except jwt.PyJWTError:
110
        raise HTTPException(401, "Session expired")
111
    if p.get("sub") not in ALLOWED:  # removing an email from the list locks it out immediately
112
        raise HTTPException(401, "Not authorised")
113
    return {"email": p["sub"], "name": p.get("name") or p["sub"]}
114

115

116
@app.post("/api/auth/google")
117
def google_login(request: Request, body: dict = Body(...)):
118
    throttle(request)
119
    cred = body.get("credential")
120
    if not isinstance(cred, str) or not cred:
121
        raise HTTPException(422, "credential is required")
122
    try:
123
        info = id_token.verify_oauth2_token(cred, g_requests.Request(), GOOGLE_CLIENT_ID,
124
                                            clock_skew_in_seconds=10)
125
    except Exception as e:  # any failure means "not verified"; the reason is logged for debugging
126
        print("Google verify failed:", type(e).__name__, e)
127
        raise HTTPException(401, "Google sign-in could not be verified")
128
    if not info.get("email_verified"):
129
        raise HTTPException(401, "Your Google email is not verified")
130
    email = str(info.get("email", "")).lower()
131
    if email not in ALLOWED:
132
        raise HTTPException(403, "This Google account is not authorised to use EstateFlow AI")
133
    name = info.get("name") or email
134
    now = int(time.time())
135
    token = jwt.encode({"sub": email, "name": name, "iat": now, "exp": now + SESSION_HOURS * 3600},
136
                       JWT_SECRET, algorithm="HS256")
137
    return {"token": token, "user": {"email": email, "name": name}}
138

139

140
@app.get("/api/auth/me")
141
def me(user: dict = Depends(current_user)):
142
    return {"user": user}
143

144

145
# ---------- database helpers ----------
146
def run(query):
147
    try:
148
        return query.execute().data
149
    except Exception as e:  # details stay in the terminal, not in the browser
150
        print("Supabase error:", e)
151
        raise HTTPException(502, "Database error. Check the backend terminal for details.")
152

153

154
def hhmm(ts):
155
    try:
156
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")
157
    except Exception:
158
        return ""
159

160

161
def to_api(coll, row):
162
    c = COLL[coll]
163
    out = {"id": row["id"]}
164
    for key, col in c["map"].items():
165
        v = row.get(col)
166
        if col == "start_time" and v:
167
            v = str(v)[:5]
168
        if v is None:
169
            v = False if col == "archived" else (0 if key in c["num"] + c["int"] else "")
170
        out[key] = v
171
    return out
172

173

174
def clean(coll, body, partial):
175
    c = COLL[coll]
176
    out = {}
177
    for k, v in body.items():
178
        if k not in c["map"]:
179
            continue
180
        if isinstance(v, str):
181
            v = v.strip()[:500]
182
        if k in c["num"] + c["int"] and v not in ("", None):
183
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
184
                raise HTTPException(422, f"{k} must be a positive number")
185
            if k in c["int"] and not isinstance(v, int):
186
                raise HTTPException(422, f"{k} must be a whole number")
187
        if k in c["choices"] and v not in c["choices"][k]:
188
            raise HTTPException(422, f"{k} has an invalid value")
189
        out[k] = v
190
    for k in c["req"]:
191
        if (not partial or k in out) and not out.get(k) and out.get(k) != 0:
192
            raise HTTPException(422, f"{k} is required")
193
    return out
194

195

196
def to_db(coll, data):
197
    c = COLL[coll]
198
    return {c["map"][k]: v for k, v in data.items()
199
            if not (k in c["num"] + c["int"] and v in ("", None))}
200

201

202
def log_activity(t, d, user):
203
    try:
204
        sb.table("activity").insert({"type": t, "description": d, "user_name": user}).execute()
205
    except Exception as e:
206
        print("Activity log failed:", e)
207

208

209
def update_event(coll, old, new):
210
    c = COLL[coll]
211
    if coll == "leads" and old.get("st") != new.get("st"):
212
        return "Lead Status Changed", f"{new['n']} moved to {new['st']}"
213
    if coll == "appointments" and new.get("st") == "Completed" and old.get("st") != "Completed":
214
        return "Appointment Completed", new["lead"]
215
    if coll == "followups" and new.get("st") == "Completed" and old.get("st") != "Completed":
216
        return "Follow-up Completed", new["task"]
217
    return f"{c['label']} Updated", str(new.get(c["title"], ""))
218

219

220
def fetch_one(coll, rid):
221
    rows = run(sb.table(COLL[coll]["table"]).select("*").eq("id", rid))
222
    if not rows:
223
        raise HTTPException(404, "Record not found")
224
    return to_api(coll, rows[0])
225

226

227
def register(coll):
228
    c = COLL[coll]
229

230
    @app.get(f"/api/{coll}", name=f"list_{coll}")
231
    def list_all(user: dict = Depends(current_user)):
232
        return [to_api(coll, r) for r in run(sb.table(c["table"]).select("*").order("seq"))]
233

234
    @app.post(f"/api/{coll}", status_code=201, name=f"create_{coll}")
235
    def create(body: dict = Body(...), user: dict = Depends(current_user)):
236
        data = clean(coll, body, partial=False)
237
        rows = run(sb.table(c["table"]).insert(to_db(coll, data)))
238
        rec = to_api(coll, rows[0])
239
        log_activity(c["created"], str(rec.get(c["title"], "")), user["name"])
240
        return rec
241

242
    @app.get(f"/api/{coll}/{{rid}}", name=f"get_{coll}")
243
    def get_one(rid: str, user: dict = Depends(current_user)):
244
        return fetch_one(coll, rid)
245

246
    @app.patch(f"/api/{coll}/{{rid}}", name=f"update_{coll}")
247
    def update(rid: str, body: dict = Body(...), user: dict = Depends(current_user)):
248
        changes = clean(coll, body, partial=True)
249
        old = fetch_one(coll, rid)
250
        db_changes = to_db(coll, changes)
251
        if not db_changes:
252
            return old
253
        rows = run(sb.table(c["table"]).update(db_changes).eq("id", rid))
254
        if not rows:
255
            raise HTTPException(404, "Record not found")
256
        new = to_api(coll, rows[0])
257
        t, d = update_event(coll, old, new)
258
        log_activity(t, d, user["name"])
259
        return new
260

261

262
for _name in COLL:
263
    register(_name)
264

265

266
@app.get("/api/health")
267
def health():
268
    return {"status": "ok"}
269

270

271
@app.get("/api/state")
272
def state(user: dict = Depends(current_user)):
273
    s = {name: [to_api(name, r) for r in run(sb.table(c["table"]).select("*").order("seq"))]
274
         for name, c in COLL.items()}
275
    acts = run(sb.table("activity").select("type,description,user_name,created_at")
276
               .order("id", desc=True).limit(50))
277
    s["activity"] = [{"t": a["type"], "d": a["description"], "time": hhmm(a["created_at"]),
278
                      "u": a["user_name"] or ""} for a in acts]
279
    return s
280

281

282
@app.post("/api/chat")
283
def chat(body: dict = Body(...), user: dict = Depends(current_user)):
284
    msg = str(body.get("message", "")).strip()
285
    if not msg:
286
        raise HTTPException(422, "message is required")
287


    # The OpenAI phase replaces this with the Responses API + tool calling.
288
    return {"status": "not_configured",
289
            "reply": "The AI agent is not connected yet. The backend received your message, but no AI model was called."}
290
