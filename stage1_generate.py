#!/usr/bin/env python3
"""
Stage 1: make today's Pictoflex Reel.

Run daily (Render Cron Job, e.g. 07:00 UTC):
  1. Pick today's business type (alternates local / high-end, no repeats in 14 days).
  2. Claude designs an automation workflow for it as JSON (the video "script").
  3. Python checks the layout rules and fixes small problems; a cheaper model scores it.
     Below the bar -> one rewrite with the feedback.
  4. Optional: Higgsfield makes a short cinematic opener.
  5. The workflow is animated in code, with sound design and optional music.
  6. The MP4 is uploaded to Supabase Storage and saved as status 'rendered' for Stage 2.

Failures are recorded on the content_items row (status 'failed' + error_message)
rather than crashing silently.
"""
import datetime as dt
import json
import os
import random
import tempfile

import requests

import assemble
import config
import db
import higgsfield
from utils import anthropic_call, extract_json, log, retry, TransientHTTPError

BRAND = "pictoflex"


# ---------------------------------------------------------------------------
# What to make today
# ---------------------------------------------------------------------------

def recent_industries() -> set[str]:
    rows = db.fetch_all(
        "SELECT content_pillar FROM content_items WHERE brand = %s AND created_at > now() - interval '14 days';",
        (BRAND,),
    )
    return {r["content_pillar"] for r in rows if r.get("content_pillar")}


def pick_industry() -> tuple[str, str]:
    high_end = dt.date.today().toordinal() % 2 == 0
    pool = config.HIGH_END_INDUSTRIES if high_end else config.LOCAL_INDUSTRIES
    audience = "established, higher-revenue businesses" if high_end else "local small businesses in the UK"
    used = recent_industries()
    choices = [i for i in pool if i not in used] or pool
    return random.choice(choices), audience


# ---------------------------------------------------------------------------
# The workflow "script"
# ---------------------------------------------------------------------------

def system_prompt() -> str:
    return f"You are the creative director for Pictoflex Labs' short-form video.\n\n{config.PICTOFLEX_FACTS}"


def write_spec(industry: str, audience: str, feedback: str | None = None) -> dict:
    user = config.SPEC_INSTRUCTIONS.format(industry=industry, audience=audience)
    if feedback:
        user += f"\n\nA reviewer scored the previous version low. Fix this: {feedback}"
    raw = anthropic_call(system_prompt(), user, max_tokens=3000)
    return extract_json(raw)


