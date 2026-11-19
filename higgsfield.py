"""
Higgsfield API client: makes the short cinematic opener clip.

Optional by design. If the keys aren't set, the estimate is over budget, or
the request fails, the caller just makes the video without an opener.

API docs: https://docs.higgsfield.ai
  submit:   POST https://api.higgsfield.ai/{model path}   (JSON body)
  status:   GET  {status_url}  -> status queued|in_progress|completed|failed|nsfw|canceled
  output:   {"video": {"url": ...}}
  estimate: POST https://api.higgsfield.ai/estimate/{model path} -> {"usd": "..."}
"""
import json
import time

import requests

import config
from utils import log

BASE = "https://api.higgsfield.ai"


def enabled() -> bool:
    return bool(config.HIGGSFIELD_KEY_ID and config.HIGGSFIELD_KEY_SECRET)


def _headers() -> dict:
    return {
        "Authorization": f"Key {config.HIGGSFIELD_KEY_ID}:{config.HIGGSFIELD_KEY_SECRET}",
        "Content-Type": "application/json",
    }


def _body(prompt: str) -> dict:
    body = {"prompt": prompt}
    body.update(json.loads(config.HIGGSFIELD_OPENER_PARAMS or "{}"))
    return body


def estimate_usd(model_path: str, body: dict) -> float | None:
    resp = requests.post(f"{BASE}/estimate/{model_path}", headers=_headers(), json=body, timeout=30)
    if resp.status_code != 200:
        log.warning("Higgsfield estimate %s: %s", resp.status_code, resp.text[:500])
        return None
    try:
        return float(resp.json().get("usd"))
    except (TypeError, ValueError):
        return None


def generate_opener(prompt: str, out_path: str) -> str | None:
    """Generate the opener clip and save it to out_path. Returns the path, or None."""
    if not enabled():
        log.info("Higgsfield keys not set - skipping opener clip.")
        return None
    model = config.HIGGSFIELD_OPENER_MODEL
    body = _body(prompt)

    usd = estimate_usd(model, body)
    log.info("Higgsfield opener estimate for %s: %s USD", model, usd)
    if usd is not None and usd > config.HIGGSFIELD_MAX_USD_PER_VIDEO:
        log.warning("Opener costs %.2f USD, over the %.2f limit - skipping.", usd, config.HIGGSFIELD_MAX_USD_PER_VIDEO)
        return None

    resp = requests.post(f"{BASE}/{model}", headers=_headers(), json=body, timeout=60)
    if resp.status_code >= 300:
        # Logged in full so a wrong model path or parameter is easy to fix from the Render logs.
        log.error("Higgsfield submit %s: %s", resp.status_code, resp.text[:1000])
        return None
    job = resp.json()
    status_url = job.get("status_url") or f"{BASE}/requests/{job.get('request_id')}/status"

    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(10)
        st = requests.get(status_url, headers=_headers(), timeout=30)
        if st.status_code != 200:
            log.warning("Higgsfield status %s: %s", st.status_code, st.text[:300])
            continue
        data = st.json()
        status = data.get("status")
        if status == "completed":
            url = (data.get("video") or {}).get("url")
            if not url:
                log.error("Higgsfield finished but returned no video url: %s", str(data)[:500])
                return None
            video = requests.get(url, timeout=120)
            video.raise_for_status()
            with open(out_path, "wb") as fh:
                fh.write(video.content)
            log.info("Higgsfield opener saved (%d KB)", len(video.content) // 1024)
            return out_path
        if status in ("failed", "nsfw", "canceled"):
            log.error("Higgsfield job ended with %s: %s", status, data.get("error"))
            return None
    log.error("Higgsfield job timed out.")
    return None
