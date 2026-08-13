# DeepDoc

DeepDoc is an AI-powered research workspace for reading and understanding academic papers. It supports PDF upload, direct arXiv analysis, evidence-grounded summaries, references, slide outlines, optional narrated MP4 generation, and side-by-side PDF reading.

The current product model is project-based:

- A **Project** is the workspace container.
- A **Paper** is one resource inside a project.
- A single-paper analysis is simply a project with one paper.
- Multi-paper projects use the same workspace layout and add lightweight paper navigation.

## Current Features

- Upload one or multiple PDF papers.
- Analyze arXiv papers by search or direct arXiv ID / URL.
- Unified Workspace UI for single-paper and multi-paper projects.
- Side-by-side AI understanding panel and original PDF viewer.
- Overview, Evidence, References, and Slides tabs.
- Project history with per-user isolation when signed in.
- Anonymous browser-only analysis for visitors.
- Supabase Auth + Postgres persistence for signed-in users.
- Cloudflare R2 storage for signed-in users' source PDFs and generated artifacts.
- Reanalysis flow that creates a new analysis while preserving the existing one.
- Optional slide generation and narrated MP4 video generation.

## Architecture

```text
DeepDoc
  FastAPI backend
  Static HTML/CSS/JS frontend
  Gemini analysis pipeline
  Supabase Auth and Postgres
  Cloudflare R2 object storage
  Browser localStorage / IndexedDB for anonymous users
```

Important behavior:

- Anonymous users can analyze papers without signing in.
- Anonymous project metadata is stored in the current browser.
- Anonymous uploaded PDFs are stored in browser IndexedDB when available, so the original PDF can survive refresh on the same browser.
- Signed-in users store projects and analyses in Supabase.
- Signed-in users' source PDFs are stored in R2 when R2 is configured.
- The backend always derives user identity from the verified Supabase access token. It does not trust frontend-provided user IDs.

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv ai-service/venv
ai-service/venv/bin/python -m pip install -r requirements.txt
```

Create `ai-service/.env`:

```bash
GEMINI_API_KEY=your_gemini_api_key
```

## Supabase Setup

Supabase is required for login, cross-device history, user isolation, and persistent project storage.

Set these backend environment variables:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_your_publishable_key
SUPABASE_SECRET_KEY=sb_secret_your_secret_key
```

Compatibility names are still accepted:

```bash
SUPABASE_ANON_KEY=your_legacy_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_legacy_service_role_key
```

`SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` must only be set on the backend or Render environment. Never expose it in browser code.

### Database Schema

Before deploying code that uses projects, run the schema in Supabase:

1. Open `supabase/schema.sql`.
2. Copy the SQL content.
3. Paste it into Supabase SQL Editor.
4. Run it.
5. Then run:

```sql
NOTIFY pgrst, 'reload schema';
```

The schema creates:

- `public.projects`
- `public.analyses`
- `project_id`, `status`, and `updated_at` support on analyses
- indexes for project/history queries
- RLS policies for authenticated users

Supabase may warn that the query contains destructive operations because the file drops and recreates RLS policies. It does not drop tables or delete analysis data.

If Render logs show this error:

```text
Could not find the table 'public.projects' in the schema cache
```

run `supabase/schema.sql` and reload the schema cache as shown above.

## Cloudflare R2 Setup

R2 is optional but recommended for signed-in users so source PDFs remain available after refresh, redeploy, or cross-device login.

Set these backend environment variables:

```bash
R2_BUCKET=deepdoc-files
R2_ENDPOINT_URL=https://your_account_id.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=your_r2_access_key_id
R2_SECRET_ACCESS_KEY=your_r2_secret_access_key
R2_REGION=auto
```

New signed-in uploads are stored with paths shaped like:

```text
users/{user_id}/projects/{project_id}/analyses/{analysis_id}/original.pdf
```

Legacy paths continue to work when present.

## Optional MP4 Generation

DeepDoc can generate narrated MP4 videos from slide outlines.

Requirements:

- `ffmpeg` installed on the server
- Piper binary available
- Piper voice model configured

The `/health` endpoint reports:

- `ffmpeg_available`
- `tts.ready`
- `tts.piper_binary_available`
- `tts.piper_model_available`
- `mp4_ready`

MP4 generation is optional. Summary, evidence, references, PDF reading, and slide outlines can work without it.

## Run Locally

```bash
cd ai-service
venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- App: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

## Testing

Run the local project/storage tests:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/deepdoc-pyc ai-service/venv/bin/python -m unittest ai-service/test_projects.py
```

Run Python compile checks:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/deepdoc-pyc ai-service/venv/bin/python -m py_compile ai-service/storage.py ai-service/main.py ai-service/test_projects.py
```

Run frontend syntax and whitespace checks:

```bash
node --check ai-service/static/app.js
git diff --check
```

`ai-service/test_models.py` calls Gemini to list models and is an external API probe, not a normal local regression test.

## Runtime Data

Generated runtime files are ignored by git:

- Uploaded/source PDFs: `ai-service/uploads/`
- Local JSON analysis records: `ai-service/data/analyses/`
- Generated videos: `ai-service/data/videos/`
- Regression outputs: `ai-service/data/eval_*`

When Supabase is not configured, DeepDoc falls back to local JSON storage for backend records.

When Supabase is configured but the user is signed out, browser-only project history is stored in the current browser. Browser storage is not cross-device and may be removed if the user clears site data.

## Key API Routes

- `POST /analyze-pdf`
- `GET /arxiv/search`
- `POST /arxiv/analyze`
- `GET /projects`
- `GET /projects/{project_id}`
- `PATCH /projects/{project_id}`
- `DELETE /projects/{project_id}`
- `POST /projects/analyze-pdfs`
- `POST /projects/{project_id}/papers`
- `GET /analyses/{analysis_id}`
- `POST /analyses/{analysis_id}/reanalyze`
- `GET /analyses/{analysis_id}/pdf`
- `GET /analyses/{analysis_id}/download`
- `DELETE /analyses/{analysis_id}`

## Deployment Notes

Before deploying to Render or another host:

- Set `GEMINI_API_KEY`.
- If using login/history, set Supabase URL and keys.
- Run `supabase/schema.sql` in Supabase before deploying project-enabled code.
- If using persistent source PDFs, set R2 variables.
- Check `/health` after deploy.
- Confirm `/health` reports `gemini_configured: true`.
- Confirm `storage_backend`, `supabase_auth_enabled`, and `r2_storage_enabled` match your intended setup.
- If MP4 is enabled, confirm `mp4_ready: true`.

After changing static JS/CSS, update `APP_VERSION` in `ai-service/main.py` and the script cache-buster in `ai-service/static/index.html` so browsers do not keep stale frontend code.

## Manual End-to-End Checklist

After a deploy, verify:

1. Anonymous user can upload one PDF and see the PDF side by side.
2. Anonymous user can refresh and reopen the local project on the same browser.
3. Signed-in user can upload one PDF and see it in History.
4. Signed-in user can upload multiple PDFs and switch papers.
5. Project name is editable and separate from paper title.
6. Reanalyze opens a confirmation dialog and creates a new analysis.
7. User A cannot see User B's projects.
8. R2-backed PDFs reopen after refresh and redeploy.
9. Direct arXiv analysis works by ID, abs URL, or PDF URL.
