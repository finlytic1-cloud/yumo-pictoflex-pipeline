#!/usr/bin/env python3
"""
Stage 3: Pull performance metrics for recently published posts.

Run daily (e.g. Render Cron Job at 20:00), after posts have had time to
accumulate some views.

NOTE: Post for Me's account-feed metrics require the "feeds" permission
to have been granted when each social account was connected. If a
connected account was added before this was requested, its feed calls
may come back empty - reconnect/re-authorize that account in the Post
for Me dashboard with feeds access if metrics stay empty here.

Field names below (impressions, reach, likes, ...) are a best-effort
mapping based on Post for Me's docs and may need small adjustments once
compared against real feed data - check Post for Me's docs if a brand's
numbers look consistently wrong or missing.
"""
import json

import requests

import config
import db
from utils import log, retry, TransientHTTPError


def get_recent_published_posts() -> list[dict]:
    return db.fetch_all(
        """
        SELECT id, brand, external_post_ids FROM content_items
        WHERE status = 'published' AND published_at > now() - interval '14 days';
        """
    )


def extract_post_account_pairs(posts: list[dict]) -> list[dict]:
    pairs = []
    for post in posts:
        ext = post.get("external_post_ids")
        if isinstance(ext, str):
            ext = json.loads(ext) if ext else {}
        ext = ext or {}
        social_accounts = ext.get("social_accounts") or []
        for acct in social_accounts:
            pairs.append({
                "content_item_id": post["id"],
                "brand": post["brand"],
                "external_post_id": ext.get("id"),
                "social_account_id": acct.get("id") or acct.get("social_account_id") or acct,
                "platform": acct.get("platform") if isinstance(acct, dict) else None,
            })
    return pairs


@retry(times=3, base_delay=3.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def fetch_account_feed(social_account_id: str) -> dict:
    resp = requests.get(
        f"https://api.postforme.dev/v1/social-account-feeds/{social_account_id}",
        headers={"Authorization": f"Bearer {config.POSTFORME_API_KEY}"},
        params={"expand": "metrics"},
        timeout=60,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Post for Me feed {resp.status_code}: {resp.text[:300]}")
    if resp.status_code != 200:
        return {"error": resp.status_code, "body": resp.text[:500]}
    return resp.json()


def match_and_extract_metrics(feed: dict, external_post_id: str | None) -> dict | None:
    items = feed.get("data") or feed.get("items") or feed.get("posts") or []
    if isinstance(items, dict):
        items = items.get("items", [])
    for item in items:
        item_id = item.get("id") or item.get("post_id")
        if external_post_id and item_id == external_post_id:
            metrics = item.get("metrics") or {}
            return {
                "impressions": metrics.get("impressions") or metrics.get("views"),
                "reach": metrics.get("reach"),
                "likes": metrics.get("likes"),
                "comments": metrics.get("comments"),
                "shares": metrics.get("shares"),
                "saves": metrics.get("saves"),
                "profile_visits": metrics.get("profile_visits"),
                "link_clicks": metrics.get("link_clicks"),
                "raw": metrics,
            }
    return None


def insert_metrics(pair: dict, metrics: dict) -> None:
    db.execute(
        """
        INSERT INTO performance_metrics
            (content_item_id, brand, social_account_id, platform, impressions, reach, likes,
             comments, shares, saves, profile_visits, link_clicks, raw_metrics)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """,
        (
            pair["content_item_id"], pair["brand"], pair["social_account_id"], pair.get("platform"),
            metrics.get("impressions"), metrics.get("reach"), metrics.get("likes"),
            metrics.get("comments"), metrics.get("shares"), metrics.get("saves"),
            metrics.get("profile_visits"), metrics.get("link_clicks"),
            json.dumps(metrics.get("raw", {})),
        ),
    )


def main() -> None:
    posts = get_recent_published_posts()
    pairs = extract_post_account_pairs(posts)
    log.info("Stage 3: checking metrics for %d (post, account) pairs", len(pairs))

    found_count = 0
    for pair in pairs:
        try:
            feed = fetch_account_feed(pair["social_account_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Feed fetch failed for account %s: %s", pair["social_account_id"], exc)
            continue

        if "error" in feed:
            log.warning("Feed error for account %s: %s", pair["social_account_id"], feed)
            continue

        metrics = match_and_extract_metrics(feed, pair.get("external_post_id"))
        if metrics is None:
            continue

        try:
            insert_metrics(pair, metrics)
            found_count += 1
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to insert metrics for content_item %d: %s", pair["content_item_id"], exc)

    log.info("Stage 3 complete. Recorded metrics for %d/%d pairs.", found_count, len(pairs))


if __name__ == "__main__":
    main()
