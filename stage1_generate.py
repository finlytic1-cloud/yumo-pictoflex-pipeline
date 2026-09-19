#!/usr/bin/env python3
"""
Stage 1: Trend research -> content generation -> QC -> image rendering.

Run daily (e.g. Render Cron Job at 07:00). For each brand:
  1. Search for current trends relevant to the brand (Brave Search).
  2. Ask Claude to score which trend (if any) is genuinely usable.
  3. Log the trend decision.
  4. Pull a summary of recently published/generated content + learnings,
     so the generator avoids repeating itself and applies what worked.
  5. Ask Claude to generate exactly 3 carousel posts, each a different
     content pillar.
  6. QC-score each post. If it fails, regenerate once with the failure
     reasons fed back in, then QC again. If it still fails, it goes to
     a failed/review queue rather than being published.
  7. For posts that pass, render each slide into an image (Imejis),
     re-host the image in Supabase Storage, and mark the post 'rendered'
     so Stage 2 can publish it.

One brand's or one post's failure never stops the rest of the run -
everything below is wrapped so errors are logged and recorded on the
individual content_item rather than crashing the whole script.
"""
import json
import time

import requests

import config
import db
from utils import anthropic_call, extract_json, log, retry, TransientHTTPError


# ---------------------------------------------------------------------------
# Trend research
# ---------------------------------------------------------------------------

@retry(times=3, base_delay=3.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def search_trends(brand_cfg: dict) -> list[dict]:
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": config.BRAVE_API_KEY,
        },
        params={"q": brand_cfg["trend_query"], "count": 8, "freshness": "pw"},
        timeout=30,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Brave {resp.status_code}: {resp.text[:300]}")
    if resp.status_code != 200:
        log.warning("Brave search returned %s for %s: %s", resp.status_code, brand_cfg["brand"], resp.text[:300])
        return []
    results = resp.json().get("web", {}).get("results", [])
    return [
        {"title": r.get("title", ""), "description": r.get("description", ""), "url": r.get("url", "")}
        for r in results[:6]
    ]


