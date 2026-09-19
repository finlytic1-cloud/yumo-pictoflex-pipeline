#!/usr/bin/env python3
"""
Stage 2: Publish rendered posts to Instagram + TikTok via Post for Me.

Run daily (e.g. Render Cron Job at 10:00), a few hours after Stage 1.

For each brand:
  - Work out how many posts have already been published today (so re-runs
    or a delayed cron don't over-publish).
  - Pull up to (POSTS_PER_BRAND_PER_DAY - already_published_today) posts
    that are rendered and ready, best quality_score first.
  - Publish each one via Post for Me. One post's failure is recorded and
    does not stop the others.
"""
import json

import requests

import config
import db
from utils import log, retry, TransientHTTPError


def already_published_today(brand: str) -> int:
    row = db.fetch_one(
        """
        SELECT count(*) AS n FROM content_items
        WHERE brand = %s AND status = 'published' AND published_at::date = (now() AT TIME ZONE 'utc')::date;
        """,
        (brand,),
    )
    return row["n"] if row else 0


def get_posts_to_publish(brand: str, limit: int) -> list[dict]:
    if limit <= 0:
        return []
    return db.fetch_all(
        """
        SELECT id, hook, caption, hashtags, image_urls
        FROM content_items
        WHERE brand = %s AND status = 'rendered' AND created_at > now() - interval '18 hours'
        ORDER BY (quality_score->>'weighted_total')::numeric DESC NULLS LAST
        LIMIT %s;
        """,
        (brand, limit),
    )


@retry(times=3, base_delay=3.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def publish_to_postforme(caption: str, social_accounts: list[str], media_urls: list[str]) -> dict:
    resp = requests.post(
        "https://api.postforme.dev/v1/social-posts",
        headers={
            "Authorization": f"Bearer {config.POSTFORME_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "caption": caption,
            "social_accounts": social_accounts,
            "media": [{"url": u} for u in media_urls],
        },
        timeout=60,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Post for Me {resp.status_code}: {resp.text[:500]}")
    # Don't raise_for_status() here - a 4xx (e.g. TikTok's active-user cap,
    # or an account issue) should be recorded on the post, not retried
    # forever or treated as a crash.
    return {"status_code": resp.status_code, "body": _safe_json(resp)}


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:1000]}


def mark_published(content_item_id: int, response_body: dict) -> None:
    db.execute(
        "UPDATE content_items SET status = 'published', published_at = now(), external_post_ids = %s WHERE id = %s;",
        (json.dumps(response_body), content_item_id),
    )


def mark_failed(content_item_id: int, error_message: str, response_body: dict | None = None) -> None:
    db.execute(
        "UPDATE content_items SET status = 'failed', error_message = %s, external_post_ids = %s WHERE id = %s;",
        (error_message, json.dumps(response_body) if response_body else None, content_item_id),
    )


def process_brand(brand_cfg: dict) -> None:
    brand = brand_cfg["brand"]
    social_accounts = brand_cfg["postforme_accounts"]
    if not social_accounts:
        log.warning("No Post for Me social accounts configured for %s - skipping.", brand)
        return

    published_today = already_published_today(brand)
    remaining = config.POSTS_PER_BRAND_PER_DAY - published_today
    log.info("=== Stage 2: %s (already published today: %d, remaining budget: %d) ===",
              brand, published_today, remaining)

    posts = get_posts_to_publish(brand, remaining)
    if not posts:
        log.info("Nothing to publish for %s right now.", brand)
        return

    for post in posts:
        caption = f"{post.get('caption') or ''}\n\n{post.get('hashtags') or ''}".strip()
        image_urls = post.get("image_urls") or []
        if isinstance(image_urls, str):
            image_urls = json.loads(image_urls)
        if not image_urls:
            mark_failed(post["id"], "no image_urls present at publish time")
            continue

        try:
            result = publish_to_postforme(caption, social_accounts, image_urls)
        except Exception as exc:  # noqa: BLE001
            log.error("Publish request errored for %s content_item %d: %s", brand, post["id"], exc)
            mark_failed(post["id"], f"publish request errored: {exc}")
            continue

        body = result["body"]
        if 200 <= result["status_code"] < 300 and body.get("id"):
            mark_published(post["id"], body)
            log.info("Published %s content_item %d -> %s", brand, post["id"], body.get("id"))
        else:
            log.error("Publish failed for %s content_item %d: %s", brand, post["id"], body)
            mark_failed(post["id"], f"Post for Me returned {result['status_code']}", body)


def main() -> None:
    for brand_cfg in config.BRANDS.values():
        try:
            process_brand(brand_cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("Unhandled error publishing for brand %s: %s", brand_cfg["brand"], exc)
    log.info("Stage 2 complete.")


if __name__ == "__main__":
    main()
