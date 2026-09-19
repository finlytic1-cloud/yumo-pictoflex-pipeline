#!/usr/bin/env python3
"""
Stage 2: Publish rendered posts to Instagram + TikTok via Post for Me.

Run daily (e.g. Render Cron Job at 10:00), a few hours after Stage 1.

For each brand: publish every post that's rendered and ready (within the
lookback window), best quality_score first. No daily-count cap - Stage 1
is what controls volume (it only ever generates 3/brand/day), so Stage 2
just publishes whatever Stage 1 produced. A generous safety limit still
applies purely to stop a runaway bug from mass-publishing, not as a
day-to-day business rule.
"""
import json

import requests

import config
import db
from utils import log, retry, TransientHTTPError


def get_posts_to_publish(brand: str) -> list[dict]:
    return db.fetch_all(
        """
        SELECT id, hook, caption, hashtags, image_urls
        FROM content_items
        WHERE brand = %s AND status = 'rendered' AND created_at > now() - interval '18 hours'
        ORDER BY (quality_score->>'weighted_total')::numeric DESC NULLS LAST
        LIMIT 50;
        """,
        (brand,),
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

    log.info("=== Stage 2: %s ===", brand)

    posts = get_posts_to_publish(brand)
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
