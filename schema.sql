-- Yumo + Pictoflex content engine schema
-- Run once against your Supabase Postgres database.

CREATE TABLE IF NOT EXISTS trend_log (
    id              SERIAL PRIMARY KEY,
    brand           TEXT NOT NULL,
    trend_description TEXT,
    relevance_score NUMERIC,
    decision        TEXT,        -- 'use' | 'ignore'
    reasoning       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_items (
    id                  SERIAL PRIMARY KEY,
    brand               TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_pillar       TEXT,
    objective           TEXT,
    target_audience     TEXT,
    hook                TEXT,
    platform_target      TEXT,
    slides              JSONB,
    caption             TEXT,
    cta                 TEXT,
    hashtags            TEXT,
    visual_spec          JSONB,
    trend_used           INTEGER REFERENCES trend_log(id),
    quality_score        JSONB,
    status               TEXT NOT NULL DEFAULT 'draft',
        -- draft | approved | rejected | rendered | published | failed
    attempt_number        INTEGER DEFAULT 1,
    image_urls           JSONB,
    scheduled_for         TIMESTAMPTZ,
    published_at          TIMESTAMPTZ,
    external_post_ids     JSONB,
    error_message         TEXT
);

CREATE INDEX IF NOT EXISTS idx_content_items_brand_status ON content_items (brand, status);
CREATE INDEX IF NOT EXISTS idx_content_items_created_at ON content_items (created_at);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id                  SERIAL PRIMARY KEY,
    content_item_id       INTEGER REFERENCES content_items(id),
    brand               TEXT NOT NULL,
    social_account_id      TEXT,
    platform             TEXT,
    impressions          BIGINT,
    reach               BIGINT,
    likes               BIGINT,
    comments             BIGINT,
    shares              BIGINT,
    saves               BIGINT,
    profile_visits        BIGINT,
    link_clicks          BIGINT,
    raw_metrics           JSONB,
    collected_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_perf_metrics_content_item ON performance_metrics (content_item_id);

CREATE TABLE IF NOT EXISTS learnings (
    id              SERIAL PRIMARY KEY,
    brand           TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    summary         TEXT,       -- human/AI readable "what worked and why"
    supporting_data  JSONB
);

CREATE TABLE IF NOT EXISTS leads (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT,
    business         TEXT,
    email           TEXT,
    project_type      TEXT,
    description       TEXT,
    budget_range      TEXT,
    timeline         TEXT,
    existing_product   TEXT,
    source_content_id   INTEGER REFERENCES content_items(id),
    status           TEXT NOT NULL DEFAULT 'new'
);
