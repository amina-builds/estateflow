# EstateFlow AI

AI-powered real estate management platform (leads, properties, appointments, follow-ups, AI assistant).

**Status:** Frontend (pink theme) + FastAPI backend + Supabase database + Google sign-in through Supabase Auth. The AI agent (OpenAI) comes next.

## Folder structure
```
backend/    FastAPI server (main.py, requirements.txt, .env.example)
database/   schema.sql (run once in Supabase)
frontend/   Web app (index.html, config.js)
package.json  Optional `npm start` shortcut for the backend
.gitignore  Files Git must NOT upload (.env, venv, *.db)
```

## How sign-in works
1. The user clicks "Continue with Google". The browser goes to Google through Supabase Auth.
2. Google sends the user back to your website (Vercel) with a Supabase session.
3. The website sends the Supabase access token to the backend (Render) on every request.
4. The backend asks Supabase who the token belongs to, and only emails in `ALLOWED_EMAILS` get data.

## Security
- Every endpoint except `/api/health` needs a valid Supabase session token.
- Only emails in `ALLOWED_EMAILS` can use the app. Anyone else gets "not authorised".
- The Supabase secret (service_role) key lives only in the backend environment. The frontend only has the public anon key.
- CORS only allows your listed websites. Use HTTPS only online (Render and Vercel provide it).
- Not built yet: separate data per agency, roles (admin / agent), audit export.

## One-time setup
0. **Supabase:** supabase.com > New project. SQL Editor > New query > paste everything from `database/schema.sql` > Run.
   Project Settings > API: copy the Project URL, the anon/publishable key, and the secret (service_role) key.
1. **Google Cloud Console:** create a project > APIs & Services > OAuth consent screen (External; add your emails under "Test users")
   > Credentials > Create credentials > OAuth client ID > Web application.
   Under **Authorized redirect URIs** add `https://YOUR-PROJECT-ID.supabase.co/auth/v1/callback`. Copy the Client ID and Client Secret.
2. **Supabase > Authentication > Providers > Google:** turn it on and paste the Client ID and Client Secret.
3. **Supabase > Authentication > URL Configuration:** set Site URL to your Vercel link, and add these under Redirect URLs:
   `https://YOUR-APP.vercel.app/**` and (for local testing) `http://127.0.0.1:5500/**`.
4. **frontend/config.js:** set `ESTATEFLOW_API` (your Render link), `SUPABASE_URL` and `SUPABASE_ANON_KEY`.
5. **backend/.env** (locally) or **Render > Environment**: set `ALLOWED_EMAILS`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `CORS_ORIGINS` (your Vercel link, no / at the end).
6. Install packages (venv active): `pip install -r backend/requirements.txt`

## Run locally
```
cd backend
venv\Scripts\activate
uvicorn main:app --reload
```
Open http://127.0.0.1:8000/api/health (should show ok). Then right-click `frontend/index.html` > Open with Live Server
and continue with Google.

## Deploy
- **Render (backend):** Root directory `backend`, build `pip install -r requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- **Vercel (frontend):** Root directory `frontend`. Every `git push` redeploys both.

## Push to GitHub
```
git add .
git status        (check that .env, venv and *.db are NOT listed)
git commit -m "Describe your change"
git push
```

## API
GET /api/auth/me, GET /api/health, GET /api/state,
GET|POST /api/{leads|properties|appointments|followups}, GET|PATCH /api/{...}/{id}, POST /api/chat
