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