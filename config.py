"""
Shared configuration for the Yumo + Pictoflex content pipeline.

Everything that used to be scattered across n8n nodes (brand facts,
carousel formatting rules, the "exactly 3 posts" instruction, QC
thresholds) lives here in one place so it's easy to tweak without
digging through multiple files.

All secrets come from environment variables — nothing is hardcoded.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_HOST = env("SUPABASE_DB_HOST", required=True)
DB_PORT = env("SUPABASE_DB_PORT", "5432")
DB_USER = env("SUPABASE_DB_USER", required=True)
DB_PASSWORD = env("SUPABASE_DB_PASSWORD", required=True)
DB_NAME = env("SUPABASE_DB_NAME", "postgres")

# ---------------------------------------------------------------------------
# Supabase Storage
# ---------------------------------------------------------------------------
SUPABASE_URL = env("SUPABASE_URL", required=True)
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", required=True)
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET", "carousel-images")

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", required=True)
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-sonnet-4-6")

# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------
BRAVE_API_KEY = env("BRAVE_API_KEY", required=True)

# ---------------------------------------------------------------------------
# Imejis
# ---------------------------------------------------------------------------
IMEJIS_API_KEY = env("IMEJIS_API_KEY", required=True)

# Slide 1 ("hook slide") is rendered on a separate photo template shared by
# both brands - a real photo with the post's hook text overlaid on it, used
# as the scroll-stopper. Slides 2+ keep using each brand's normal
# IMEJIS_TEMPLATE_ID_* template. Optional: if unset, all slides fall back to
# the brand's normal template as before.
HOOK_IMEJIS_TEMPLATE_ID = env("HOOK_IMEJIS_TEMPLATE_ID", "")
HOOK_IMEJIS_TEXT_FIELD = env("HOOK_IMEJIS_TEXT_FIELD", "")

# ---------------------------------------------------------------------------
# Post for Me
# ---------------------------------------------------------------------------
POSTFORME_API_KEY = env("POSTFORME_API_KEY", required=True)

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------
POSTS_PER_BRAND_PER_DAY = int(env("POSTS_PER_BRAND_PER_DAY", "3"))
QC_PASS_THRESHOLD = float(env("QC_PASS_THRESHOLD", "70"))
QC_MIN_FACTUAL_ACCURACY = float(env("QC_MIN_FACTUAL_ACCURACY", "8"))
QC_MIN_REPETITION_SCORE = float(env("QC_MIN_REPETITION_SCORE", "6"))
MAX_GENERATION_ATTEMPTS = int(env("MAX_GENERATION_ATTEMPTS", "2"))

# ---------------------------------------------------------------------------
# Carousel formatting rules — shared by both brands.
# These encode the user's explicit feedback:
#   - no final "visit our website" slide with a link (bio has the link)
#   - slides must be short / compact, not paragraphs
#   - firm "exactly 3 posts" volume requirement
# ---------------------------------------------------------------------------
CAROUSEL_FORMAT_RULES = """
CAROUSEL FORMAT RULES (important - read carefully):
- Each slide is text overlaid on an image, viewed for 1-2 seconds while scrolling. Every slide must be SHORT: one punchy line or at most two short lines. Never write a paragraph on a slide. If you find yourself writing more than about 15-20 words for a single slide, cut it down or split the idea differently - compact and sweet, not dense.
- Do NOT include a final "visit our website/download now" slide with a link (e.g. do not end with "yumoapp.co" as a slide). The account bio already has the link - carousels should end on a strong closing thought, a punchy one-liner, or a short call-to-action phrase with NO url attached. The caption (separate field) is where the link-related CTA belongs, not the image slides.
- Aim for 3-4 slides total per post (hook + 1-2 content slides + a short closing line) rather than a fixed 5 - whatever best fits the idea without padding.
""".strip()

GENERATION_TASK_INSTRUCTION = """
Generate exactly 3 structured carousel posts, each on a genuinely different content_pillar. All 3 must be complete and ready to publish - do not submit a weak or placeholder post just to hit the count, but you must produce 3 real, distinct, well-crafted ideas every time, not fewer.

Return ONLY valid JSON, no markdown code fences, no commentary, in exactly this shape:
{"posts": [{"content_pillar": "...", "objective": "...", "target_audience": "...", "hook": "...", "slides": ["short slide 1 text", "short slide 2 text", "..."], "caption": "...", "cta": "...", "hashtags": "...", "visual_spec": "...", "trend_used": "..."}]}
""".strip()


# ---------------------------------------------------------------------------
# Brand facts — source of truth. Do not invent features, testimonials,
# stats, clients or results beyond what's listed here.
# ---------------------------------------------------------------------------
YUMO_FACTS = """
BRAND: Yumo (yumoapp.co)
WHAT IT IS: A consumer/business finance app for young entrepreneurs and resellers/small business owners - a financial operating system that gives founders a clear, real-time picture of their money.

