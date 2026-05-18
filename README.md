# Goal Setting & Tracking Portal

Full-stack hackathon demo for AtomQuest Hackathon 2026. It supports employee goal creation, manager approvals, shared KPIs, quarterly tracking, admin monitoring, audit logs, and CSV exports.

## Stack

- Frontend: React.js, Tailwind CSS, Vite
- Backend: Flask, JWT auth
- Database: SQLite
- Deployment: Vercel frontend, Render/Railway backend

## Demo Accounts

On first run, the backend creates a local SQLite database and seeds basic demo users:

- Employee: `employee@demo.com` / `password123`
- Manager: `manager@demo.com` / `password123`
- Admin: `admin@demo.com` / `password123`

For the final hackathon demo, load the full realistic dataset:

```bash
cd backend
python3 seed_demo_data.py
```

This creates 1 Admin, 2 Managers, 4 Employees, approved goals, pending goals, shared KPIs, quarterly updates, comments, audit logs, and dashboard-ready progress data. It resets the configured SQLite database before loading the demo dataset.

## Run Locally

Backend:

```bash
cd backend
cp .env.example .env
python3 -m pip install -r requirements.txt
python3 app.py
```

If port `5001` is busy:

```bash
PORT=5002 python3 app.py
```

Frontend:

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open `http://localhost:5173`.

If Vite chooses a later port such as `5176`, open that URL. The backend already allows local Vite ports through `5177`.

## SQLite

The backend stores data in `backend/goal_portal.db` by default. To use a different file, set:

```bash
SQLITE_DB_PATH=/absolute/path/to/goal_portal.db
JWT_SECRET=<strong-secret>
FRONTEND_ORIGIN=https://your-vercel-app.vercel.app
GEMINI_API_KEY=<optional-gemini-api-key>
```

The three demo users are created automatically only when the `users` table is empty.

## Optional AI Insights

Dashboards include a lightweight optional **AI Performance Insights** card. It uses Gemini only from the Flask backend, never from the frontend.

```bash
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-flash-latest
```

If the key is missing or Gemini fails, the app continues normally and shows `AI insights temporarily unavailable.`

## Main API Groups

- `POST /api/auth/login`
- `GET /api/goals/dashboard`
- `GET /api/goals`
- `POST /api/goals`
- `POST /api/goals/submit`
- `POST /api/goals/:id/quarterly-updates`
- `GET /api/manager/dashboard`
- `GET /api/manager/submissions`
- `POST /api/manager/goals/:id/decision`
- `POST /api/manager/shared-goals`
- `POST /api/admin/goals/:id/unlock`
- `GET /api/admin/dashboard`
- `GET /api/admin/audit-logs`
- `GET /api/ai/employee-insights`
- `GET /api/ai/manager-insights`
- `GET /api/ai/admin-insights`
- `GET /api/admin/reports/export?type=goals`
- `GET /api/admin/reports/export?type=quarterly`
- `GET /api/admin/reports/export?type=team`
- `GET /api/admin/reports/export?type=organization`

## Final Demo Flow

1. Open `/` for the professional landing page.
2. Use quick demo login buttons on `/login`.
3. Employee: view dashboard, create goals, validate 100% weightage, submit goals, add quarterly updates after approval.
4. Manager: review pending submissions, approve/rework/reject goals, assign shared KPIs, add quarterly feedback.
5. Admin/HR: monitor organization dashboard, unlock approved goals, manage users, inspect audit logs, export reports.

## Deployment Notes

Backend on Render/Railway:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Set `JWT_SECRET`, `FRONTEND_ORIGIN`, and optionally `SQLITE_DB_PATH`
- For longer-lived production data on Render/Railway, attach a persistent volume and point `SQLITE_DB_PATH` to that volume.

Frontend on Vercel:

- Root: `frontend`
- Build command: `npm run build`
- Output directory: `dist`
- Set `VITE_API_URL` to the deployed backend URL
