# EstateFlow AI - Frontend + Backend (Phase 2 + 4)

## Run it (VS Code terminal)
1. Backend:
   cd backend
   python -m venv venv
   venv\Scripts\activate          (Mac/Linux: source venv/bin/activate)
   pip install -r requirements.txt
   uvicorn main:app --reload
   -> check http://127.0.0.1:8000/api/health  (should show {"status":"ok"})
   -> API docs: http://127.0.0.1:8000/docs
2. Frontend: open frontend/index.html with the VS Code "Live Server" extension (Open with Live Server).
3. Sign in (demo login) and add your first lead. Data is saved in backend/estateflow.db.

## Notes
- The database starts empty: no demo records.
- Sign-in is still a demo screen: real authentication and roles are not built yet.
- AI Agent: POST /api/chat answers "not connected" until the OpenAI phase.
- Storage is SQLite for local use. Supabase replaces it in Phase 6.