CONFIRMED FEATURES (only mention these - do not invent others):
- Smart Cashflow Dashboard - shows what's coming in, what's going out, and what's actually kept as profit.
- AI Insights that surface spending/revenue patterns automatically.
- Live integrations with Shopify, Stripe and eBay - sales, fees and orders sync automatically.
- AI chat for logging expenses - describe a cost in plain language (postage, packaging, a one-off purchase) and it's logged, no manual entry.
- Free to start, no card required.
- Pro plan: £2.99/mo.
- Founding member: £99.99 one-time. NEVER state an exact number of "spots remaining" - that figure goes stale immediately and must never be quoted.

TESTIMONIALS (use verbatim only, never invent new ones or new people):
- Sarah K.
- Marcus T.
- James R.

APPROVED STAT: "1,000+ young entrepreneurs" - never upgrade or round this number up.

TARGET AUDIENCE: young founders, resellers (eBay/Shopify sellers), side-hustlers and small business owners who are making money but don't have a clear view of what they're actually keeping.

TONE: direct, clear-eyed, a bit provocative about the gap between "revenue" and "what you actually keep." Never salesy or hypey. Speaks like someone who has actually run a small business.

CTA STYLE: "Try Yumo free", "See how Yumo works" - always pointing to the bio link, never a bare url on a slide.
""".strip()

PICTOFLEX_FACTS = """
BRAND: Pictoflex Labs (pictoflexlabs.co.uk)
WHAT IT IS: A lean software development agency building MVPs, mobile apps, websites and AI automation for founders and small businesses, without traditional agency overhead.

CONFIRMED SERVICES AND PRICING (only mention these - do not invent others):
- Website / Landing Page - from £50, 1-2 weeks.
- MVP Development - from £500, 4-6 weeks.
- Mobile App (iOS + Android, built with Flutter) - from £2,000, 8-12 weeks.
- AI Automation - from £2,000, 4-8 weeks.
- Fixed price agreed before work starts. Client owns 100% of the code and IP. Weekly progress updates/sprints.

TESTIMONIALS (use verbatim only, never invent new ones or new people):
- James R.
- Sarah P.
- Amir K.

CASE STUDY: Yumo itself was built by Pictoflex Labs and can be referenced as a real, in-house example of their work.

TARGET AUDIENCE: founders and small business owners who need custom software (an app, an MVP, a website, an automation) but are wary of bloated agency quotes.

TONE: confident, direct, a little contrarian about agency pricing/overhead. Educational - explain the "why" behind software decisions (what an MVP actually is, what a lean team looks like) rather than just pitching.

CTA STYLE: "Book a Free Discovery Call" - always pointing to the bio link, never a bare url on a slide.
""".strip()


BRANDS = {
    "yumo": {
        "brand": "yumo",
        "platform_target": "instagram,tiktok",
        "trend_query": "small business finance app trends founders resellers social media 2026",
        "facts": YUMO_FACTS,
        "imejis_template_id": env("IMEJIS_TEMPLATE_ID_YUMO", required=True),
        "imejis_text_field": env("IMEJIS_TEXT_FIELD_YUMO", required=True),
        "postforme_accounts": [
            a.strip() for a in env("POSTFORME_ACCOUNTS_YUMO", "").split(",") if a.strip()
        ],
    },
    "pictoflex": {
        "brand": "pictoflex",
        "platform_target": "instagram,tiktok",
        "trend_query": "software agency MVP app development trends founders social media 2026",
        "facts": PICTOFLEX_FACTS,
        "imejis_template_id": env("IMEJIS_TEMPLATE_ID_PICTOFLEX", required=True),
        "imejis_text_field": env("IMEJIS_TEXT_FIELD_PICTOFLEX", required=True),
        "postforme_accounts": [
            a.strip() for a in env("POSTFORME_ACCOUNTS_PICTOFLEX", "").split(",") if a.strip()
        ],
    },
}


def build_system_prompt(brand_cfg: dict) -> str:
    """The system prompt used for the main content-generation call."""
    return f"""You are the content strategist and copywriter for {brand_cfg['brand'].title()}.

{brand_cfg['facts']}

IMPORTANT RULES:
- Do not invent product features, testimonials, users, statistics, clients, case studies, revenue or results beyond what is listed above.
- Never fabricate trend data - only reference a trend if it's given to you in the task context.
- Vary content pillars across the batch - do not write 3 posts about the same angle.
- Review the recent-content summary given to you and actively avoid repeating the same hook, topic or structure.
- Apply any learnings given to you about what has performed well or poorly.

{CAROUSEL_FORMAT_RULES}
"""
