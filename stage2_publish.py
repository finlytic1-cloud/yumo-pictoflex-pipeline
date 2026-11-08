#!/usr/bin/env python3
"""
Stage 2: Publish rendered posts via Post for Me (image carousels, plus vertical
video Reels to Instagram/Facebook for brands that have reel accounts).

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
def publish_to_postforme(caption: str, social_accounts: list[str], media_urls: list[str],
                         platform_configurations: dict | None = None) -> dict:
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
            **({"platform_configurations": platform_configurations} if platform_configurations else {}),
        },
        timeout=60,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Post for Me {resp.status_code}: {resp.text[:500]}")
    # Don't raise_for_status() here - a 4xx (e.g. TikTok's active-user cap,
    # or an account issue) should be recorded on the post, not retried
    # forever or treated as a crash.
    return {"status_code": resp.status_code, "body": _safe_json(resp)}


def reel_url(brand: str, content_item_id: int) -> str:
    return (f"{config.SUPABASE_URL}/storage/v1/object/public/"
            f"{config.SUPABASE_STORAGE_BUCKET}/{brand}/{content_item_id}/reel.mp4")


def reel_exists(url: str) -> bool:
    try:
        return requests.head(url, timeout=20, allow_redirects=True).status_code == 200
    except requests.exceptions.RequestException:
        return False


def _ok(result: dict) -> bool:
    return 200 <= result["status_code"] < 300 and bool(result["body"].get("id"))


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
    reel_accounts = brand_cfg.get("reel_accounts", [])
    if not social_accounts and not reel_accounts:
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

        # Decide what goes where: Reel (video) to reel accounts if a video exists,
        # image carousel to everything else. Without a video, reel accounts just
        # get the image carousel like before.
        video = reel_url(brand, post["id"])
        use_reel = bool(reel_accounts) and reel_exists(video)
        jobs = []  # (label, accounts, media, platform_configurations)
        if use_reel:
            if social_accounts:
                jobs.append(("images", social_accounts, image_urls, None))
            jobs.append(("reel", reel_accounts, [video],
                         {"instagram": {"placement": "reels"}, "facebook": {"placement": "reels"}}))
        else:
            jobs.append(("images", list(dict.fromkeys(social_accounts + reel_accounts)), image_urls, None))

        results = {}
        for label, accounts, media, cfgs in jobs:
            try:
                results[label] = publish_to_postforme(caption, accounts, media, cfgs)
            except Exception as exc:  # noqa: BLE001
                log.error("%s publish request errored for %s content_item %d: %s", label, brand, post["id"], exc)
                results[label] = {"status_code": 0, "body": {"error": str(exc)}}

        ok = {k: v for k, v in results.items() if _ok(v)}
        if ok:
            # Keep the primary response at the top level (Stage 3 reads it);
            # attach the other request's response under its own key.
            primary_key = "images" if "images" in ok else next(iter(ok))
            body = dict(ok[primary_key]["body"])
            for k, v in results.items():
                if k != primary_key:
                    body[f"{k}_response"] = v["body"]
            mark_published(post["id"], body)
            log.info("Published %s content_item %d -> %s", brand, post["id"],
                     ", ".join(f"{k}={v['body'].get('id')}" for k, v in ok.items()))
            for k, v in results.items():
                if k not in ok:
                    log.error("PARTIAL: %s request failed for %s content_item %d: %s", k, brand, post["id"], v)
        else:
            log.error("Publish failed for %s content_item %d: %s", brand, post["id"], results)
            first = next(iter(results.values()))
            mark_failed(post["id"], f"Post for Me returned {first['status_code']}", first["body"])


def main() -> None:
    for brand_cfg in config.BRANDS.values():
        try:
            process_brand(brand_cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("Unhandled error publishing for brand %s: %s", brand_cfg["brand"], exc)
    log.info("Stage 2 complete.")


if __name__ == "__main__":
    main()
