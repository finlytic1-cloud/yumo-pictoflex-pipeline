"""
Shared configuration for the Pictoflex Labs content pipeline.

One Reel a day: Claude writes an automation workflow for a business type,
it's animated in code with matching sound design, an optional Higgsfield
cinematic opener goes in front, and Post for Me publishes it everywhere.

All secrets come from environment variables - nothing is hardcoded.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def env_list(name: str) -> list[str]:
    return [a.strip() for a in env(name, "").split(",") if a.strip()]


# ---------------------------------------------------------------------------
# Database + storage (Supabase)
# ---------------------------------------------------------------------------
DB_HOST = env("SUPABASE_DB_HOST", required=True)
DB_PORT = env("SUPABASE_DB_PORT", "5432")
DB_USER = env("SUPABASE_DB_USER", required=True)
DB_PASSWORD = env("SUPABASE_DB_PASSWORD", required=True)
DB_NAME = env("SUPABASE_DB_NAME", "postgres")

SUPABASE_URL = env("SUPABASE_URL", required=True)
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", required=True)
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET", "carousel-images")

# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", required=True)
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MODEL_FAST = env("CLAUDE_MODEL_FAST", CLAUDE_MODEL)   # used for QC only

# ---------------------------------------------------------------------------
# Higgsfield (optional cinematic opener). Leave the keys empty to skip it.
# ---------------------------------------------------------------------------
HIGGSFIELD_KEY_ID = env("HIGGSFIELD_KEY_ID", "").strip().strip('"').strip("'")
HIGGSFIELD_KEY_SECRET = env("HIGGSFIELD_KEY_SECRET", "").strip().strip('"').strip("'")
# Also accept the whole key pasted as one "id:secret" value (in either variable, or HIGGSFIELD_KEY).
_combined = env("HIGGSFIELD_KEY", "").strip()
for _v in (_combined, HIGGSFIELD_KEY_ID, HIGGSFIELD_KEY_SECRET):
    if ":" in _v:
        HIGGSFIELD_KEY_ID, HIGGSFIELD_KEY_SECRET = _v.split(":", 1)
        break
HIGGSFIELD_OPENER_MODEL = env("HIGGSFIELD_OPENER_MODEL", "bytedance/seedance-2.5/text-to-video")
# Extra JSON parameters sent with the prompt. Field names follow the model's docs page.
HIGGSFIELD_OPENER_PARAMS = env(
    "HIGGSFIELD_OPENER_PARAMS",
    '{"duration": 4, "aspect_ratio": "9:16", "resolution": "720p", "generate_audio": false}',
)
# Safety cap: skip the opener if Higgsfield's own estimate is above this.
HIGGSFIELD_MAX_USD_PER_VIDEO = float(env("HIGGSFIELD_MAX_USD_PER_VIDEO", "1.00"))

# ---------------------------------------------------------------------------
# Voiceover (ElevenLabs). Leave the key empty to post with sound design only.
# ---------------------------------------------------------------------------
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")   # "George": warm British narrator
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# ---------------------------------------------------------------------------
# Post for Me
# ---------------------------------------------------------------------------
POSTFORME_API_KEY = env("POSTFORME_API_KEY", required=True)
# Every Pictoflex account (Instagram, TikTok, Facebook, LinkedIn page), comma-separated spc_... ids.
# Falls back to the old two variables so existing Render settings keep working.
POSTFORME_ACCOUNTS = env_list("POSTFORME_ACCOUNTS_PICTOFLEX") + env_list("POSTFORME_REEL_ACCOUNTS_PICTOFLEX")

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------
QC_PASS_THRESHOLD = float(env("QC_PASS_THRESHOLD", "70"))
MUSIC_DIR = env("MUSIC_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "music"))

# ---------------------------------------------------------------------------
# Who the videos are for. Alternates daily between the two lists, and avoids
# any industry used in the last 14 days.
# ---------------------------------------------------------------------------
LOCAL_INDUSTRIES = [
    "dry cleaner", "takeaway restaurant", "barber shop", "dental practice", "plumber",
    "independent gym", "beauty salon", "mobile tyre fitter", "estate agent", "driving school",
    "physiotherapy clinic", "car garage / MOT centre", "cleaning company", "florist", "tutoring centre",
]
HIGH_END_INDUSTRIES = [
    "law firm", "accounting firm", "private medical clinic", "e-commerce brand", "recruitment agency",
    "property developer", "wealth management firm", "architecture studio", "luxury car dealership",
    "boutique hotel", "private school", "marketing agency", "aesthetics clinic", "letting agency with 500 units",
]

PICTOFLEX_FACTS = """
BRAND: Pictoflex Labs (pictoflexlabs.co.uk), a lean UK studio that builds websites, custom software
and AI automations for businesses. What makes it different: it builds the COMPLETE system, end to end,
not a single chatbot or a template. Client owns 100% of the code. Fixed price agreed before work starts.
Free audit and discovery call (link in bio).