def _cut(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def validate_spec(spec: dict) -> dict:
    """Enforce the layout rules the animation depends on. Raises ValueError if unusable."""
    nodes = spec.get("nodes") or []
    if len(nodes) < 5:
        raise ValueError("fewer than 5 nodes")
    ids = set()
    for n in nodes:
        n["id"] = str(n["id"])
        n["row"] = int(n["row"])
        n["kind"] = n.get("kind") if n.get("kind") in ("trigger", "ai", "action", "outcome") else "action"
        n["label"] = _cut(n.get("label"), 30)
        n["sub"] = _cut(n.get("sub"), 32)
        v = n.get("value")
        n["value"] = int(v) if isinstance(v, (int, float)) and v > 0 else None
        if not n["value"]:
            n.pop("value")
        ids.add(n["id"])
    # renumber rows 0..k so gaps don't break the layout
    rows = sorted({n["row"] for n in nodes})
    remap = {r: i for i, r in enumerate(rows)}
    for n in nodes:
        n["row"] = remap[n["row"]]
    if len(rows) < 4 or len(rows) > 8:
        raise ValueError(f"{len(rows)} rows (need 4-8)")
    per_row = {}
    for n in nodes:
        per_row.setdefault(n["row"], []).append(n)
    if any(len(v) > 3 for v in per_row.values()):
        raise ValueError("a row has more than 3 nodes")
    row_of = {n["id"]: n["row"] for n in nodes}
    edges = []
    for e in spec.get("edges") or []:
        a, b = str(e[0]), str(e[1])
        if a in ids and b in ids and row_of[b] == row_of[a] + 1:
            edges.append((a, b))
    # make sure every node is connected in and out
    last = len(rows) - 1
    for n in nodes:
        r = n["row"]
        if r > 0 and not any(b == n["id"] for _, b in edges):
            edges.append((per_row[r - 1][0]["id"], n["id"]))
        if r < last and not any(a == n["id"] for a, _ in edges):
            edges.append((n["id"], per_row[r + 1][0]["id"]))
    spec["edges"] = edges
    spec["nodes"] = nodes
    spec["hook"] = _cut(spec.get("hook"), 48)
    spec["overview_line"] = _cut(spec.get("overview_line") or "One system. Running 24/7.", 32)
    spec["counter_label"] = _cut(spec.get("counter_label"), 32)
    spec["results_title"] = _cut(spec.get("results_title") or "What you wake up to", 30)
    spec["cta"] = _cut(spec.get("cta") or "Free automation audit. Link in bio.", 80)
    notes = spec.get("notifications") or []
    spec["notifications"] = [
        {"type": x.get("type", "booking"), "title": _cut(x.get("title"), 26), "body": _cut(x.get("body"), 40)}
        for x in notes[:4]
    ]
    if not any(n.get("value") for n in nodes):
        spec.pop("counter_label", None)
    spec["tag"] = "Example automation"
    vo = spec.get("voiceover") if isinstance(spec.get("voiceover"), dict) else {}
    spec["voiceover"] = {k: _cut(vo.get(k), 120) for k in ("hook", "build", "reveal", "results", "cta") if vo.get(k)}
    # a different look each day: colour theme + layout
    import workflow_video
    spec["theme"] = random.choice(list(workflow_video.THEMES))
    spec["layout"] = random.choice(["stack", "zigzag"])
    return spec


def qc(spec: dict) -> tuple[float, str]:
    """Score the plan. A broken QC call never blocks the day's video - it just passes."""
    try:
        raw = anthropic_call(system_prompt(), f"{config.QC_INSTRUCTIONS}\n\nPlan:\n{json.dumps(spec)}",
                             max_tokens=2000, model=config.CLAUDE_MODEL_FAST)
        s = extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("QC call failed, skipping QC: %s", str(exc)[:300])
        return config.QC_PASS_THRESHOLD, "QC unavailable"
    keys = ["hook_strength", "clarity", "satisfying_flow", "believability", "fomo"]
    score = sum(float(s.get(k, 0)) for k in keys) / len(keys) * 10   # computed here, not by the model
    return score, s.get("notes", "")


def written_and_checked(industry: str, audience: str, feedback: str | None = None) -> dict:
    """Ask Claude for the workflow; if it breaks a layout rule, ask again with the error (max 3 tries)."""
    for attempt in range(3):
        try:
            return validate_spec(write_spec(industry, audience, feedback=feedback))
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("Workflow plan rejected (attempt %d): %s", attempt + 1, exc)
            feedback = f"Your last plan broke a layout rule: {exc}. Follow the layout rules exactly."
    raise ValueError("Claude could not produce a valid workflow plan in 3 attempts")


def make_spec() -> tuple[dict, float]:
    industry, audience = pick_industry()
    log.info("Today's business type: %s (%s)", industry, audience)
    spec = written_and_checked(industry, audience)
    score, notes = qc(spec)
    log.info("QC score %.0f - %s", score, notes)
    if score < config.QC_PASS_THRESHOLD:
        spec = written_and_checked(industry, audience, feedback=notes)
        score, notes = qc(spec)
        log.info("QC score after rewrite %.0f - %s", score, notes)
    spec["industry"] = industry
    log.info("Look: %s theme, %s layout", spec["theme"], spec["layout"])
    return spec, score


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@retry(times=3, base_delay=2.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def upload_to_supabase(file_path: str, data: bytes, content_type: str) -> str:
    resp = requests.post(
        f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_STORAGE_BUCKET}/{file_path}",
        headers={
            "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
        data=data,
        timeout=120,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Supabase upload {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_STORAGE_BUCKET}/{file_path}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    spec, score = make_spec()
    row = db.execute(
        """
        INSERT INTO content_items
            (brand, content_pillar, objective, hook, platform_target, caption, cta, hashtags,
             visual_spec, quality_score, status, attempt_number)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved', 1) RETURNING id;
        """,
        (BRAND, spec["industry"], "workflow_reel", spec["hook"], "instagram,tiktok,facebook,linkedin",
         _cut(spec.get("caption"), 160), spec["cta"], spec.get("hashtags"),
         json.dumps(spec), json.dumps({"weighted_total": round(score)})),
        returning=True,
    )
    item_id = row["id"]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "reel.mp4")
            clip = None
            if config.VIDEO_MODE == "cinematic" and spec.get("cinematic_prompt"):
                import cinematic
                try:
                    clip = higgsfield.generate_workflow_clip(cinematic.video_prompt(spec), cinematic.clip_seconds(spec),
                                                             os.path.join(tmp, "workflow.mp4"))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Higgsfield clip failed, falling back to the animated version: %s", exc)
            if clip:
                assemble.build_cinematic(spec, clip, out, music_dir=config.MUSIC_DIR)
            else:
                assemble.build_reel(spec, out, music_dir=config.MUSIC_DIR)
            with open(out, "rb") as fh:
                data = fh.read()
            url = upload_to_supabase(f"{BRAND}/{item_id}/reel.mp4", data, "video/mp4")
        db.execute("UPDATE content_items SET status = 'rendered', image_urls = %s WHERE id = %s;",
                   (json.dumps([url]), item_id))
        log.info("Reel %d ready (%d KB, cinematic: %s): %s", item_id, len(data) // 1024, bool(clip), url)
    except Exception as exc:  # noqa: BLE001
        log.error("Rendering failed for content_item %d: %s", item_id, exc)
        db.execute("UPDATE content_items SET status = 'failed', error_message = %s WHERE id = %s;",
                   (str(exc)[:2000], item_id))
        raise


if __name__ == "__main__":
    main()
