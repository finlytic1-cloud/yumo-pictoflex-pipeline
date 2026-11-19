#!/usr/bin/env python3
"""
Stage 2: publish today's Pictoflex Reel via Post for Me.

Run daily (Render Cron Job, e.g. 12:00 UTC), after Stage 1. Publishes every
'rendered' Reel from the last 18 hours (normally exactly one) as a video to
every Pictoflex account. Instagram and Facebook get it as a Reel.
"""
import json

import requests

import config
import db
from utils import log, retry, TransientHTTPError

BRAND = "pictoflex"


def get_posts_to_publish() -> list[dict]:
    return db.fetch_all(
        """
        SELECT id, caption, hashtags, image_urls FROM content_items
        WHERE brand = %s AND status = 'rendered' AND created_at > now() - interval '18 hours'
        ORDER BY created_at LIMIT 3;
        """,
        (BRAND,),
    )


@retry(times=3, base_delay=3.0, exceptions=(TransientHTTPError, requests.exceptions.ConnectionError,
                                             requests.exceptions.Timeout))
def publish(caption: str, video_url: str) -> dict:
    resp = requests.post(
        "https://api.postforme.dev/v1/social-posts",
        headers={"Authorization": f"Bearer {config.POSTFORME_API_KEY}", "Content-Type": "application/json"},
        json={
            "caption": caption,
            "social_accounts": config.POSTFORME_ACCOUNTS,
            "media": [{"url": video_url}],
            "platform_configurations": {"instagram": {"placement": "reels"}, "facebook": {"placement": "reels"}},
        },
        timeout=60,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Post for Me {resp.status_code}: {resp.text[:500]}")
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:1000]}
    return {"status_code": resp.status_code, "body": body}


def main() -> None:
    if not config.POSTFORME_ACCOUNTS:
        log.warning("No Pictoflex Post for Me accounts configured - nothing to do.")
        return
    for post in get_posts_to_publish():
        urls = post["image_urls"] if isinstance(post["image_urls"], list) else json.loads(post["image_urls"] or "[]")
        if not urls:
            db.execute("UPDATE content_items SET status='failed', error_message='no video url' WHERE id=%s;", (post["id"],))
            continue
        caption = f"{post.get('caption') or ''}\n\n{post.get('hashtags') or ''}".strip()
        try:
            result = publish(caption, urls[0])
        except Exception as exc:  # noqa: BLE001
            result = {"status_code": 0, "body": {"error": str(exc)}}
        if 200 <= result["status_code"] < 300 and result["body"].get("id"):
            db.execute("UPDATE content_items SET status='published', published_at=now(), external_post_ids=%s WHERE id=%s;",
                       (json.dumps(result["body"]), post["id"]))
            log.info("Published Reel %d -> %s", post["id"], result["body"].get("id"))
        else:
            db.execute("UPDATE content_items SET status='failed', error_message=%s, external_post_ids=%s WHERE id=%s;",
                       (f"Post for Me returned {result['status_code']}", json.dumps(result["body"]), post["id"]))
            log.error("Publish failed for Reel %d: %s", post["id"], result)
    log.info("Stage 2 complete.")


if __name__ == "__main__":
    main()