Never invent client names, testimonials, statistics or results. Every video shows an EXAMPLE workflow
for a type of business, labelled on screen as an example - so numbers in it must be realistic for that
business type and framed as what such a system can do, never as a named client's real results.
""".strip()

SPEC_INSTRUCTIONS = """
Design ONE automation workflow for a {industry} and describe it as JSON for an animated vertical video.
Audience: owners of {audience}. The feeling to create: "my competitors have this and I don't".
Show how far a complete system goes (several connected steps, parallel branches, real outcomes),
in plain English with NO tool or software names.

Layout rules (the animation engine depends on them):
- "row" numbers go 0..6 (5 to 7 rows total). Each row has 1 to 3 nodes.
- Row 0 has exactly ONE node, kind "trigger". The last row has exactly ONE node, kind "outcome".
- Include at least one row with 2 or 3 nodes (a branch) and later merge back to 1 node.
- kinds: "trigger", "ai" (AI understanding/deciding), "action", "outcome" (a business result).
- 2 or 3 outcome nodes have "value": a realistic whole-number £ amount of revenue or fees that outcome
  brings the business in one night/day (fees and bookings, NOT property or deal values). Keep totals modest
  and believable for one night - typically £100-£2,000.
- Edges only connect a node in row r to a node in row r+1. Every node except row 0 has an incoming edge;
  every node except the last row has an outgoing edge.
- label: max 30 characters. sub: max 32 characters. Short, concrete, punchy.

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
 "industry": "{industry}",
 "hook": "max 45 chars, sets a scene that hurts, e.g. It's 11:42pm. Your clinic is closed.",
 "overview_line": "max 30 chars",
 "counter_label": "max 30 chars, what the £ counter means, e.g. Booked while you slept",
 "nodes": [{{"id": "short_id", "row": 0, "kind": "trigger", "label": "...", "sub": "...", "value": 0}}],
 "edges": [["from_id", "to_id"]],
 "results_title": "max 28 chars, e.g. What you wake up to",
 "notifications": [{{"type": "booking|money|review", "title": "max 24 chars", "body": "max 38 chars"}}],
 "cta": "max 70 chars, FOMO + free audit, link in bio, no url",
 "caption": "ONE sentence, max 20 words, no hashtags, no url",
 "hashtags": "3-5 hashtags",
 "opener_prompt": "A 4-second cinematic vertical shot that sets the mood for the hook, inside the {industry}'s own world. Pick ONE striking, specific, unexpected image (e.g. rain streaking down a dark shopfront window, a single desk lamp left on in an empty office, steam rising off a coffee in a quiet kitchen at dawn, keys swinging on a hook). Moody, premium, shallow depth of field, slow camera move. NEVER show phones, laptops, screens, devices, people's faces, text or logos.",
 "voiceover": {{
   "hook": "spoken over the opening, max 10 words, matches the hook",
   "build": "spoken while the workflow builds, max 16 words, what the system does",
   "reveal": "spoken as the whole system is revealed, max 8 words",
   "results": "spoken over the phone notifications, max 12 words",
   "cta": "spoken on the logo, max 10 words, ends with: link in bio"
 }}
}}
Use 3 or 4 notifications. NEVER use people's names (no "Sarah T." etc.) anywhere - say "a new client", "a buyer".
Voiceover: calm, confident British narrator, plain English, second person ("you"), no tool names.
""".strip()

QC_INSTRUCTIONS = """
Score this workflow video plan 0-10 on: hook_strength, clarity (would a busy owner instantly get it),
satisfying_flow (branches, build-up, payoff), believability (numbers realistic for the business type,
no invented clients or claims), fomo (makes them feel behind). Return ONLY JSON:
{"hook_strength": 0, "clarity": 0, "satisfying_flow": 0, "believability": 0, "fomo": 0, "notes": "one line"}
""".strip()
