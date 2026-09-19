# Yumo + Pictoflex content pipeline (n8n replacement)

Plain Python replacing the n8n workflows: Stage 1 (trend research →
generation → QC → image rendering), Stage 2 (publishing), Stage 3
(analytics). No workflow-engine bugs, no re-entering credentials in a UI
every time something changes — just files and environment variables.

## Files

- `config.py` — all brand facts, prompts, formatting rules, thresholds, env loading.
- `db.py` — Postgres connection (Supabase Session Pooler).
- `utils.py` — logging, retry decorator, JSON extraction, Anthropic call.
- `stage1_generate.py` — trend research → generate 3 posts/brand → QC (+ 1 regeneration) → render images.
- `stage2_publish.py` — publish rendered posts (up to `POSTS_PER_BRAND_PER_DAY` per brand) via Post for Me.
- `stage3_analytics.py` — pull metrics for posts published in the last 14 days.
- `schema.sql` / `init_db.py` — database setup.
- `.env.example` — every environment variable needed, with comments.

## One-time setup

1. **Database**: copy `.env.example` to `.env` and fill in your Supabase
   Session Pooler credentials (Supabase dashboard → Project Settings →
   Database → Connection Pooling → **Session** mode, not Transaction, not
   the direct host). Then run:
   ```bash
   pip install -r requirements.txt
   python init_db.py
   ```
   This is safe to re-run — it only creates tables that don't exist yet.

2. **Fill in the rest of `.env`**: Anthropic key, Brave key, Imejis key +
   template IDs + text-field names (per brand — these differ per Imejis
   template), Post for Me key + your `spc_...` account IDs per brand,
   Supabase Storage service-role key + bucket name.

3. **Test locally** (optional but recommended before deploying):
   ```bash
   python stage1_generate.py
   # check the content_items table has rows with status='rendered'
   python stage2_publish.py
   # check status flips to 'published' (or 'failed' with error_message set)
   python stage3_analytics.py
   ```

## Deploying on Render (three Cron Jobs)

You're already paying for Render, so this needs no new hosting spend —
just three Cron Jobs in the same account, each running one script. No
n8n subscription, no separate workflow host.

For each of the three scripts, create a **Render Cron Job**:

| Cron Job name | Command | Suggested schedule (UTC) |
|---|---|---|
| pipeline-stage1-generate | `python stage1_generate.py` | `0 7 * * *` |
| pipeline-stage2-publish | `python stage2_publish.py` | `0 10 * * *` |
| pipeline-stage3-analytics | `python stage3_analytics.py` | `0 20 * * *` |

Setup for each Cron Job:
- **Runtime**: Python 3
- **Build Command**: `pip install -r requirements.txt`
- **Root Directory**: wherever you push this folder in your repo
- **Environment Variables**: paste in everything from `.env` (Render
  supports an "Environment Group" you can attach to all three jobs at
  once, so you only enter credentials one time, not three).

That's it — no visual workflow to keep re-wiring, no node-mode bugs, no
re-uploading credentials every time a prompt or a query changes. Editing
behavior going forward is editing a `.py` file and pushing it; Render
redeploys automatically.

## Why these design choices (carried over from the n8n build)

- **Imejis images are rendered as `format=jpg`, never `png`.** TikTok's
  photo-post endpoint rejects PNG outright — this was the root cause of a
  long debugging chase in the n8n version. Don't change this back.
- **Session Pooler, not direct host, for Postgres.** The direct
  `db.{ref}.supabase.co` host is IPv6-only and won't connect from Render.
- **Post for Me account IDs are its own `spc_...` IDs**, not raw
  platform IDs — get these from the Post for Me dashboard.
- **Firm "exactly 3 posts/day per brand"** generation, with compact
  slide text and no final "visit our website" slide (the bio link covers
  that) — both are encoded directly in `config.py`'s prompt rules, per
  your explicit feedback.
- **One post's or one brand's failure never stops the run** — every
  stage catches and records errors per-item (`status='failed'` with
  `error_message` set) rather than crashing, matching the "no single
  failure blocks the day" requirement from the original spec.
- **Max 2 generation attempts** (1 original + 1 regeneration) before a
  post goes to the rejected/review queue — no infinite regeneration loops.

## Still to build (Stage 4-6, deferred from the original 17-phase spec)

- Stage 4: learning loop that writes rows into the `learnings` table
  explaining *why* content performed well/poorly (Stage 1 already reads
  from `learnings` — it just has nothing to read yet until this is built).
- Stage 5: Pictoflex lead capture (form/webhook → `leads` table).
- Stage 6: advanced autonomy / self-tuning.

Ask whenever you're ready to add these — they slot into this same
structure as new scripts.