def score_trends(brand_cfg: dict, snippets: list[dict]) -> list[dict]:
    if not snippets:
        return []
    snippet_text = "\n".join(
        f"{i+1}. {s['title']} — {s['description']} ({s['url']})" for i, s in enumerate(snippets)
    )
    system = (
        f"You are a trend-relevance scorer for {brand_cfg['brand'].title()}.\n\n{brand_cfg['facts']}\n\n"
        "You will be given a list of recent web search results. Score how relevant each one is as a "
        "genuine content angle for this brand. Never force a connection that isn't natural. It's fine "
        "and expected for most or all of them to be 'ignore'."
    )
    user = (
        f"Candidate trends/search results:\n{snippet_text}\n\n"
        'Return ONLY valid JSON, no markdown fences: '
        '{"trends": [{"trend_description": "...", "relevance_score": 0-10, "decision": "use|ignore", "reasoning": "..."}]}'
    )
    try:
        raw = anthropic_call(system, user, max_tokens=1500)
        parsed = extract_json(raw)
        return parsed.get("trends", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("score_trends failed for %s: %s", brand_cfg["brand"], exc)
        return []


def pick_best_trend(scored: list[dict]) -> dict | None:
    usable = [t for t in scored if t.get("decision") == "use"]
    if not usable:
        return None
    return max(usable, key=lambda t: t.get("relevance_score", 0))


def log_trend(brand: str, trend: dict | None) -> int | None:
    if trend is None:
        row = db.execute(
            "INSERT INTO trend_log (brand, trend_description, relevance_score, decision, reasoning) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id;",
            (brand, "no relevant trend found", None, "ignore", "no candidate trend cleared relevance bar"),
            returning=True,
        )
    else:
        row = db.execute(
            "INSERT INTO trend_log (brand, trend_description, relevance_score, decision, reasoning) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id;",
            (
                brand,
                trend.get("trend_description"),
                trend.get("relevance_score"),
                trend.get("decision"),
                trend.get("reasoning"),
            ),
            returning=True,
        )
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Context gathering (repetition prevention + learning loop input)
# ---------------------------------------------------------------------------

def get_recent_content_summary(brand: str) -> str:
    row = db.fetch_one(
        """
        SELECT COALESCE(
            string_agg(DISTINCT content_pillar || ': ' || hook, ' | '),
            'No recent content yet.'
        ) AS summary
        FROM content_items
        WHERE brand = %s AND created_at > now() - interval '14 days'
        ORDER BY NULL;
        """,
        (brand,),
    )
    return row["summary"] if row else "No recent content yet."


def get_latest_learnings(brand: str) -> str:
    row = db.fetch_one(
        """
        SELECT COALESCE(
            string_agg(summary, ' | ' ORDER BY created_at DESC),
            'No learnings recorded yet.'
        ) AS summary
        FROM (
            SELECT summary, created_at FROM learnings
            WHERE brand = %s
            ORDER BY created_at DESC
            LIMIT 5
        ) recent;
        """,
        (brand,),
    )
    return row["summary"] if row else "No learnings recorded yet."


# ---------------------------------------------------------------------------
# Content generation
# ---------------------------------------------------------------------------

def generate_posts(brand_cfg: dict, trend: dict | None, recent_summary: str, learnings: str) -> list[dict]:
    system = config.build_system_prompt(brand_cfg)
    trend_text = (
        f"Trend to consider (only use it if it fits naturally): {trend['trend_description']} "
        f"(reasoning: {trend.get('reasoning', '')})"
        if trend
        else "No usable trend was found today - generate purely from brand strategy and content pillars."
    )
    user = f"""{trend_text}

Recent content already published/generated (avoid repeating these hooks/topics/structures):
{recent_summary}

Learnings from past performance (apply these where relevant):
{learnings}

{config.GENERATION_TASK_INSTRUCTION}
"""
    raw = anthropic_call(system, user, max_tokens=6000)
    parsed = extract_json(raw)
    posts = parsed.get("posts", [])
    if not posts:
        log.error("Claude returned 0 posts for %s. Raw response: %s", brand_cfg["brand"], raw[:1000])
    return posts


# ---------------------------------------------------------------------------
# Quality control
# ---------------------------------------------------------------------------

def qc_score(brand_cfg: dict, post: dict, recent_summary: str) -> dict:
    system = (
        f"You are the quality controller for {brand_cfg['brand'].title()}'s content pipeline.\n\n"
        f"{brand_cfg['facts']}\n\nRecently published content (for repetition scoring):\n{recent_summary}"
    )
    user = f"""Score this generated carousel post on each category (0-10):
hook_strength, brand_fit, audience_relevance, originality, clarity, value, conversion_potential,
factual_accuracy, platform_suitability, repetition_risk (10 = not repetitive at all).

Post:
{json.dumps(post, ensure_ascii=False)}

Return ONLY valid JSON, no markdown fences:
{{"hook_strength": 0, "brand_fit": 0, "audience_relevance": 0, "originality": 0, "clarity": 0, "value": 0,
"conversion_potential": 0, "factual_accuracy": 0, "platform_suitability": 0, "repetition_risk": 0,
"weighted_total": 0-100, "pass": true/false, "failure_reasons": ["..."]}}

A post should only pass if weighted_total >= {config.QC_PASS_THRESHOLD}, factual_accuracy >= {config.QC_MIN_FACTUAL_ACCURACY},
and repetition_risk >= {config.QC_MIN_REPETITION_SCORE}. Compute weighted_total yourself as the average of
the 9 category scores scaled to 100, and set pass accordingly.
"""
    raw = anthropic_call(system, user, max_tokens=1200, temperature=0.3)
    return extract_json(raw)


def regenerate_post(brand_cfg: dict, post: dict, qc_result: dict, recent_summary: str, learnings: str) -> dict:
    system = config.build_system_prompt(brand_cfg)
    user = f"""The following carousel post failed quality control. Rewrite it to fix the specific problems
listed, while keeping the same general content_pillar and objective unless the problem is the pillar itself.

Original post:
{json.dumps(post, ensure_ascii=False)}

Quality control failure reasons:
{json.dumps(qc_result.get('failure_reasons', []), ensure_ascii=False)}

Recent content already published (avoid repeating these):
{recent_summary}

Learnings to apply:
{learnings}

Return ONLY valid JSON for the single improved post, no markdown fences:
{{"content_pillar": "...", "objective": "...", "target_audience": "...", "hook": "...", "slides": ["..."],
"caption": "...", "cta": "...", "hashtags": "...", "visual_spec": "...", "trend_used": "..."}}

{config.CAROUSEL_FORMAT_RULES}
"""
    raw = anthropic_call(system, user, max_tokens=2000)
    return extract_json(raw)


# ---------------------------------------------------------------------------
# Persist content item
# ---------------------------------------------------------------------------

def insert_content_item(brand: str, post: dict, quality_score: dict, status: str,
                         attempt_number: int, trend_id: int | None, platform_target: str) -> int:
    row = db.execute(
        """
        INSERT INTO content_items
            (brand, content_pillar, objective, target_audience, hook, platform_target,
             slides, caption, cta, hashtags, visual_spec, trend_used, quality_score, status, attempt_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (
            brand,
            post.get("content_pillar"),
            post.get("objective"),
            post.get("target_audience"),
            post.get("hook"),
            platform_target,
            json.dumps(post.get("slides", [])),
            post.get("caption"),
            post.get("cta"),
            post.get("hashtags"),
            json.dumps(post.get("visual_spec", "")),
            trend_id,
            json.dumps(quality_score),
            status,
            attempt_number,
        ),
        returning=True,
    )
    return row["id"]


def mark_failed(content_item_id: int, error_message: str) -> None:
    db.execute(
        "UPDATE content_items SET status = 'failed', error_message = %s WHERE id = %s;",
        (error_message, content_item_id),
    )


# ---------------------------------------------------------------------------
# Image rendering
# ---------------------------------------------------------------------------

@retry(times=3, base_delay=2.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def fetch_rendered_image(brand_cfg: dict, slide_text: str) -> bytes:
    resp = requests.get(
        f"https://render.imejis.io/v1/{brand_cfg['imejis_template_id']}",
        params={
            "dma-api-key": config.IMEJIS_API_KEY,
            f"{brand_cfg['imejis_text_field']}.text": slide_text,
            f"{brand_cfg['imejis_text_field']}.color": "ffffff",
            f"{brand_cfg['imejis_text_field']}.backgroundColor": "transparent",
            f"{brand_cfg['imejis_text_field']}.textBackgroundColor": "transparent",
            f"{brand_cfg['imejis_text_field']}.borderColor": "transparent",
            f"{brand_cfg['imejis_text_field']}.strokeColor": "transparent",
            # IMPORTANT: TikTok's photo-post endpoint rejects PNG outright.
            # jpg is required, not png - this was the root cause of a long
            # debugging saga during the n8n build. Do not change back to png.
            "format": "jpg",
        },
        timeout=60,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Imejis {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.content


@retry(times=3, base_delay=2.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def upload_to_supabase(file_path: str, image_bytes: bytes) -> str:
    resp = requests.post(
        f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_STORAGE_BUCKET}/{file_path}",
        headers={
            "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "image/jpeg",
            "x-upsert": "true",
        },
        data=image_bytes,
        timeout=60,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Supabase upload {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_STORAGE_BUCKET}/{file_path}"


def render_carousel(content_item_id: int, brand: str, brand_cfg: dict, slides: list[str]) -> list[str]:
    image_urls = []
    for idx, slide_text in enumerate(slides):
        image_bytes = fetch_rendered_image(brand_cfg, slide_text)
        file_path = f"{brand}/{content_item_id}/slide_{idx}.jpg"
        public_url = upload_to_supabase(file_path, image_bytes)
        image_urls.append(public_url)
        time.sleep(1.5)  # be gentle with Imejis rate limits
    return image_urls


# ---------------------------------------------------------------------------
# Per-post pipeline (generate -> QC -> maybe regenerate -> render)
# ---------------------------------------------------------------------------

def process_post(brand_cfg: dict, post: dict, trend_id: int | None, recent_summary: str, learnings: str) -> None:
    brand = brand_cfg["brand"]
    try:
        qc1 = qc_score(brand_cfg, post, recent_summary)
    except Exception as exc:  # noqa: BLE001
        log.error("QC attempt 1 failed for %s post %r: %s", brand, post.get("hook"), exc)
        content_item_id = insert_content_item(brand, post, {"error": str(exc)}, "failed", 1, trend_id,
                                               brand_cfg["platform_target"])
        mark_failed(content_item_id, f"QC attempt 1 errored: {exc}")
        return

    if qc1.get("pass"):
        content_item_id = insert_content_item(brand, post, qc1, "approved", 1, trend_id, brand_cfg["platform_target"])
    else:
        log.info("%s post %r failed QC1 (%s) - regenerating", brand, post.get("hook"), qc1.get("failure_reasons"))
        try:
            improved = regenerate_post(brand_cfg, post, qc1, recent_summary, learnings)
            qc2 = qc_score(brand_cfg, improved, recent_summary)
        except Exception as exc:  # noqa: BLE001
            log.error("Regeneration/QC2 failed for %s post %r: %s", brand, post.get("hook"), exc)
            content_item_id = insert_content_item(brand, post, qc1, "failed", 2, trend_id, brand_cfg["platform_target"])
            mark_failed(content_item_id, f"regeneration errored: {exc}")
            return

        if qc2.get("pass"):
            content_item_id = insert_content_item(brand, improved, qc2, "approved", 2, trend_id, brand_cfg["platform_target"])
            post = improved
        else:
            log.info("%s post %r failed QC2 too - moving to review queue", brand, post.get("hook"))
            content_item_id = insert_content_item(brand, improved, qc2, "rejected", 2, trend_id, brand_cfg["platform_target"])
            return

    # Render images for approved posts.
    try:
        image_urls = render_carousel(content_item_id, brand, brand_cfg, post.get("slides", []))
        db.execute(
            "UPDATE content_items SET status = 'rendered', image_urls = %s WHERE id = %s;",
            (json.dumps(image_urls), content_item_id),
        )
        log.info("%s content_item %d rendered with %d slides", brand, content_item_id, len(image_urls))
    except Exception as exc:  # noqa: BLE001
        log.error("Image rendering failed for %s content_item %d: %s", brand, content_item_id, exc)
        mark_failed(content_item_id, f"image rendering errored: {exc}")


# ---------------------------------------------------------------------------
# Per-brand pipeline
# ---------------------------------------------------------------------------

def process_brand(brand_cfg: dict) -> None:
    brand = brand_cfg["brand"]
    log.info("=== Stage 1: %s ===", brand)

    try:
        snippets = search_trends(brand_cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("Trend search failed for %s, continuing without a trend: %s", brand, exc)
        snippets = []

    scored = score_trends(brand_cfg, snippets)
    trend = pick_best_trend(scored)
    trend_id = log_trend(brand, trend)

    recent_summary = get_recent_content_summary(brand)
    learnings = get_latest_learnings(brand)

    try:
        posts = generate_posts(brand_cfg, trend, recent_summary, learnings)
    except Exception as exc:  # noqa: BLE001
        log.error("Content generation failed entirely for %s: %s", brand, exc)
        return

    if not posts:
        log.error("No posts generated for %s today - nothing to process.", brand)
        return

    for post in posts:
        try:
            process_post(brand_cfg, post, trend_id, recent_summary, learnings)
        except Exception as exc:  # noqa: BLE001
            # Absolute last-resort catch so one bad post never kills the run.
            log.error("Unhandled error processing a post for %s: %s", brand, exc)


def main() -> None:
    for brand_cfg in config.BRANDS.values():
        try:
            process_brand(brand_cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("Unhandled error processing brand %s: %s", brand_cfg["brand"], exc)
    log.info("Stage 1 complete.")


if __name__ == "__main__":
    main()
