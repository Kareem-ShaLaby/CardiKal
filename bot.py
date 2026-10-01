import re
import string
import random
import os
import html
import tempfile
import traceback
import asyncio
from io import BytesIO

# ═══════════════════════════════════════════════════════════════
# FILE INDEX
# ═══════════════════════════════════════════════════════════════
#  This is a surgical extraction of ONLY the PDF collection-and-export
#  feature out of the original Quizician bot.py — no XP/analytics, no
#  lecture/quiz-channel system, no Daily Quiz, no settings, no backups.
#  PDF_BUFFER (and everything else below) is in-memory only, same as it
#  always was in the original bot — a restart mid-collection loses
#  whatever wasn't exported yet, exactly like before.
#
#   50   FONT SETUP
#   90   CONFIG (BOT_TOKEN)
#  110   QUIZZY — flavor text
#  140   MESSAGES
#  160   STATE
#  180   HELPERS (MCQ/written-block parsing)
#  260   PROGRESS MESSAGE BUILDER
#  300   KEYBOARD HELPERS
#  360   HOW TO USE TEXT
#  380   PDF BUILDER
#  770   SLEEP / WAKE
#  790   FORWARDED POLL HANDLER
#  840   POLL UPDATE HANDLER (passive correct-answer backfill)
#  880   CLARIFY QUEUE
#  920   QUESTION REVIEW / EDIT
#  980   IMAGE HANDLER
# 1060   FONT UPLOAD HANDLER
# 1090   DOCUMENT HANDLER (captioned PDFs)
# 1120   TEXT MESSAGE HANDLER
# 1260   INLINE BUTTON HANDLER
# 1440   EXPORT (build+send PDF, session reset)
# 1500   PDF COMMANDS (/pdf_start, /pdf_generate, /pdf_clear, /cancel)
# 1540   START / HELP
# 1570   MAIN
# ═══════════════════════════════════════════════════════════════

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    PollHandler,
    filters,
    ContextTypes,
    AIORateLimiter,
)
from telegram.constants import ParseMode

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, KeepTogether, Flowable, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as _canvas

# ═══════════════════════════════════════════════════════════════
# FONT SETUP
# ═══════════════════════════════════════════════════════════════
_POPPINS_REG  = "/usr/share/fonts/truetype/google-fonts/Poppins-Regular.ttf"
_POPPINS_BOLD = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"

FONT_NAME      = "Helvetica"
FONT_NAME_BOLD = "Helvetica-Bold"

if os.path.exists(_POPPINS_REG) and os.path.exists(_POPPINS_BOLD):
    try:
        pdfmetrics.registerFont(TTFont("Poppins",      _POPPINS_REG))
        pdfmetrics.registerFont(TTFont("Poppins-Bold", _POPPINS_BOLD))
        FONT_NAME      = "Poppins"
        FONT_NAME_BOLD = "Poppins-Bold"
        print("Poppins font loaded")
    except Exception as e:
        print(f"Poppins load error: {e} — using Helvetica")

# ─── FALLBACK FONT CHAIN ────────────────────────────────────────
# No single font covers every script/symbol, so instead of one fallback we
# keep an ORDERED CHAIN. For each character the active font can't draw, we
# walk the chain and use the first font that actually has that glyph.
#
# IMPORTANT reportlab limitation: it can only embed TrueType-outline fonts.
# CFF/PostScript-outline .otf files, most .ttc collections (e.g. Noto CJK)
# and colour-emoji fonts raise TTFError, so those are skipped automatically
# by the try/except below — they are NOT an error, just unusable here.
#
# Order matters: broad symbol/math/Arabic/Cyrillic coverage first, then
# progressively more exotic scripts. Every path is best-effort — anything
# missing on the host is silently skipped. To extend coverage on your host,
# just drop a .ttf into ./fonts/fallback/ (picked up automatically) or
# `apt install fonts-dejavu-core fonts-freefont-ttf fonts-noto-core
# fonts-wqy-zenhei`.
_FALLBACK_CANDIDATES = [
    # (regular, bold-or-None)
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/freefont/FreeSans.ttf",
     "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ("/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
     "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf", None),
    ("/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf", None),
    ("/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf", None),
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None),           # CJK + Hangul
    ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", None),      # Japanese
    ("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", None),
    ("/usr/share/fonts/truetype/unifont/unifont.ttf", None),          # huge BMP coverage
    ("/usr/share/fonts/truetype/ttf-bitstream-vera/Vera.ttf", None),
    ("/Library/Fonts/Arial Unicode.ttf", None),                       # macOS
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),   # Windows
    ("C:/Windows/Fonts/seguisym.ttf", None),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
]

def _discover_bundled_fallbacks():
    """Fonts shipped WITH the bot in ./fonts/fallback/ (DejaVu is included).
    These come FIRST in the chain and are the reason glyphs still render on
    a bare host image that has no system fonts installed. Pairs like
    Foo.ttf + Foo-Bold.ttf are matched up automatically; any extra .ttf you
    drop in there joins the chain too."""
    found = []
    try:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "fallback")
        if os.path.isdir(d):
            files = sorted(f for f in os.listdir(d) if f.lower().endswith((".ttf", ".ttc")))
            lower = {f.lower(): f for f in files}
            for fn in files:
                stem, ext = os.path.splitext(fn)
                if stem.lower().endswith(("-bold", "bold")) and stem.lower() != "bold":
                    continue                                   # attached to its regular below
                bold = lower.get((stem + "-Bold" + ext).lower()) or lower.get((stem + "Bold" + ext).lower())
                found.append((os.path.join(d, fn), os.path.join(d, bold) if bold else None))
    except Exception as e:
        print(f"fallback folder scan error: {e}")
    return found

# Chains hold registered reportlab font NAMES, in priority order.
FALLBACK_CHAIN:      list = []
FALLBACK_CHAIN_BOLD: list = []
_seen_fallback_paths = set()
# Bundled fonts first (guaranteed present), then whatever the host happens to have.
for _i, (_reg_path, _bold_path) in enumerate(_discover_bundled_fallbacks() + _FALLBACK_CANDIDATES):
    if not os.path.exists(_reg_path) or _reg_path in _seen_fallback_paths:
        continue
    _seen_fallback_paths.add(_reg_path)
    try:
        _reg_name = f"PDFFallback{_i}"
        pdfmetrics.registerFont(TTFont(_reg_name, _reg_path))
        _bold_name = _reg_name
        if _bold_path and os.path.exists(_bold_path):
            try:
                _bold_name = f"PDFFallback{_i}-Bold"
                pdfmetrics.registerFont(TTFont(_bold_name, _bold_path))
            except Exception:
                _bold_name = _reg_name
        FALLBACK_CHAIN.append(_reg_name)
        FALLBACK_CHAIN_BOLD.append(_bold_name)
        print(f"PDF fallback font loaded: {os.path.basename(_reg_path)}")
    except Exception as e:
        # Expected for CFF .otf / colour-emoji / some .ttc — reportlab can't embed them.
        print(f"PDF fallback skipped ({os.path.basename(_reg_path)}): {str(e)[:70]}")

# Back-compat aliases: the rest of the file (and anything importing this)
# still sees a single "primary" fallback name.
FALLBACK_FONT_NAME      = FALLBACK_CHAIN[0]      if FALLBACK_CHAIN      else None
FALLBACK_FONT_NAME_BOLD = FALLBACK_CHAIN_BOLD[0] if FALLBACK_CHAIN_BOLD else None

if not FALLBACK_CHAIN:
    # This is THE cause of '?' in the PDF: with no fallback font, every
    # character Poppins lacks (superscripts, arrows, Greek, math, Arabic…)
    # has nowhere to go. Say so loudly instead of failing quietly.
    print("=" * 70)
    print("WARNING: NO FALLBACK FONTS LOADED — special characters will print as '?'.")
    print("  Expected DejaVuSans.ttf in:",
          os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "fallback"))
    print("  Fix: make sure the fonts/ folder is deployed alongside bot.py.")
    print("=" * 70)

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ["BOT_TOKEN"]  # set this in your host's env vars — use a DIFFERENT token/bot than Quizician itself
# NOTE: AIORateLimiter (used below when building `app`) needs the extra:
#   pip install "python-telegram-bot[rate-limiter]"

# Portable temp dir: tempfile.gettempdir() respects $TMPDIR, so this resolves
# to a writable path on both Railway (/tmp) and Termux ($PREFIX/tmp) — a
# hardcoded "/tmp" fails on Android, which has no writable /tmp.
IMG_BASE_DIR  = os.path.join(tempfile.gettempdir(), "quizician_pdf_imgs")
FONT_BASE_DIR = os.path.join(tempfile.gettempdir(), "quizician_pdf_fonts")
# Cover + frame images live here, separately from IMG_BASE_DIR (which gets
# wiped wholesale on every session reset/export) — this directory is never
# touched by _cleanup_images/_reset_pdf_session, so whatever's saved here
# stays remembered across sessions until the bot process restarts.
BRANDING_BASE_DIR = os.path.join(tempfile.gettempdir(), "quizician_pdf_branding")

# Preset fonts bundled with the bot itself (not user-uploaded) — put the
# actual font files in a `fonts/` folder next to this script. Either .ttf
# or .otf works fine — just match the base filename below.
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

def _find_font_file(base_name: str):
    for ext in (".otf", ".ttf", ".OTF", ".TTF"):
        path = os.path.join(FONTS_DIR, base_name + ext)
        if os.path.exists(path):
            return path
    return None

BUNDLED_FONTS = {
    "Comic Sans": {
        "regular": _find_font_file("ComicSans"),
        "bold":    _find_font_file("ComicSans-Bold"),
    },
    "Canva Sans": {
        "regular": _find_font_file("CanvaSans"),
        "bold":    _find_font_file("CanvaSans-Bold"),
    },
    "Times New Roman": {
        "regular": _find_font_file("TimesNewRoman"),
        "bold":    _find_font_file("TimesNewRoman-Bold"),
    },
    "Amaranth": {
        "regular": _find_font_file("Amaranth"),
        "bold":    _find_font_file("Amaranth-Bold"),
    },
}


# ─── TITLE FONT ("The Bomb Sound") ──────────────────────────────
# Drop the font file in the `fonts/` folder next to this script. Any .ttf/.otf
# whose filename contains "bomb" is picked up (TheBombSound.ttf, The Bomb
# Sound.otf, TheBombSound-Regular.ttf ...). NOTE: reportlab can only embed
# TrueType-outline fonts — a CFF/PostScript .otf raises TTFError below; if that
# happens, convert it to .ttf. Until a usable file is found the title falls
# back to the bold body font (still black with a purple outline).
TITLE_FONT    = FONT_NAME_BOLD
TITLE_FONT_OK = False

def _register_title_font():
    global TITLE_FONT, TITLE_FONT_OK
    candidates = []
    try:
        if os.path.isdir(FONTS_DIR):
            candidates = sorted(
                os.path.join(FONTS_DIR, f) for f in os.listdir(FONTS_DIR)
                if "bomb" in f.lower() and f.lower().endswith((".ttf", ".otf"))
            )
    except Exception as e:
        print(f"Title font scan error: {e}")
    for path in candidates:
        try:
            pdfmetrics.registerFont(TTFont("TitleBombSound", path))
            TITLE_FONT, TITLE_FONT_OK = "TitleBombSound", True
            print(f"Title font loaded: {os.path.basename(path)}")
            return
        except Exception as e:
            print(f"Title font {os.path.basename(path)} can't be embedded ({e}) — "
                  f"convert it to a TrueType .ttf. Using the bold body font for now.")
    if not candidates:
        print("Title font not found — put The Bomb Sound (.ttf) in ./fonts/. Using the bold body font for now.")

_register_title_font()


# ═══════════════════════════════════════════════════════════════
# QUIZZY — flavor text (kept purely cosmetic, no dependency on anything else)
# ═══════════════════════════════════════════════════════════════
QUIZZY_WELCOME_ART = (
    " /\\_/\\ \n"
    "( ⌒.⌒ )\n"
    "  > ^ <  "
)
QUIZZY_SLEEPING_ART = (
    " /\\_/\\ \n"
    "(  -.- ) zzz\n"
    " > ^ <  "
)
QUIZZY_OOPS_ART = (
    " /\\_/\\ \n"
    "( ×_× )\n"
    " > ~ <  "
)
QUIZZY_WELCOME_LINES = [
    "صباح (أو مساء) الورد 🌹",
    "باشا البلد",
    "الله أكبر أخيرا قررت تذاكر",
]
QUIZZY_SUCCESS_LINES = [
    "تحياتي 🫡",
    "مش بقول باشا 😎",
    "قدوة 😌🙌",
]
QUIZZY_ERROR_LINES = [
    "كويزي وقع على دماغه من الصدمة، بس متقلقش هنظبطها 😓",
    "كويزي شايف إن المشكلة دي معندهاش داعي، جرب تاني 😓",
    "احنا مش عارفين إيه اللي حصل، بس كويزي واثق إنها هتتحل 😓",
]

def quizzy_block(art: str, line: str) -> str:
    return f"<pre>{html.escape(art)}</pre>\n<i>{html.escape(line)}</i>"

# ═══════════════════════════════════════════════════════════════
# MESSAGES
# ═══════════════════════════════════════════════════════════════
MSG_PDF_ASK_NAME = (
    "✏️ <b>اكتب اسم التوحفة الفنية (الملف) اللي عايزه:</b>\n"
    "<i>Lecture 1 Anatomy Questions</i>"
)
MSG_PDF_EMPTY = "❌ لا يوجد أسئلة محفوظة"
MSG_EXPORT_EMPTY = "❌ لا يوجد أسئلة محفوظة بعد"
MSG_EXPORT_GENERATING = "⏳ جاري توليد {kind} لـ {count} عنصر..."
MSG_PDF_GENERATING = "⏳ جاري توليد PDF لـ {count} عنصر..."
MSG_PDF_CAPTION = "📄 {count} سؤال — {name} ({label}) ❤️\n\n <i>{quizzy_line}</i>"
LABEL_ANSWERED = "بالإجابات"
LABEL_BLANK = "بدون إجابات"
MSG_PDF_CLEARED = "🗑 تم قرار إزالة يا دولي"
MSG_EXPORT_CLEARED_ALL = "🗑 تم قرار إزاله يا دولي"
MSG_CANCEL_DONE = "❌ تم نطر أبلكاش"
MSG_CANCEL_NOTHING = "بتلغيني أنا يعني ولا أي🤨"
MSG_NOT_IN_SESSION = "📄 ابدأ الأول بـ /pdf_start عشان تبدأ تجمع الأسئلة."

LAYOUT_PROMPT_TEXT = (
    "📐 <b>ظبط الشكل</b> — ابعت <u>5 أرقام</u> في رسالة واحدة، مفصولين بمسافة أو فاصلة، بالترتيب ده:\n\n"
    "1️⃣ حجم خط السؤال (الافتراضي 12)\n"
    "2️⃣ المسافة من فوق للسؤال الأول، بالسنتيمتر (الافتراضي 2.5)\n"
    "3️⃣ إزاحة صندوق الإجابات، بالسنتيمتر — رقم موجب يزحزحه لليسار، وسالب يزحزحه لليمين (الافتراضي 0)\n"
    "4️⃣ مسافة صندوق الإجابات من الحافة اليمين، بالسنتيمتر (الافتراضي 0.5)\n"
    "5️⃣ رفع صندوق الإجابات، بالسنتيمتر — رقم موجب يرفعه لفوق، وسالب ينزله لتحت (الافتراضي 4)\n\n"
    "مثال: <code>12 2.5 0 0.5 4</code>\n"
    "لو عايز رقم معين يفضل زي ما هو، اكتب مكانه <code>-</code>.\n"
    "أو ابعت <code>-</code> لوحدها عشان تاخد كل الإعدادات الافتراضية."
)

# ═══════════════════════════════════════════════════════════════
# STATE — all in-memory only, exactly like the original bot (a restart
# loses whatever's mid-collection and hasn't been exported yet)
# ═══════════════════════════════════════════════════════════════
PDF_BUFFER             = {}    # user_id -> list of item dicts
PDF_NAMES              = {}    # user_id -> str
AWAITING_NAME          = {}    # user_id -> True
PDF_FONT_PATH          = {}    # user_id -> path to a regular-weight .ttf/.otf, or absent for the default font
PDF_FONT_BOLD_PATH     = {}    # user_id -> path to that font's bold weight, if one's available (presets only —
                                # a single user upload has no bold companion, so bold text just reuses it)
PDF_FRAME_PATH         = {}    # user_id -> path to a per-page frame/background image. Persists ACROSS
                                # sessions (set with /set_frame, cleared with /clear_frame or a bot
                                # restart) — unlike everything else here, _reset_pdf_session never touches it.
AWAITING_FRAME         = {}    # user_id -> True, waiting on the next photo to save as the frame image
PDF_COVER_PATH         = {}    # user_id -> path to a cover-page image, inserted as page 1 of every PDF.
                                # Same persistence as PDF_FRAME_PATH — set with /set_cover, cleared with
                                # /clear_cover or a bot restart.
AWAITING_COVER         = {}    # user_id -> True, waiting on the next photo to save as the cover image
AWAITING_FONT          = {}    # user_id -> True, while the /pdf_start setup flow is waiting on a font file/skip
PDF_AK_STYLE           = {}    # user_id -> "grouped" (default, rows of "1-A  2-D") or "column" (one "1. A" per line)
PDF_AK_NUDGE_CM        = {}    # user_id -> float, how many cm to shift the answer-key box left of its default spot
AWAITING_AK_STYLE      = {}    # user_id -> True, while the /pdf_start setup flow is waiting on the answer-key style choice

# ─── LAYOUT SETTINGS — all asked for in one combined numeric prompt ─────
PDF_FONT_SIZE          = {}    # user_id -> float, base question font size (other text scales with it)
PDF_TOP_MARGIN_CM      = {}    # user_id -> float, how far the first question starts from the top of the page
PDF_AK_WALL_GAP_CM     = {}    # user_id -> float, how close the answer-key box sits to the page's right edge
PDF_AK_UP_NUDGE_CM     = {}    # user_id -> float, how many cm the answer-key box is lifted above its default corner spot
AWAITING_LAYOUT        = {}    # user_id -> True, while the /pdf_start setup flow is waiting on the combined layout numbers

# Defaults used for any layout value the user leaves blank/"-"
DEFAULT_FONT_SIZE      = 12.0
DEFAULT_TOP_MARGIN_CM  = 2.5
DEFAULT_AK_NUDGE_CM    = 0.0
DEFAULT_AK_WALL_GAP_CM = 0.5
DEFAULT_AK_UP_NUDGE_CM = 4.0
SLEEPING               = set()
PROGRESS_MSG_ID        = {}    # user_id -> message_id of the live progress message
PENDING_IMAGE          = {}    # user_id -> local path of an image waiting to attach to the NEXT poll/question
PENDING_IMG_CAPTION    = {}    # user_id -> caption that came with that pending image (printed under it)
PENDING_CASE           = {}    # user_id -> long text (case study) waiting to attach to the NEXT poll/question
CLARIFY_QUEUE          = {}    # user_id -> list of PDF_BUFFER indices awaiting a correct-answer tap
POLL_WATCH             = {}    # poll_id -> (user_id, item_index) for passive auto-detection
PENDING_EDIT           = {}    # user_id -> {"index": int, "field": "q"/"title"/"content"/"option", "opt_index": int?}
                                # awaiting free-text replacement for one field of a just-added question

# ─── PAGE-MARGIN TUNING ─────────────────────────────────────────
CASE_MIN_CHARS      = 40    # plain text at least this long (and not a question) is treated as a case study
IMG_MAX_LINES       = 5     # an image may be at most this many lines of text tall (width follows the aspect ratio)
OPT_SPACING         = 6     # points of space after each MCQ option (was 3)
Q_NUM_COLOR         = "#90A4AE"   # colour of the inline "Q1" label
LEFT_MARGIN_CM      = 1.2   # where questions start from the page's left edge (was 2.0)
AK_COLUMN_BOX_W     = 4.2 * cm   # width of the "column"-style answer-key box (single source of truth)
TEXT_TO_BOX_GAP_CM  = 0.4   # breathing room between the end of the text lines and the answer-key box
MIN_RIGHT_MARGIN_CM = 1.0   # text never runs closer than this to the right edge of the page
MIN_TEXT_WIDTH_CM   = 8.0   # safety floor so a huge left-shift of the box can't squeeze the text column shut

PDF_MAX_IMG_WIDTH  = 13 * cm
PDF_MAX_IMG_HEIGHT = 9 * cm    # caps height too, so a tall/portrait photo
                               # can't balloon into taking up the whole page

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def clean_option(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[A-Ea-e1-5][).\-]\s*", "", line)
    line = re.sub(r"^[-•]\s*", "", line)
    return line.strip()

def strip_leading_letter_prefix(option: str) -> str:
    # Was only stripping "a)"-style markers, not "a." or "a-" — kept via
    # clean_option so every path that later prepends "A) " (forwarded
    # polls included) strips the SAME set of original markers first,
    # instead of forwarded polls double-labeling as "A) a. text".
    return clean_option(option)

_MCQ_OPTION_PREFIX_RE = re.compile(r"^[A-Ea-e1-5][).\-]\s*")

def _looks_like_mcq_attempt(lines: list) -> bool:
    """True if at least one line looks like someone attempting an MCQ
    option (starts with a "a)"/"b)"/"1." style prefix — same pattern
    clean_option() strips), even though the block as a whole fell short
    of the 3+ lines normalize_mcq_block needs to treat it as a real
    question. Used to tell an ordinary chat message apart from a
    genuine-but-broken question attempt."""
    return any(_MCQ_OPTION_PREFIX_RE.match(l) for l in lines)

def normalize_mcq_block(block: str):
    block = block.strip()
    if "\n" in block:
        return [l.strip() for l in block.split("\n") if l.strip()]
    match = re.search(r"\b([A-Ea-e1-5])[).]", block)
    if not match:
        return [block]
    question     = block[:match.start()].strip()
    options_part = block[match.start():]
    parts = re.split(r"(?=\b[A-Ea-e1-5][).])", options_part)
    return [question] + [p.strip() for p in parts if p.strip()]

def extract_written_qa(block: str, spoiler_texts: list = None, mask: list = None, literal: bool = False):
    """
    A block is a written question ONLY when part of it is hidden behind a
    spoiler: either a real Telegram spoiler (select the answer text in the
    Telegram app and choose "Spoiler" formatting — preferred) or the
    legacy typed "||text||" marker, kept for backward compatibility. The
    spoiler-covered part becomes the (hidden) answer; EVERYTHING ELSE in
    the block is the (always-visible) question.

    A block with NO spoiler anywhere is NOT a written question — this
    returns None so the block falls through to MCQ / other detection
    instead, which is what actually fixes misclassifying plain MCQ blocks
    as "written" questions.

    `mask` (preferred) is a per-character list of booleans, True where that
    character of `block` is covered by a real spoiler entity. It's computed
    from the message's entity offsets (see _spoiler_mask), so the split is
    exact — a spoiler whose text also appears elsewhere in the question
    (e.g. the answer "T4" and a "T4" in the question) can't be mixed up.
    Without a mask it falls back to matching `spoiler_texts` by substring.

    literal=True keeps the question text exactly as written (whitespace tidied
    only); the default also trims dangling punctuation like a trailing ":" or
    "-" left behind where an "Answer:" label used to sit before the spoiler.
    """
    question = block
    answers  = []

    if mask is not None and len(mask) == len(block):
        q_chars, cur = [], []
        for ch, hidden in zip(block, mask):
            if hidden:
                cur.append(ch)
            else:
                if cur:
                    answers.append("".join(cur))
                    cur = []
                    q_chars.append(" ")        # keep words on either side of the gap apart
                q_chars.append(ch)
        if cur:
            answers.append("".join(cur))
            q_chars.append(" ")
        question = "".join(q_chars)
    else:
        for sp in (spoiler_texts or []):
            sp = sp.strip()
            if sp and sp in question:
                answers.append(sp)
                question = question.replace(sp, " ", 1)

    def _grab(m):
        answers.append(m.group(1).strip())
        return " "
    question = re.sub(r"\|\|(.+?)\|\|", _grab, question, flags=re.DOTALL)

    answers = [a.strip() for a in answers if a and a.strip()]
    if not answers:
        return None  # no spoiler at all → not a written question

    question = re.sub(r"[ \t]+", " ", question)
    question = re.sub(r" *\n\s*\n+ *", "\n", question)
    question = re.sub(r" *\n *", "\n", question)
    question = question.strip() if literal else question.strip(" \n\t.:،-")
    answer   = " / ".join(answers)
    if not question or not answer:
        return None
    return question, answer

def _spoiler_mask(raw: str, entities) -> list:
    """Per-character mask of a message's text: True where the character is
    inside a real Telegram spoiler. Entity offsets/lengths are in UTF-16 code
    units, so characters outside the BMP (emoji…) count as two."""
    spans = [(e.offset, e.offset + e.length)
             for e in (entities or []) if getattr(e, "type", None) == MessageEntity.SPOILER]
    if not spans:
        return [False] * len(raw)
    mask, pos = [], 0
    for ch in raw:
        mask.append(any(st <= pos < en for st, en in spans))
        pos += 2 if ord(ch) > 0xFFFF else 1
    return mask

def _split_blocks_with_mask(raw: str, mask: list) -> list:
    """Blank-line-separated blocks of the raw message text as
    [(block_text, block_mask), ...], whitespace-trimmed, empties dropped."""
    out, last = [], 0
    for m in list(re.finditer(r"\n\s*\n", raw)) + [None]:
        end = m.start() if m else len(raw)
        seg, seg_mask = raw[last:end], mask[last:end]
        lead  = len(seg) - len(seg.lstrip())
        trail = len(seg.rstrip())
        seg, seg_mask = seg[lead:trail], seg_mask[lead:trail]
        if seg:
            out.append((seg, seg_mask))
        if m:
            last = m.end()
    return out

def _is_forwarded(msg) -> bool:
    """True for a forwarded message (works across python-telegram-bot versions)."""
    return any(getattr(msg, attr, None) for attr in
               ("forward_origin", "forward_date", "forward_from", "forward_from_chat", "forward_sender_name"))

def _whole_message_written_qa(raw: str, mask: list, blocks: list, forwarded: bool):
    """The rule for a written question that arrives as ONE message (typically
    forwarded): everything NOT under a spoiler is the question, everything
    under a spoiler is the answer — blank lines and all. Returns (question,
    answer), or None to leave the message to the normal block-by-block path.

    It applies when the message has a spoiler and either it was forwarded, or
    some block has no spoiler (a question sitting apart from its hidden answer).
    A message that also holds ordinary, complete MCQ blocks is a mixed batch,
    so it's left to the per-block path."""
    if not any(mask):
        return None
    for text, m in blocks:
        if not any(m) and parse_mcq_block(text):
            return None
    has_unspoilered_block = any(not any(m) for _, m in blocks)
    if not (forwarded or has_unspoilered_block):
        return None
    lead  = len(raw) - len(raw.lstrip())
    trail = len(raw.rstrip())
    return extract_written_qa(raw[lead:trail], mask=mask[lead:trail], literal=True)

def parse_mcq_lines(lines: list):
    """
    Given already-normalized MCQ lines (question line + option lines),
    extracts (question, raw_options, correct_index, explanation).
    correct_index is None if no option was marked correct.
    """
    question      = lines[0]
    options       = []
    correct_index = None
    explanation   = None

    for line in lines[1:]:
        ex_match = re.match(r"^ex:\s*(.+)", line, re.IGNORECASE)
        if ex_match:
            explanation = ex_match.group(1).strip()
            continue

        opt       = clean_option(line)
        has_z_end = re.search(r"\s+[zZ]\s*$", opt)
        has_check = "✅" in opt

        if has_z_end or has_check:
            opt           = opt.replace("✅", "")
            opt           = re.sub(r"\s+[zZ]\s*$", "", opt).strip()
            correct_index = len(options)

        if opt:
            options.append(opt)

    return question, options, correct_index, explanation

def parse_mcq_block(block: str):
    """
    Full validation on top of parse_mcq_lines: returns
    (question, raw_options, correct_index, explanation) only if the block
    is a COMPLETE, valid MCQ (>=3 lines, a correct answer marked).
    Returns None otherwise. Used to detect a fully-formed question in an
    image caption so we don't need to ask the user to resend it.
    """
    lines = normalize_mcq_block(block.strip())
    if len(lines) < 3:
        return None
    question, options, correct_index, explanation = parse_mcq_lines(lines)
    if correct_index is None or correct_index >= len(options):
        return None
    return question, options, correct_index, explanation

# ═══════════════════════════════════════════════════════════════
# GLYPH SAFETY — makes sure no character ever renders as a blank box
# or crashes the PDF build. Three layers:
#   1. normalize_text()     — strips invisible junk, maps emoji → drawable
#                             symbols, shapes Arabic (optional libs)
#   2. pdf_safe_markup()    — per-character fallback down the font CHAIN
#   3. everything wrapped   — a bad char degrades to "?" and never raises
# ═══════════════════════════════════════════════════════════════

# Optional Arabic support. If these aren't installed, Arabic is simply left
# as-is (still renders, just unjoined) instead of the bot failing to start.
#   pip install arabic-reshaper python-bidi
try:
    import arabic_reshaper as _arabic_reshaper
    from bidi.algorithm import get_display as _bidi_get_display
    ARABIC_SHAPING_AVAILABLE = True
except Exception:
    ARABIC_SHAPING_AVAILABLE = False

# Characters with NO visible glyph in any font. Left in, they show up as
# empty squares (▯) or stray gaps, so they're removed. NOTE: ZWJ/ZWNJ are
# only meaningful for scripts that need shaping; since we pre-shape Arabic
# ourselves and draw with plain TTFs (no shaper), they'd render as boxes.
_INVISIBLE_CHARS = {
    0x00AD,                      # soft hyphen
    0x034F,                      # combining grapheme joiner
    0x061C,                      # arabic letter mark
    0x115F, 0x1160, 0x17B4, 0x17B5,
    0x180B, 0x180C, 0x180D, 0x180E,   # mongolian free variation selectors
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,  # zero-width space/joiners/marks
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi embeddings/overrides
    0x2060, 0x2061, 0x2062, 0x2063, 0x2064,  # word joiner + invisible operators
    0x2066, 0x2067, 0x2068, 0x2069,          # bidi isolates
    0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F,
    0x3164, 0xFEFF, 0xFFA0,                  # hangul fillers, BOM
    0xFFF9, 0xFFFA, 0xFFFB,                  # interlinear annotation
    0xFFFC, 0xFFFD,                          # object replacement / replacement char
}
# Variation selectors (VS1–VS16, VS17–VS256) — the emoji "make it colourful"
# selector (U+FE0F) is the classic cause of a blank box after a ✔/⚠ etc.
_VARIATION_SELECTORS = set(range(0xFE00, 0xFE10)) | set(range(0xE0100, 0xE01F0))
# Emoji tag characters used in flag sequences (🏴󠁧󠁢󠁥󠁮󠁧󠁿) — invisible on their own.
_TAG_CHARS = set(range(0xE0000, 0xE0080))
# Skin-tone modifiers (🏻–🏿) — they'd render as a coloured box alone.
_SKIN_TONES = set(range(0x1F3FB, 0x1F400))

# Emoji → a symbol that EXISTS in ordinary monochrome TTFs, so the meaning
# survives instead of a blank. Anything emoji-ish NOT listed here is handled
# by the generic fallback in _replace_unrenderable().
_EMOJI_MAP = {
    "✅": "✓", "✔": "✓", "☑": "✓", "🗹": "✓", "✓": "✓",
    "❌": "✗", "✖": "✗", "❎": "✗", "☒": "✗", "🗙": "✗", "✘": "✗",
    "⚠": "!", "❗": "!", "❕": "!", "‼": "!!", "❓": "?", "❔": "?", "⁉": "?!",
    "📷": "[img]", "📸": "[img]", "🖼": "[img]", "📹": "[video]", "🎥": "[video]",
    "📄": "[doc]", "📃": "[doc]", "📑": "[doc]", "📝": "[note]", "📋": "[list]",
    "📌": "•", "📍": "•", "🔹": "◆", "🔸": "◆", "🔺": "▲", "🔻": "▼",
    "🔴": "●", "🟠": "●", "🟡": "●", "🟢": "●", "🔵": "●", "🟣": "●", "⚫": "●", "⚪": "○",
    "🟥": "■", "🟧": "■", "🟨": "■", "🟩": "■", "🟦": "■", "🟪": "■", "⬛": "■", "⬜": "□",
    "⭐": "★", "🌟": "★", "✨": "*", "💫": "*",
    "➡": "→", "⬅": "←", "⬆": "↑", "⬇": "↓", "↗": "↗", "↘": "↘", "↙": "↙", "↖": "↖",
    "▶": "▶", "◀": "◀", "🔼": "▲", "🔽": "▼", "⏩": "»", "⏪": "«",
    "➕": "+", "➖": "−", "➗": "÷", "✖️": "×",
    "💡": "*", "🔥": "*", "💯": "100", "🎯": "•", "🏆": "*", "🎓": "•",
    "📚": "•", "📖": "•", "🔬": "•", "🧪": "•", "🧬": "•", "💊": "•", "💉": "•",
    "🩺": "•", "🫀": "•", "🧠": "•", "🦠": "•", "🩸": "•", "🫁": "•", "🦴": "•",
    "👉": "→", "👈": "←", "👆": "↑", "👇": "↓", "👍": "+", "👎": "−",
    "😀": ":)", "😃": ":)", "😄": ":)", "😁": ":D", "😊": ":)", "🙂": ":)", "😉": ";)",
    "😢": ":(", "😭": ":'(", "😞": ":(", "🙁": ":(", "😡": ">:(", "😮": ":O", "😂": ":D",
    "❤": "♥", "💙": "♥", "💚": "♥", "💛": "♥", "💜": "♥", "🖤": "♥", "🤍": "♡",
    "©": "©", "®": "®", "™": "™", "℠": "℠",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ", "\u200a": " ",
    "\u2002": " ", "\u2003": " ", "\u2004": " ", "\u2005": " ", "\u2006": " ",
    "\u2008": " ", "\u205f": " ", "\u3000": " ", "\u1680": " ",
    "\u2028": "\n", "\u2029": "\n", "\u0085": "\n", "\u000b": "\n", "\u000c": "\n", "\r": "\n",
    "\t": "    ",
}

def _is_emoji_codepoint(cp: int) -> bool:
    """Broad emoji/pictograph block test — used to catch emoji we didn't
    hand-map so they become a tidy placeholder instead of a blank box."""
    return (
        0x1F300 <= cp <= 0x1FAFF      # misc symbols/pictographs, emoticons, transport, supplemental
        or 0x1F000 <= cp <= 0x1F2FF   # mahjong, dominoes, cards, enclosed alphanumerics
        or 0x2600 <= cp <= 0x27BF     # misc symbols + dingbats
        or 0x2B00 <= cp <= 0x2BFF     # misc symbols and arrows
        or 0x1F900 <= cp <= 0x1F9FF
        or cp in (0x203C, 0x2049, 0x2122, 0x2139, 0x231A, 0x231B, 0x2328, 0x23CF,
                  0x23E9, 0x23EA, 0x23EB, 0x23EC, 0x23ED, 0x23EE, 0x23EF, 0x23F0,
                  0x23F1, 0x23F2, 0x23F3, 0x23F8, 0x23F9, 0x23FA, 0x24C2, 0x25AA,
                  0x25AB, 0x25B6, 0x25C0, 0x25FB, 0x25FC, 0x25FD, 0x25FE, 0x2934,
                  0x2935, 0x3030, 0x303D, 0x3297, 0x3299)
    )

def _strip_control_and_invisible(text: str) -> str:
    """Drops every character that can never draw anything AND can corrupt
    output: ASCII/C1 control chars (except \n), invisible format chars,
    variation selectors, emoji tags, lone surrogates, and non-characters.
    Lone surrogates + NUL are the ones that actually CRASH reportlab/lxml
    ('All strings must be XML compatible'), not just draw blank."""
    out = []
    for ch in text:
        cp = ord(ch)
        if ch == "\n":
            out.append(ch)
        elif cp < 32 or 0x7F <= cp <= 0x9F:                 # C0 / DEL / C1 controls
            continue
        elif 0xD800 <= cp <= 0xDFFF:                        # lone surrogates → crash
            continue
        elif cp in _INVISIBLE_CHARS or cp in _VARIATION_SELECTORS or cp in _TAG_CHARS:
            continue
        elif (cp & 0xFFFE) == 0xFFFE or 0xFDD0 <= cp <= 0xFDEF:  # non-characters
            continue
        elif 0xE000 <= cp <= 0xF8FF:                        # private use — never has a glyph
            continue
        else:
            out.append(ch)
    return "".join(out)

def _shape_arabic_hebrew(text: str) -> str:
    """Joins Arabic letters into their connected forms and reorders RTL runs
    for a left-to-right drawing engine. Reportlab draws glyphs strictly in
    logical order with no shaping, so without this Arabic comes out as
    isolated, backwards letters. No-ops (safely) if the libs aren't
    installed or the text has no RTL characters."""
    if not ARABIC_SHAPING_AVAILABLE:
        return text
    if not any(("\u0590" <= c <= "\u08FF") or ("\uFB1D" <= c <= "\uFDFF") or ("\uFE70" <= c <= "\uFEFF") for c in text):
        return text
    try:
        # Shape line-by-line so newlines/paragraph structure survive bidi.
        return "\n".join(_bidi_get_display(_arabic_reshaper.reshape(line)) for line in text.split("\n"))
    except Exception:
        return text

def _flags_to_codes(text: str) -> str:
    """🇪🇬 is two 'regional indicator' codepoints (U+1F1EA U+1F1EC) that no
    monochrome font draws as a flag; convert each pair to its ISO country
    code ('EG') so the meaning survives instead of showing boxed letters."""
    out, i = [], 0
    while i < len(text):
        c = ord(text[i])
        if 0x1F1E6 <= c <= 0x1F1FF and i + 1 < len(text) and 0x1F1E6 <= ord(text[i + 1]) <= 0x1F1FF:
            out.append(chr(c - 0x1F1E6 + 65) + chr(ord(text[i + 1]) - 0x1F1E6 + 65))
            i += 2
        elif 0x1F1E6 <= c <= 0x1F1FF:
            out.append(chr(c - 0x1F1E6 + 65)); i += 1
        else:
            out.append(text[i]); i += 1
    return "".join(out)

def normalize_text(text, shape_rtl: bool = False) -> str:
    """One entry point for making arbitrary user text safe to draw.
    - non-str → str, invalid/lone-surrogate bytes replaced
    - NFC-normalizes (so 'é' as e+◌́ becomes a single drawable glyph)
    - maps emoji/odd spaces to drawable equivalents
    - drops invisible / crashing characters
    - optionally shapes Arabic/Hebrew (PDF text is drawn with no shaper, so we do it)"""
    import unicodedata
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    try:
        text = text.encode("utf-8", "replace").decode("utf-8", "replace")
    except Exception:
        pass
    try:
        text = unicodedata.normalize("NFC", text)
    except Exception:
        pass
    text = _flags_to_codes(text)
    # Multi-char emoji keys first (e.g. "✖️" with its selector), then singles.
    for k in sorted((k for k in _EMOJI_MAP if len(k) > 1), key=len, reverse=True):
        if k in text:
            text = text.replace(k, _EMOJI_MAP[k])
    out = []
    for ch in text:
        cp = ord(ch)
        if ch in _EMOJI_MAP:
            out.append(_EMOJI_MAP[ch])
        elif cp in _SKIN_TONES:
            continue
        elif _is_emoji_codepoint(cp):
            # Unmapped emoji/pictograph: keep it ONLY if some chain font can
            # really draw it (many dingbats/arrows/symbols can). Otherwise use
            # a placeholder — but collapse a whole run of them into ONE, so
            # "🧬🔬💊💉" becomes a single "•" rather than "••••".
            out.append(ch if _chain_font_for(ch, False) else "•")
        else:
            out.append(ch)
    text = _strip_control_and_invisible("".join(out))
    # Collapse runs of placeholder bullets that came from emoji ("🧬🔬💊💉"
    # -> "••••") into one. Only touches 2+ bullets in a row, so a lone
    # "•" or a deliberate "• item" list marker is never affected.
    text = re.sub(r"•(?:[ \u00a0]?•)+", "•", text)
    if shape_rtl:
        text = _shape_arabic_hebrew(text)
    return text

def _font_has_glyph(font_name: str, ch: str) -> bool:
    """Whether a registered font can draw `ch`. TTF fonts expose the exact
    codepoints they contain via face.charWidths; base-14 fonts (plain
    Helvetica) don't, so we assume Latin-1 coverage for those."""
    try:
        face = pdfmetrics.getFont(font_name).face
        widths = getattr(face, "charWidths", None)
        if widths is not None:
            return ord(ch) in widths
    except Exception:
        pass
    return ord(ch) < 256

_GLYPH_PICK_CACHE: dict = {}

def _chain_font_for(ch: str, bold: bool, primary: str = None):
    """Returns the first font in the fallback chain that can draw `ch`, or
    None if nothing can. Cached — text is drawn char-by-char and chains are
    short, but a big question bank would otherwise redo this constantly."""
    key = (ch, bold, primary)
    hit = _GLYPH_PICK_CACHE.get(key, 0)
    if hit != 0:
        return hit
    chain = FALLBACK_CHAIN_BOLD if bold else FALLBACK_CHAIN
    found = None
    for name in chain:
        if _font_has_glyph(name, ch):
            found = name
            break
    if found is None and bold:                # bold face may lack it; regular might not
        for name in FALLBACK_CHAIN:
            if _font_has_glyph(name, ch):
                found = name
                break
    _GLYPH_PICK_CACHE[key] = found
    return found

def _last_resort_char(font_name: str, bold: bool) -> str:
    """A character guaranteed drawable in the primary font, used when
    NOTHING in the chain has the requested glyph."""
    for cand in ("?", "•", "*", "-", " "):
        if _font_has_glyph(font_name, cand):
            return cand
    return " "

def pdf_safe_markup(text: str, font_name: str, fallback_name: str = None,
                    bold: bool = None) -> str:
    """Escapes text for a reportlab Paragraph and, per character, picks the
    first font that can actually draw it: the active font if it can,
    otherwise the first font in the fallback chain that can, otherwise a
    harmless placeholder — so there are no blank boxes and no exceptions.

    `fallback_name` is kept ONLY for backwards compatibility with old call
    sites; the real chain is FALLBACK_CHAIN / FALLBACK_CHAIN_BOLD, chosen by
    whether `font_name` is a bold face (or pass bold= explicitly)."""
    if not text:
        return ""
    try:
        text = normalize_text(text, shape_rtl=True)
    except Exception:
        text = html.escape(str(text)) if not isinstance(text, str) else text
    if not text:
        return ""

    if bold is None:
        bold = ("bold" in str(font_name).lower()) or (fallback_name in FALLBACK_CHAIN_BOLD and fallback_name not in FALLBACK_CHAIN)
        if fallback_name and fallback_name == FALLBACK_FONT_NAME_BOLD and fallback_name != FALLBACK_FONT_NAME:
            bold = True

    out, buf, current = [], [], None          # current = fallback font name in use, or None for primary

    def flush():
        if buf:
            out.append(html.escape("".join(buf)))
            buf.clear()

    for ch in text:
        if ch == "\n":
            target = current                  # newlines ride along with the current run
        elif _font_has_glyph(font_name, ch):
            target = None
        else:
            target = _chain_font_for(ch, bold, font_name)
            if target is None:                # nothing can draw it → placeholder in primary font
                ch = _last_resort_char(font_name, bold)
                target = None
        if target != current:
            flush()
            if current is not None:
                out.append("</font>")
            current = target
            if current is not None:
                out.append(f'<font face="{current}">')
        buf.append(ch)
    flush()
    if current is not None:
        out.append("</font>")
    return "".join(out)

_LEADING_BULLET_RE = re.compile(r"^\s*(?:[•●○◦▪▫■□◆◇▸▹►▻‣⁃∙·*\-–—✓✔☑➤➢➔→»]+\s+)+")

def _strip_leading_bullet(line: str) -> str:
    """Removes bullet-ish markers the user already typed so our own '•'
    doesn't double up ('• • text'). Requires a following space so it never
    eats a real leading '-5' or '*args'."""
    stripped = _LEADING_BULLET_RE.sub("", line, count=1)
    return stripped if stripped.strip() else line

def _pdf_marker(symbol: str, font_name: str, bold: bool = False) -> str:
    """Returns a decorative marker (✓ • etc.) ready to embed in a Paragraph,
    guaranteed to draw something. These literals used to be written straight
    into paragraphs, bypassing all glyph checks — so with Poppins/Helvetica
    (which lack ✓) they rendered as blank boxes. Now: primary font if it has
    it, else the first chain font that does, else a plain ASCII stand-in."""
    stand_in = {"✓": "v", "✗": "x", "•": "-", "◆": "*", "★": "*", "→": "->"}
    if _font_has_glyph(font_name, symbol):
        return html.escape(symbol)
    f = _chain_font_for(symbol, bold, font_name)
    if f:
        return f'<font face="{f}">{html.escape(symbol)}</font>'
    return html.escape(stand_in.get(symbol, "*"))

def _cleanup_images(user_id: int):
    import shutil
    img_dir = os.path.join(IMG_BASE_DIR, str(user_id))
    if os.path.exists(img_dir):
        shutil.rmtree(img_dir, ignore_errors=True)

def _clear_pending_image(user_id: int):
    """Drop any image that's still waiting for a question, deleting its file."""
    path = PENDING_IMAGE.pop(user_id, None)
    PENDING_IMG_CAPTION.pop(user_id, None)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

def _clear_pending_attachments(user_id: int):
    """Drop BOTH kinds of parked attachment (image + case-study text)."""
    _clear_pending_image(user_id)
    PENDING_CASE.pop(user_id, None)

def _take_pending(user_id: int) -> dict:
    """Pops whatever is parked for this user and returns it as item fields
    ({"image", "image_caption", "case"} — only the ones that exist), ready for
    item.update(...). Called when the next poll / typed question arrives."""
    out = {}
    img = PENDING_IMAGE.pop(user_id, None)
    cap = PENDING_IMG_CAPTION.pop(user_id, None)
    if img:
        out["image"] = img
        if cap:
            out["image_caption"] = cap
    case = PENDING_CASE.pop(user_id, None)
    if case:
        out["case"] = case
    return out

def _add_pending_case(user_id: int, text: str):
    """Park case-study text for the next question. Several messages sent before
    the poll are joined (blank line between) into one case."""
    text = text.strip()
    if not text:
        return
    prev = PENDING_CASE.get(user_id)
    PENDING_CASE[user_id] = f"{prev}\n\n{text}" if prev else text

def _block_looks_like_mcq(block: str, lines: list) -> bool:
    """True when the block looks like someone TRYING to write an MCQ (so the
    'wrong format' warnings are the right reply) rather than plain prose such
    as a case study. Multi-line: some line after the first starts with an
    option marker (a) / b. / 1)). One-liner: it has a first AND second inline
    option marker (a) … b) …) — a stray 'type 2.' or 'e.g.' doesn't count."""
    if "\n" in block:
        return any(_MCQ_OPTION_PREFIX_RE.match(l) for l in lines[1:])
    marks = re.findall(r"(?:^|\s)([A-Ea-e1-5])[).]\s", block)
    return len(marks) >= 2 and marks[0].lower() in "a1" and marks[1].lower() in "b2"

def _clear_pending_edit(user_id: int):
    """Drop any pending 'send new text for this field' state for this user."""
    PENDING_EDIT.pop(user_id, None)

def _clear_clarify_queue(user_id: int):
    """Drop any pending 'choose the correct answer' queue/watches for this user."""
    CLARIFY_QUEUE.pop(user_id, None)
    stale_poll_ids = [pid for pid, (uid, _) in POLL_WATCH.items() if uid == user_id]
    for pid in stale_poll_ids:
        POLL_WATCH.pop(pid, None)

# ═══════════════════════════════════════════════════════════════
# PROGRESS MESSAGE BUILDER
# ═══════════════════════════════════════════════════════════════
def build_progress_text(items: list, latest_label: str = "", pending: list = None) -> str:
    count   = len(items)
    bar_len = 4   # smaller block = the bar fills up faster (2 items = 50% full)

    if count == 0:
        filled = 0
    else:
        filled = count % bar_len or bar_len   # land on a full bar, not an empty one
    bar = "█" * filled + "░" * (bar_len - filled)

    type_counts = {"mcq": 0, "written": 0, "image": 0}
    for it in items:
        t = it.get("type", "mcq")
        if t in type_counts:
            type_counts[t] += 1

    breakdown = []
    if type_counts["mcq"]:
        breakdown.append(f"❓ {type_counts['mcq']} MCQ")
    if type_counts["written"]:
        breakdown.append(f"📝 {type_counts['written']} Written")
    if type_counts["image"]:
        breakdown.append(f"🖼 {type_counts['image']} Image")

    text = (
        f"📄 <b>PDF Collection Mode</b>\n"
        f"<code>{bar}</code>\n"
        f"Collected: <b>{count}</b> item{'s' if count != 1 else ''}"
    )
    if breakdown:
        text += f"\n{' · '.join(breakdown)}"
    if pending:
        text += f"\n📎 Attached to the next poll: {' · '.join(pending)}"
    return text

def _pending_summary(user_id: int) -> list:
    """What's parked and waiting for the next poll — shown on the progress
    message in place of a separate 'received' reply."""
    out = []
    if PENDING_CASE.get(user_id):
        out.append("📋 case text")
    if PENDING_IMAGE.get(user_id):
        out.append("🖼 image")
    return out

# Progress refreshes are COALESCED per user and run in the background, so the
# handler never waits on Telegram: it just marks the progress message "dirty"
# and returns, and one worker task redraws it with the LATEST state. A burst of
# forwarded polls therefore jumps the bar straight to the current count instead
# of crawling through every intermediate number behind a queue of edits.
_PROGRESS_DIRTY: dict = {}   # user_id -> chat_id that needs a refresh
_PROGRESS_TASKS: dict = {}   # user_id -> the running worker task

async def _render_progress(context, user_id: int, chat_id: int):
    """Replace the progress message so it stays at the end of the chat."""
    items    = PDF_BUFFER.get(user_id, [])
    text     = build_progress_text(items, pending=_pending_summary(user_id))
    keyboard = export_keyboard()
    msg_id   = PROGRESS_MSG_ID.get(user_id)

    if msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            print(f"PROGRESS DELETE ERROR: {e}")
        PROGRESS_MSG_ID.pop(user_id, None)

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    PROGRESS_MSG_ID[user_id] = sent.message_id

async def _progress_worker(context, user_id: int):
    try:
        while user_id in _PROGRESS_DIRTY:
            chat_id = _PROGRESS_DIRTY.pop(user_id)
            if user_id not in PDF_BUFFER:      # session ended meanwhile — nothing to show
                continue
            try:
                await _render_progress(context, user_id, chat_id)
            except Exception as e:
                print("PROGRESS ERROR:", e)
    finally:
        _PROGRESS_TASKS.pop(user_id, None)

async def update_progress(context, user_id: int, chat_id: int, latest_label: str = ""):
    """Refresh the live progress message right away without blocking the
    caller. (latest_label is kept only for the old call sites.)"""
    _PROGRESS_DIRTY[user_id] = chat_id
    task = _PROGRESS_TASKS.get(user_id)
    if task is not None and not task.done():
        return   # the running worker will pick up the newest state on its next pass
    _PROGRESS_TASKS[user_id] = asyncio.get_running_loop().create_task(_progress_worker(context, user_id))

# ═══════════════════════════════════════════════════════════════
# KEYBOARD HELPERS
# ═══════════════════════════════════════════════════════════════
def export_keyboard():
    row = [InlineKeyboardButton("📄 Export as PDF", callback_data="gen_pdf")]
    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("👁 Preview", callback_data="preview_pdf")],
        [InlineKeyboardButton("✏️ Edit a Question", callback_data="edit_pick")],
        [InlineKeyboardButton("🗑 Clear & Cancel", callback_data="clear_pdf")],
    ])

def _item_preview_label(item: dict, max_len: int = 40) -> str:
    """First few words of a buffered item, for the edit-picker list."""
    if item["type"] == "written":
        text = item.get("title", "")
    elif item["type"] == "image":
        text = item.get("caption") or "(صورة من غير نص)"
    else:
        text = item.get("q", "")
    text = text.strip()
    return text[:max_len] + ("…" if len(text) > max_len else "")

def edit_pick_keyboard(items: list) -> InlineKeyboardMarkup:
    """Numbered grid (1, 2, 3...) — one button per buffered question, each
    jumping straight into the existing per-question edit menu."""
    buttons = [
        InlineKeyboardButton(str(i + 1), callback_data=f"revedit:{i}:open")
        for i in range(len(items))
    ]
    rows = [buttons[i:i + 6] for i in range(0, len(buttons), 6)]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="edit_pick_back")])
    return InlineKeyboardMarkup(rows)

def font_prompt_keyboard() -> InlineKeyboardMarkup:
    """Preset font buttons (bundled .otf files, see BUNDLED_FONTS) plus
    Skip — shown alongside the option to just upload a font file instead."""
    names = list(BUNDLED_FONTS.keys())
    rows  = [
        [InlineKeyboardButton(names[i], callback_data=f"font_preset:{i}") for i in range(0, 2)],
        [InlineKeyboardButton(names[i], callback_data=f"font_preset:{i}") for i in range(2, 4)],
        [InlineKeyboardButton("⏭ Skip", callback_data="font_skip")],
    ]
    return InlineKeyboardMarkup(rows)

# ═══════════════════════════════════════════════════════════════
# HOW TO USE TEXT
# ═══════════════════════════════════════════════════════════════
HOW_TO_USE_TEXT = (
    "📄 <b>How To Use — Quizician PDF Bot</b>\n\n"
    "<b>1) Start a session</b>\n"
    "/pdf_start — pick a name, a font, a cover page (first page of every PDF) and a page frame, then start sending content.\n"
     "The cover and frame are remembered for every future PDF until the bot restarts (/set_cover, /clear_cover, /set_frame, /clear_frame to change them).\n\n"
    "<b>2) Normal MCQ</b>\n"
    "<code>Question?\n"
    "a) Option A\n"
    "b) Option B z   ← mark correct with z\n"
    "c) Option C\n"
    "ex: Explanation here (optional)</code>\n\n"
    "<b>3) Single-line MCQ</b>\n"
    "<code>Question? a) A b) B z c) C</code>\n\n"
    "<b>4) Written / Flashcard</b>\n"
    "Write (or forward) the question and hide the answer behind a "
    "<b>spoiler</b> (select the text → Spoiler). Everything NOT under a spoiler "
    "is the question; everything under a spoiler is the answer — a forwarded "
    "message is read as one question, blank lines included.\n\n"
    "<b>5) Images & case studies</b>\n"
    "Send a photo (a caption is fine) or a long text — a case study — and it's "
    "held and attached to the NEXT poll or question you send, with no reply. "
    "The progress message shows a 📎 line while something is waiting. "
    "(A photo whose caption is a full question is added as that question.)\n\n"
    "<b>6) Forwarded Quiz Polls</b>\n"
    "Forward any Telegram quiz — it's added straight to the buffer, using "
    "Telegram's revealed correct answer where available or asking you to tap "
    "it otherwise.\n\n"
    "<b>7) Captioned PDFs</b>\n"
    "Send a PDF file with a full question as its caption.\n\n"
    "<b>8) Export</b>\n"
    "/pdf_generate — builds a PDF from everything collected so far.\n"
    "Or use the ✏️ Edit / 📄 Export as PDF / 🗑 Clear buttons "
    "on the progress message.\n\n"
    "/pdf_clear — clears the current session.\n"
    "/cancel — cancels whatever's in progress (session, pending image, setup step).\n"
    "😴 /sleep — mute the bot until /start"
)

class _PageRecorder(Flowable):
    """Zero-size flowable placed right after every item's content (inside
    the same KeepTogether block, so it always lands on that question's
    page). When Platypus actually draws it, self.canv.getPageNumber()
    tells us which physical page the question ended up on — the only way
    to know that, since page breaks aren't decided until the story is
    flowed. Recorded into `record[key]`, read back afterwards both to
    group answer_pairs by page for the per-page answer key, and to work
    out which questions start/end a page so their separators can be
    stripped."""
    def __init__(self, record: dict, key):
        Flowable.__init__(self)
        self.width  = 0
        self.height = 0
        self._record = record
        self._key    = key

    def wrap(self, availWidth, availHeight):
        return (0, 0)

    def draw(self):
        self._record[self._key] = self.canv.getPageNumber()

# ═══════════════════════════════════════════════════════════════
# ANSWER KEY OVERLAY — a small bordered box stamped onto the bottom-right
# corner of EVERY page that has at least one scored MCQ, listing only
# that page's questions. Reportlab's Platypus flow can't know where page
# breaks fall until the whole document has been laid out, so this uses
# the standard two-pass Canvas trick: buffer every page via showPage(),
# then on save() replay them, drawing each page's own box on top.
# `page_of_idx` (question number -> 1-indexed page number) is filled in
# by _PageRecorder flowables during the same build() pass, and is fully
# populated by the time save() runs at the very end of it.
# ═══════════════════════════════════════════════════════════════
class _AnswerKeyCanvas(_canvas.Canvas):
    def __init__(self, *args, answer_pairs=None, page_of_idx=None,
                 font_name=FONT_NAME, font_name_bold=FONT_NAME_BOLD,
                 ak_style="grouped", ak_nudge_cm=0.0,
                 ak_right_margin=2*cm, ak_top_margin=2.5*cm,
                 ak_wall_gap_cm=0.5, ak_up_nudge_cm=4.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._ak_pages       = []
        self._answer_pairs   = answer_pairs or []
        self._page_of_idx    = page_of_idx if page_of_idx is not None else {}
        self._ak_font        = font_name
        self._ak_font_bold   = font_name_bold
        self._ak_style       = ak_style if ak_style in ("grouped", "column") else "grouped"
        self._ak_nudge_cm    = ak_nudge_cm or 0.0
        self._ak_wall_gap_cm = ak_wall_gap_cm
        self._ak_up_nudge_cm = ak_up_nudge_cm
        # Only used by the "column" style, which attaches its box to the
        # frame's right edge rather than floating at a fixed spot — needs
        # to know where that edge actually is.
        self._ak_right_margin = ak_right_margin
        self._ak_top_margin   = ak_top_margin

    def showPage(self):
        self._ak_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._ak_pages)
        # Group each answer pair under the page its question was recorded
        # on; a pair whose page never got recorded (shouldn't normally
        # happen) safely falls off rather than crashing the export.
        pairs_by_page = {}
        for q_num, letter in self._answer_pairs:
            page_no = self._page_of_idx.get(q_num)
            if page_no:
                pairs_by_page.setdefault(page_no, []).append((q_num, letter))

        for i, state in enumerate(self._ak_pages):
            self.__dict__.update(state)
            page_pairs = pairs_by_page.get(i + 1)
            if page_pairs:
                self._draw_answer_key(page_pairs)
            super().showPage()
        super().save()

    def _draw_safe_string(self, x, y, text, font, size, bold=False):
        """drawString, but per-character font fallback so nothing in the
        answer key can ever be a blank box (raw drawString has no fallback)."""
        try:
            text = normalize_text(text, shape_rtl=False)
        except Exception:
            text = str(text)
        cur_x = x
        for ch in text:
            f = font
            if not _font_has_glyph(font, ch):
                f = _chain_font_for(ch, bold, font) or font
                if not _font_has_glyph(f, ch):
                    ch = _last_resort_char(font, bold)
                    f = font
            self.setFont(f, size)
            self.drawString(cur_x, y, ch)
            try:
                cur_x += pdfmetrics.stringWidth(ch, f, size)
            except Exception:
                cur_x += size * 0.5

    def _draw_answer_key(self, pairs):
        pad = 0.3 * cm

        # Distance kept from the true physical right edge of the page (the
        # "wall") and the vertical lift off the default bottom-corner spot
        # — both configurable per-session (see PDF_AK_WALL_GAP_CM /
        # PDF_AK_UP_NUDGE_CM), both styles anchor off these now instead of
        # off the doc's rightMargin/sidebar reserve, so the box actually
        # runs out to (near) the edge rather than stopping short of it.
        AK_WALL_GAP = self._ak_wall_gap_cm * cm
        AK_UP_NUDGE = self._ak_up_nudge_cm * cm

        if self._ak_style == "column":
            # One "N. Letter" per line, attached to the frame's right edge
            # as a sidebar column rather than floating — its left edge
            # touches the content frame's right boundary, sitting in the
            # bottom-right corner. No title — the numbered list speaks for
            # itself.
            font_size = 13
            line_h    = 0.65 * cm
            lines     = [f"{n}. {letter}" for n, letter in pairs]
            box_w     = AK_COLUMN_BOX_W   # wide enough for double-digit numbers at this size
            box_h     = pad * 2 + line_h * len(lines)

            x_right  = A4[0] - AK_WALL_GAP - self._ak_nudge_cm * cm
            x_left   = x_right - box_w
            y_bottom = 2.1 * cm + AK_UP_NUDGE

            fill_color   = colors.HexColor("#EDE4F8")
            stroke_color = colors.HexColor("#B39DDB")
        else:
            font_size = 10
            line_h    = 0.48 * cm
            per_row   = 4
            lines     = [
                "   ".join(f"{n}-{letter}" for n, letter in pairs[i:i + per_row])
                for i in range(0, len(pairs), per_row)
            ]
            box_w = 6.6 * cm
            box_h = pad * 2 + line_h * len(lines)

            x_right  = A4[0] - AK_WALL_GAP - self._ak_nudge_cm * cm
            x_left   = x_right - box_w
            y_bottom = 2.1 * cm + AK_UP_NUDGE

            fill_color   = colors.HexColor("#F5F7F8")
            stroke_color = colors.HexColor("#90A4AE")

        self.saveState()
        self.setFillColor(fill_color)
        self.setStrokeColor(stroke_color)
        self.setLineWidth(0.75)
        self.roundRect(x_left, y_bottom, box_w, box_h, 4, stroke=1, fill=1)

        self.setFillColor(colors.HexColor("#1A1A2E"))
        y = y_bottom + box_h - pad - 0.3 * cm
        for line in lines:
            self._draw_safe_string(x_left + pad, y, line, self._ak_font, font_size, bold=False)
            y -= line_h

        self.restoreState()

TITLE_FILL_COLOR       = colors.HexColor("#8A2BE2")   # violet letters
TITLE_INNER_LINE_COLOR = colors.white                 # first line around the letters
TITLE_OUTER_LINE_COLOR = colors.black                 # outer line, around the white one
TITLE_WHITE_LINE_EM    = 0.065   # visible thickness of the white line, as a fraction of the font size
TITLE_BLACK_LINE_EM    = 0.045   # visible thickness of the black line

class _OutlinedTitle(Flowable):
    """Centered display title: violet letters with a double outline — a white
    line right around the letters and a black line around that. Drawn as three
    passes (thick black stroke, thinner white stroke, violet fill on top) so
    both lines sit OUTSIDE the letters like a sticker border. Wraps on spaces,
    shrinks to fit, picks a fallback font per character so nothing renders as
    a box."""
    MIN_SIZE  = 8
    MAX_LINES = 3

    def __init__(self, text, base_size=30.0):
        super().__init__()
        try:
            self.text = normalize_text(text, shape_rtl=True).strip()
        except Exception:
            self.text = str(text).strip()
        self.base_size = base_size
        self.size, self.lines, self.ow, self.leading = base_size, [self.text], 2.0, base_size * 1.2
        self.ow_white = self.ow_black = 1.0
        self._w = self._h = 0

    def _font_for(self, ch):
        if _font_has_glyph(TITLE_FONT, ch):
            return TITLE_FONT
        f = _chain_font_for(ch, True, TITLE_FONT)
        return f if f else TITLE_FONT

    def _runs(self, s):
        """Split into (font, text) runs so each character is drawn by a font that has it."""
        runs = []
        for ch in s:
            f = self._font_for(ch)
            if runs and runs[-1][0] == f:
                runs[-1][1] += ch
            else:
                runs.append([f, ch])
        return runs

    def _width(self, s, size):
        total = 0.0
        for f, t in self._runs(s):
            try:
                total += pdfmetrics.stringWidth(t, f, size)
            except Exception:
                total += len(t) * size * 0.6
        return total

    def _layout(self, size, avail):
        # Arabic/Hebrew is already in visual order after shaping — wrapping it
        # by words would scramble the reading order, so keep it on one line.
        if re.search(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]", self.text):
            return [self.text]
        lines, cur = [], ""
        for word in self.text.split():
            trial = f"{cur} {word}".strip()
            if cur and self._width(trial, size) > avail:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines or [""]

    def wrap(self, availWidth, availHeight):
        size = self.base_size
        while True:
            ow    = size * (TITLE_WHITE_LINE_EM + TITLE_BLACK_LINE_EM)
            inner = max(availWidth - 2 * ow, 10)
            lines = self._layout(size, inner)
            fits  = len(lines) <= self.MAX_LINES and all(self._width(l, size) <= inner for l in lines)
            if fits or size <= self.MIN_SIZE:
                break
            size -= 1
        self.size, self.lines = size, lines
        self.ow_white = size * TITLE_WHITE_LINE_EM
        self.ow_black = size * TITLE_BLACK_LINE_EM
        self.ow       = self.ow_white + self.ow_black   # total border thickness outside the letters
        self.leading = size * 1.2
        self._w = availWidth
        self._h = self.leading * len(lines) + 2 * self.ow
        return self._w, self._h

    def _draw_line(self, c, x, y, line, mode):
        for f, t in self._runs(line):
            to = c.beginText(x, y)
            to.setFont(f, self.size)
            to.setTextRenderMode(mode)      # 1 = stroke only, 0 = fill only
            to.textOut(t)
            c.drawText(to)
            try:
                x += pdfmetrics.stringWidth(t, f, self.size)
            except Exception:
                x += len(t) * self.size * 0.6

    def draw(self):
        c = self.canv
        try:
            asc = min(pdfmetrics.getAscentDescent(TITLE_FONT, self.size)[0], self.size)
        except Exception:
            asc = self.size * 0.85
        y = self._h - self.ow - asc
        c.saveState()
        c.setLineJoin(1)                    # rounded joins/caps = smooth sticker-style border
        c.setLineCap(1)
        for line in self.lines:
            x = (self._w - self._width(line, self.size)) / 2
            # Each pass gets its own save/restore: the text render mode is part
            # of the PDF graphics state and reportlab won't emit "0 Tr" for the
            # fill pass, so without this the stroke mode would leak into it and
            # the black letters would never be drawn.
            # A stroke is centred on the glyph edge, so half its width is covered
            # by the passes drawn after it → visible thickness = half the width.
            c.saveState()                        # 1) black — outermost line
            c.setStrokeColor(TITLE_OUTER_LINE_COLOR)
            c.setLineWidth(2 * (self.ow_black + self.ow_white))
            self._draw_line(c, x, y, line, 1)
            c.restoreState()
            c.saveState()                        # 2) white — line right around the letters
            c.setStrokeColor(TITLE_INNER_LINE_COLOR)
            c.setLineWidth(2 * self.ow_white)
            self._draw_line(c, x, y, line, 1)
            c.restoreState()
            c.saveState()                        # 3) violet letters on top
            c.setFillColor(TITLE_FILL_COLOR)
            self._draw_line(c, x, y, line, 0)
            c.restoreState()
            y -= self.leading
        c.restoreState()

def _fit_image(path, max_w=PDF_MAX_IMG_WIDTH, max_h=PDF_MAX_IMG_HEIGHT):
    """Loads an image as an RLImage, scaled DOWN (never up) to fit within
    max_w x max_h while keeping its aspect ratio — so a wide screenshot or
    a tall portrait photo both come out compressed to a sane on-page size
    instead of one dimension ballooning to fill the page."""
    img   = RLImage(path)
    scale = min(max_w / img.imageWidth, max_h / img.imageHeight, 1.0)
    if scale < 1.0:
        img.drawWidth  = img.imageWidth * scale
        img.drawHeight = img.imageHeight * scale
    return img

# ═══════════════════════════════════════════════════════════════
# PDF BUILDER
# ═══════════════════════════════════════════════════════════════
def build_pdf(items: list, doc_title: str = "questions", font_path: str = None,
              font_bold_path: str = None, bg_image_path: str = None,
              show_answers: bool = True, ak_style: str = "grouped",
              ak_nudge_cm: float = 0.0, font_size: float = DEFAULT_FONT_SIZE,
              top_margin_cm: float = DEFAULT_TOP_MARGIN_CM,
              ak_wall_gap_cm: float = DEFAULT_AK_WALL_GAP_CM,
              ak_up_nudge_cm: float = DEFAULT_AK_UP_NUDGE_CM,
              cover_image_path: str = None) -> BytesIO:
    buffer = BytesIO()

    # Custom font: registered under a name unique to this call so two users'
    # uploaded fonts (built around the same time) can never clobber each
    # other in reportlab's global font registry. If a genuine bold weight
    # file is available (bundled presets only), register it separately so
    # bold text — the question itself — actually renders heavier than the
    # options, not just as the same glyphs relabeled "bold". Falls back to
    # reusing the regular file for bold otherwise (e.g. a plain user
    # upload, which never comes with a bold companion).
    font_name, font_name_bold = FONT_NAME, FONT_NAME_BOLD
    if font_path and os.path.exists(font_path):
        try:
            custom_name = f"CustomFont_{abs(hash(font_path)) % 10**8}"
            pdfmetrics.registerFont(TTFont(custom_name, font_path))
            font_name = font_name_bold = custom_name
            if font_bold_path and os.path.exists(font_bold_path):
                custom_bold_name = f"CustomFontBold_{abs(hash(font_bold_path)) % 10**8}"
                pdfmetrics.registerFont(TTFont(custom_bold_name, font_bold_path))
                font_name_bold = custom_bold_name
        except Exception as e:
            print(f"Custom PDF font load error: {e} — using default")

    def draw_header(canvas, doc):
        canvas.saveState()
        if bg_image_path and os.path.exists(bg_image_path):
            try:
                canvas.drawImage(
                    bg_image_path, 0, 0, width=A4[0], height=A4[1],
                    preserveAspectRatio=False, mask="auto",
                )
            except Exception as e:
                print(f"PDF background image draw error: {e}")
        canvas.restoreState()

    def draw_cover(canvas, doc):
        """Drawn on page 1 ONLY (via onFirstPage), instead of draw_header —
        a full-bleed cover image, deliberately with no frame/background and
        no question content on it (the leading PageBreak() in `story` moves
        straight to page 2 for that)."""
        canvas.saveState()
        if cover_image_path and os.path.exists(cover_image_path):
            try:
                canvas.drawImage(
                    cover_image_path, 0, 0, width=A4[0], height=A4[1],
                    preserveAspectRatio=False, mask="auto",
                )
            except Exception as e:
                print(f"PDF cover image draw error: {e}")
        canvas.restoreState()

    TOP_MARGIN    = top_margin_cm * cm
    LEFT_MARGIN   = LEFT_MARGIN_CM * cm

    # Every text size below is defined relative to DEFAULT_FONT_SIZE and
    # scaled by this ratio, so a single "font size" number moves the whole
    # document's typography up or down together instead of just the
    # question text.
    FSCALE = font_size / DEFAULT_FONT_SIZE

    if ak_style == "column":
        # The text column ends just before the answer-key box's ACTUAL left
        # edge — computed from the same wall-gap / shift numbers the canvas
        # uses to place the box — so lines run right up to it instead of
        # stopping at a fixed reserve. If the box is pushed mostly off the
        # page (negative shift), the text simply extends further right.
        box_left_x   = A4[0] - ak_wall_gap_cm * cm - ak_nudge_cm * cm - AK_COLUMN_BOX_W
        right_margin = A4[0] - box_left_x + TEXT_TO_BOX_GAP_CM * cm
        right_margin = max(MIN_RIGHT_MARGIN_CM * cm, right_margin)
        right_margin = min(right_margin, A4[0] - LEFT_MARGIN - MIN_TEXT_WIDTH_CM * cm)
        _DOC_MARGINS = dict(
            leftMargin=LEFT_MARGIN,
            rightMargin=right_margin,
            topMargin=TOP_MARGIN,
            bottomMargin=2*cm,
        )
    else:
        _DOC_MARGINS = dict(
            leftMargin=LEFT_MARGIN, rightMargin=2*cm,
            topMargin=TOP_MARGIN,
            # Extra bottom margin (vs. the plain 2cm content uses elsewhere)
            # reserves room for the per-page answer-key box so normal flowing
            # text doesn't get laid out underneath where that box will later
            # be stamped on top of it.
            bottomMargin=3.6*cm,
        )

    # Images are capped at IMG_MAX_LINES lines of option text (same line height
    # the options use), so they follow the font-size setting and stay small.
    IMG_MAX_H = IMG_MAX_LINES * 15 * FSCALE

    Q_STYLE = ParagraphStyle(
        "QStyle", fontName=font_name_bold, fontSize=12 * FSCALE, leading=16 * FSCALE,
        textColor=colors.HexColor("#1A1A2E"), spaceAfter=6, spaceBefore=14,
    )
    OPT_STYLE = ParagraphStyle(
        "OptStyle", fontName=font_name, fontSize=11 * FSCALE, leading=15 * FSCALE,
        textColor=colors.HexColor("#1A1A2E"), leftIndent=14, spaceAfter=OPT_SPACING,
    )
    OPT_CORRECT = ParagraphStyle(
        "OptCorrect", fontName=font_name_bold, fontSize=11 * FSCALE, leading=15 * FSCALE,
        textColor=colors.HexColor("#1B5E20"), leftIndent=14, spaceAfter=OPT_SPACING,
    )
    WRITTEN_TITLE = ParagraphStyle(
        "WTitle", fontName=font_name_bold, fontSize=12 * FSCALE, leading=16 * FSCALE,
        textColor=colors.HexColor("#1A1A2E"), spaceAfter=4, spaceBefore=14,
    )
    WRITTEN_BODY = ParagraphStyle(
        "WBody", fontName=font_name, fontSize=11 * FSCALE, leading=15 * FSCALE,
        textColor=colors.HexColor("#37474F"), leftIndent=14, spaceAfter=6,
    )
    # The (spoiler-hidden) answer to a written question — shown only in the
    # "answered" PDF, in the same green as a correct MCQ option; the blank
    # PDF gets writing space instead (see the "written" branch below).
    WRITTEN_ANSWER = ParagraphStyle(
        "WAnswer", fontName=font_name_bold, fontSize=11 * FSCALE, leading=15 * FSCALE,
        textColor=colors.HexColor("#1B5E20"), leftIndent=14, spaceAfter=6,
    )
    NUM_STYLE = ParagraphStyle(
        "NumStyle", fontName=font_name_bold, fontSize=9 * FSCALE,
        textColor=colors.HexColor("#90A4AE"), spaceAfter=2,
    )
    IMG_CAPTION = ParagraphStyle(
        "ImgCaption", fontName=font_name, fontSize=9 * FSCALE, leading=12 * FSCALE,
        textColor=colors.HexColor("#78909C"), spaceAfter=6, spaceBefore=4,
    )
    # Case study text printed above the question it belongs to: a softly
    # shaded, bordered box. Paragraph backgrounds split cleanly across pages,
    # so even a very long case can't overflow a page.
    CASE_STYLE = ParagraphStyle(
        "CaseStyle", fontName=font_name, fontSize=10.5 * FSCALE, leading=15 * FSCALE,
        textColor=colors.HexColor("#2D2A3E"),
        backColor=colors.HexColor("#F5F1FB"), borderColor=colors.HexColor("#C9B8E8"),
        borderWidth=0.8, borderPadding=7, borderRadius=4,
        spaceBefore=8, spaceAfter=6,
    )

    def _build_story(page_of_idx):
        """Builds the flowables for the document. `page_of_idx` is a dict
        that _PageRecorder fills in with question_number -> 1-indexed page
        as doc.build() actually draws each block."""
        story        = []
        answer_pairs = []  # (question_number, letter) for the per-page answer key

        for idx, item in enumerate(items, 1):
            block = []  # everything for this one question — kept together on one page

            # Every question gets a little breathing room before it starts —
            # whether it's Q1 sitting right below the header, a question
            # opening a later page, or one flowing normally after another on
            # the same page.
            block.append(Spacer(1, 18))

            q_num_label = f"~Q{idx}" if item.get("type") == "mcq" and item.get("correct") is None else f"Q{idx}"
            # The number sits INLINE, in front of the question text (same line),
            # instead of on a separate line above it. Image-only items have no
            # question text to sit next to, so they keep the standalone label.
            q_num_inline = (
                f'<font color="{Q_NUM_COLOR}">{pdf_safe_markup(q_num_label, font_name_bold, bold=True)}</font>'
                f'&nbsp;&nbsp;&nbsp;'
            )

            if item["type"] == "mcq":
                if item.get("case"):
                    # Per-line markup so the case keeps its line breaks and each
                    # character still gets a font that can draw it.
                    case_markup = "<br/>".join(
                        pdf_safe_markup(ln, font_name, bold=False) for ln in item["case"].split("\n")
                    )
                    block.append(Paragraph(case_markup, CASE_STYLE))
                block.append(Paragraph(q_num_inline + pdf_safe_markup(item["q"], font_name_bold, bold=True), Q_STYLE))
                if item.get("image"):
                    try:
                        img = _fit_image(item["image"], max_h=IMG_MAX_H)
                        block.append(Spacer(1, 6))
                        block.append(img)
                        if item.get("image_caption"):
                            block.append(Paragraph(pdf_safe_markup(item["image_caption"], font_name, bold=False), IMG_CAPTION))
                        else:
                            block.append(Spacer(1, 6))
                    except Exception as e:
                        block.append(Paragraph(f"[Image error: {pdf_safe_markup(str(e), font_name)}]", WRITTEN_BODY))
                for i, opt in enumerate(item["options"]):
                    safe_opt = pdf_safe_markup(opt, font_name_bold if (show_answers and i == item["correct"]) else font_name,
                                               bold=bool(show_answers and i == item["correct"]))
                    if show_answers and i == item["correct"]:
                        block.append(Paragraph(f"{_pdf_marker('✓', font_name_bold, bold=True)}  {safe_opt}", OPT_CORRECT))
                    else:
                        block.append(Paragraph(f"     {safe_opt}", OPT_STYLE))
                if item.get("correct") is not None:
                    answer_pairs.append((idx, string.ascii_uppercase[item["correct"]]))

            elif item["type"] == "written":
                block.append(Paragraph(q_num_inline + pdf_safe_markup(item["title"], font_name_bold, bold=True), WRITTEN_TITLE))
                if show_answers:
                    block.append(Paragraph(
                        f"{_pdf_marker('✓', font_name_bold, bold=True)}  {pdf_safe_markup(item['content'], font_name_bold, bold=True)}",
                        WRITTEN_ANSWER,
                    ))
                else:
                    # Answer stays hidden (that's the whole point of the
                    # spoiler) — leave writing space instead.
                    block.append(Spacer(1, 1.0 * cm))

            elif item["type"] == "image":
                block.append(Paragraph(q_num_label, NUM_STYLE))
                img_path = item["path"]
                try:
                    img = _fit_image(img_path, max_h=IMG_MAX_H)
                    block.append(Spacer(1, 8))
                    block.append(img)
                    if item.get("caption"):
                        safe_cap = pdf_safe_markup(item["caption"], font_name, bold=False)
                        block.append(Paragraph(f"{safe_cap}", IMG_CAPTION))
                    block.append(Spacer(1, 4))
                except Exception as e:
                    block.append(Paragraph(f"[Image error: {pdf_safe_markup(str(e), font_name)}]", WRITTEN_BODY))

            # Recorded for EVERY item (regardless of type / scored-ness) so
            # page assignment is known for the whole document, not just
            # scored MCQs — needed to compute top/bottom separators below.
            block.append(_PageRecorder(page_of_idx, idx))

            story.append(KeepTogether(block))

        return story, answer_pairs

    page_of_idx  = {}
    story, answer_pairs = _build_story(page_of_idx=page_of_idx)

    # Title (= the file name) at the top of the first content page. With a
    # cover, the PageBreak inserted below goes in front of this, so the title
    # lands on page 2 — the first page that actually carries content.
    if doc_title and str(doc_title).strip():
        story[0:0] = [_OutlinedTitle(doc_title, base_size=30 * FSCALE), Spacer(1, 4)]

    have_cover = bool(cover_image_path and os.path.exists(cover_image_path))
    if have_cover:
        # Page 1 is drawn entirely by draw_cover() below (a full-bleed
        # image, via onFirstPage) — this PageBreak means no flowable
        # content/frame/header ever lands on it, and the first real
        # question starts fresh on page 2.
        story.insert(0, PageBreak())

    doc = SimpleDocTemplate(buffer, pagesize=A4, **_DOC_MARGINS)

    def _make_canvas(*args, **kwargs):
        return _AnswerKeyCanvas(
            *args, answer_pairs=answer_pairs, page_of_idx=page_of_idx,
            font_name=font_name, font_name_bold=font_name_bold,
            ak_style=ak_style, ak_nudge_cm=ak_nudge_cm,
            ak_right_margin=_DOC_MARGINS["rightMargin"], ak_top_margin=_DOC_MARGINS["topMargin"],
            ak_wall_gap_cm=ak_wall_gap_cm, ak_up_nudge_cm=ak_up_nudge_cm,
            **kwargs,
        )

    doc.build(
        story,
        onFirstPage=draw_cover if have_cover else draw_header,
        onLaterPages=draw_header,
        canvasmaker=_make_canvas,
    )
    buffer.seek(0)
    return buffer

# ═══════════════════════════════════════════════════════════════
# PREVIEW — sample PDF with random short + long questions, built with the
# user's CURRENT layout numbers / font / frame / cover / answer-key style.
# Never touches PDF_BUFFER or the session, so it's safe to press any time.
# ═══════════════════════════════════════════════════════════════
PREVIEW_SHORT_COUNT = 5
PREVIEW_LONG_COUNT  = 4

# (question, [options], correct_index)
_PREVIEW_SHORT_MCQ = [
    ("Which bone is the longest in the human body?", ["Femur", "Tibia", "Humerus", "Fibula"], 0),
    ("The heart has how many chambers?", ["Two", "Three", "Four", "Six"], 2),
    ("Which vitamin is produced in the skin?", ["Vitamin A", "Vitamin C", "Vitamin D", "Vitamin K"], 2),
    ("The largest organ of the body is the:", ["Liver", "Skin", "Lung", "Brain"], 1),
    ("Which blood cells carry oxygen?", ["Platelets", "Erythrocytes", "Lymphocytes", "Neutrophils"], 1),
    ("Which nerve is compressed in carpal tunnel syndrome?", ["Ulnar", "Radial", "Median", "Axillary"], 2),
    ("Insulin is secreted by which cells?", ["Alpha cells", "Beta cells", "Delta cells", "Acinar cells"], 1),
]
_PREVIEW_LONG_MCQ = [
    ("A 45-year-old man presents to the emergency department with sudden-onset crushing chest pain "
     "radiating to his left arm and jaw, associated with sweating and shortness of breath that started "
     "about 40 minutes ago while he was climbing stairs. His ECG shows ST-segment elevation in leads II, "
     "III and aVF. Which coronary artery is most likely occluded?",
     ["Right coronary artery, which supplies the inferior wall of the left ventricle and the AV node",
      "Left anterior descending artery, which supplies the anterior wall and most of the interventricular septum",
      "Left circumflex artery, which supplies the lateral wall of the left ventricle",
      "Left main coronary artery, which supplies both the anterior and the lateral walls"], 0),
    ("A 28-year-old woman complains of fatigue, weight gain, constipation and cold intolerance over the past "
     "six months. On examination she has a diffusely enlarged, non-tender thyroid gland and delayed relaxation "
     "of the ankle reflex. Laboratory tests show a high TSH and low free T4. What is the most likely underlying "
     "cause of her condition in an iodine-sufficient area?",
     ["Autoimmune destruction of thyroid tissue with anti-thyroid peroxidase antibodies (Hashimoto thyroiditis)",
      "Stimulating antibodies directed against the TSH receptor on thyroid follicular cells",
      "Viral infection of the thyroid gland causing painful granulomatous inflammation",
      "A functioning adenoma producing excess thyroid hormone independently of TSH"], 0),
    ("During a surgical procedure in the anterior triangle of the neck, the surgeon must identify the structure "
     "that runs within the carotid sheath together with the common carotid artery and the internal jugular vein. "
     "Damage to this structure would lead to hoarseness, loss of the gag reflex and difficulty swallowing. "
     "Which structure is it?",
     ["The vagus nerve (cranial nerve X), lying posteriorly between the artery and the vein",
      "The hypoglossal nerve (cranial nerve XII), crossing superficially over the carotid bifurcation",
      "The accessory nerve (cranial nerve XI), running toward the trapezius muscle",
      "The phrenic nerve, descending on the anterior surface of scalenus anterior"], 0),
    ("A 60-year-old alcoholic patient is brought in with confusion, ataxia and ophthalmoplegia. He has been "
     "malnourished for several months and is given intravenous glucose by the paramedics before arrival. "
     "Which vitamin should have been administered first to prevent worsening of his neurological state, "
     "and which structures are characteristically damaged in this disease?",
     ["Thiamine (vitamin B1); mammillary bodies and the medial dorsal nucleus of the thalamus",
      "Cobalamin (vitamin B12); dorsal columns and lateral corticospinal tracts of the spinal cord",
      "Niacin (vitamin B3); cerebral cortex, causing dermatitis, diarrhea and dementia",
      "Pyridoxine (vitamin B6); peripheral nerves, causing sideroblastic anemia"], 0),
]
# (title/question, answer)
_PREVIEW_SHORT_WRITTEN = [
    ("Define homeostasis.", "The maintenance of a stable internal environment despite external changes."),
    ("What is the function of the pancreas?", "Digestive enzyme secretion and hormone production (insulin and glucagon)."),
    ("Name the layers of the skin.", "Epidermis, dermis and hypodermis (subcutaneous tissue)."),
]
_PREVIEW_LONG_WRITTEN = [
    ("Describe the sequence of events in the cardiac cycle, including the pressure and volume changes "
     "that occur in the left ventricle during each phase, and explain how the heart sounds are produced.",
     "The cardiac cycle starts with atrial contraction, which tops up ventricular filling. Isovolumetric "
     "contraction follows: ventricular pressure rises with all valves closed, and the closing of the AV valves "
     "produces the first heart sound (S1). When ventricular pressure exceeds aortic pressure the aortic valve "
     "opens and rapid ejection begins, then reduced ejection. Isovolumetric relaxation follows closure of the "
     "aortic valve (second heart sound, S2), and finally rapid ventricular filling as the AV valves open."),
    ("Explain in detail how the kidney regulates blood pressure through the renin-angiotensin-aldosterone "
     "system, starting from the stimulus for renin release and ending with the effect on the vasculature.",
     "Low renal perfusion, low sodium at the macula densa or sympathetic stimulation triggers renin release "
     "from the juxtaglomerular cells. Renin converts angiotensinogen to angiotensin I, which ACE (mainly in the "
     "lung) converts to angiotensin II. Angiotensin II causes vasoconstriction, stimulates aldosterone release "
     "from the adrenal cortex (sodium and water retention), stimulates ADH release and thirst, all of which "
     "raise blood pressure."),
]

# (case text, question, [options], correct_index) — shows the shaded case box in the preview
_PREVIEW_CASES = [
    ("A 52-year-old woman is brought to the clinic with progressive fatigue, weight gain and constipation over "
     "eight months. She complains of feeling cold all the time and her skin has become dry.\n\n"
     "Examination: pulse 54/min, delayed relaxation of the ankle reflex, and a firm, diffusely enlarged, "
     "non-tender thyroid gland. Laboratory results: TSH 18 mIU/L (high), free T4 low.",
     "Which antibody is most likely to be found in this patient?",
     ["Anti-thyroid peroxidase (anti-TPO)", "Anti-TSH receptor (stimulating)", "Anti-nuclear (ANA)", "Anti-centromere"], 0),
]

def _preview_sample_image():
    """A generated wide placeholder picture for the preview (None if Pillow's
    drawing isn't available) — lets you see how tall images print."""
    path = os.path.join(tempfile.gettempdir(), "quizician_preview_sample.png")
    try:
        if not os.path.exists(path):
            from PIL import Image, ImageDraw
            w, h = 900, 420
            img = Image.new("RGB", (w, h), (138, 43, 226))
            d = ImageDraw.Draw(img)
            for x in range(0, w, 60):
                d.line([(x, 0), (x - 120, h)], fill=(160, 90, 235), width=18)
            d.rectangle([6, 6, w - 7, h - 7], outline=(255, 255, 255), width=6)
            d.text((w // 2 - 40, h // 2 - 6), "SAMPLE IMAGE", fill=(255, 255, 255))
            img.save(path)
        return path
    except Exception:
        return None

def make_preview_items(n_short: int = PREVIEW_SHORT_COUNT, n_long: int = PREVIEW_LONG_COUNT) -> list:
    """Random mix of short and long questions (MCQ + written), shuffled.
    MCQ options are shuffled too so the answer key shows varied letters."""
    def _mcq(q, opts, correct):
        pairs = list(enumerate(opts))
        random.shuffle(pairs)
        labeled = [f"{string.ascii_uppercase[i]}) {text}" for i, (_, text) in enumerate(pairs)]
        new_correct = next(i for i, (orig, _) in enumerate(pairs) if orig == correct)
        return {"type": "mcq", "q": q, "options": labeled, "correct": new_correct}

    def _written(t, c):
        return {"type": "written", "title": t, "content": c}

    short_pool = [("mcq", x) for x in _PREVIEW_SHORT_MCQ] + [("w", x) for x in _PREVIEW_SHORT_WRITTEN]
    long_pool  = [("mcq", x) for x in _PREVIEW_LONG_MCQ]  + [("w", x) for x in _PREVIEW_LONG_WRITTEN]
    picked = (random.sample(short_pool, min(n_short, len(short_pool)))
              + random.sample(long_pool, min(n_long, len(long_pool))))
    random.shuffle(picked)
    out = [_mcq(*d) if kind == "mcq" else _written(*d) for kind, d in picked]
    # One question that carries a case-study box, so the preview shows how it prints.
    case_text, cq, copts, ccorrect = random.choice(_PREVIEW_CASES)
    case_item = _mcq(cq, copts, ccorrect)
    case_item["case"] = case_text
    out.insert(random.randint(0, len(out)), case_item)
    # One question with a picture, so the image-height cap shows up too.
    sample = _preview_sample_image()
    if sample:
        pic = _mcq("Which structure is highlighted in the picture?",
                   ["Liver", "Spleen", "Pancreas", "Kidney"], 3)
        pic["image"] = sample
        out.insert(random.randint(0, len(out)), pic)
    return out

def preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("👁 Preview", callback_data="preview_pdf")]])

async def _send_preview(context: ContextTypes.DEFAULT_TYPE, message, user_id: int) -> None:
    """Builds ONE answered-style sample PDF (so the answer-key box and green
    markers are visible) with the user's current settings and sends it.
    Leaves the session and PDF_BUFFER completely untouched."""
    import asyncio
    font_size      = PDF_FONT_SIZE.get(user_id, DEFAULT_FONT_SIZE)
    top_margin_cm  = PDF_TOP_MARGIN_CM.get(user_id, DEFAULT_TOP_MARGIN_CM)
    ak_nudge_cm    = PDF_AK_NUDGE_CM.get(user_id, DEFAULT_AK_NUDGE_CM)
    ak_wall_gap_cm = PDF_AK_WALL_GAP_CM.get(user_id, DEFAULT_AK_WALL_GAP_CM)
    ak_up_nudge_cm = PDF_AK_UP_NUDGE_CM.get(user_id, DEFAULT_AK_UP_NUDGE_CM)
    items = make_preview_items()
    try:
        pdf_bytes = await asyncio.to_thread(
            build_pdf, items, "Preview",
            font_path=PDF_FONT_PATH.get(user_id),
            font_bold_path=PDF_FONT_BOLD_PATH.get(user_id),
            bg_image_path=PDF_FRAME_PATH.get(user_id),
            show_answers=True,
            ak_style=PDF_AK_STYLE.get(user_id, "grouped"),
            ak_nudge_cm=ak_nudge_cm, font_size=font_size,
            top_margin_cm=top_margin_cm, ak_wall_gap_cm=ak_wall_gap_cm,
            ak_up_nudge_cm=ak_up_nudge_cm,
            cover_image_path=PDF_COVER_PATH.get(user_id),
        )
    except Exception as e:
        print("PREVIEW ERROR:", e)
        traceback.print_exc()
        await message.reply_text(
            f"{quizzy_block(QUIZZY_OOPS_ART, random.choice(QUIZZY_ERROR_LINES))}\n\n<code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    n_long = sum(1 for it in items if (it["type"] == "mcq" and len(it["q"]) > 120)
                 or (it["type"] == "written" and len(it["title"]) > 100))
    await message.reply_document(
        document=pdf_bytes, filename="preview.pdf",
        caption=(
            f"👁 <b>معاينة</b> — {len(items)} سؤال عشوائي ({len(items) - n_long} قصير · {n_long} طويل)\n"
            f"📐 خط <code>{font_size:g}</code> · فوق <code>{top_margin_cm:g}</code> · "
            f"إزاحة <code>{ak_nudge_cm:g}</code> · حافة <code>{ak_wall_gap_cm:g}</code> · "
            f"رفع <code>{ak_up_nudge_cm:g}</code>"
            + ("" if TITLE_FONT_OK else "\n⚠️ خط The Bomb Sound مش موجود — حط ملفه (.ttf) في فولدر fonts/")
        ),
        parse_mode=ParseMode.HTML,
    )

async def pdf_preview_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    await update.message.reply_text("⏳ جاري توليد المعاينة...")
    await _send_preview(context, update.message, user_id)

# ═══════════════════════════════════════════════════════════════
# SLEEP / WAKE
# ═══════════════════════════════════════════════════════════════
async def sleep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    SLEEPING.add(user_id)
    await update.message.reply_text(
        f"{quizzy_block(QUIZZY_SLEEPING_ART, 'قوزي نام، وأنا نايم معاه 😴')}\n\n"
        "نادينا بـ /start لما تحتاجنا تاني",
        parse_mode=ParseMode.HTML,
    )

# ═══════════════════════════════════════════════════════════════
# FORWARDED POLL HANDLER
# ═══════════════════════════════════════════════════════════════
async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A forwarded (or directly sent) Telegram quiz poll — added straight
    to the buffer. If Telegram hasn't revealed the correct answer (open
    quiz not made by this bot), it's queued for a quick button tap
    instead (see the clarify flow below)."""
    if not update.message or not update.message.poll:
        return

    user_id = update.effective_chat.id
    if user_id in SLEEPING:
        return

    poll = update.message.poll
    question    = poll.question
    raw_options = [strip_leading_letter_prefix(opt.text) for opt in poll.options]
    # Telegram only reveals correct_option_ids if the quiz is closed, or was
    # sent by our own bot / directly to it — an open quiz forwarded from
    # someone else comes back empty. We must NOT guess in that case.
    correct_index = poll.correct_option_ids[0] if poll.correct_option_ids else None

    if user_id not in PDF_BUFFER:
        await update.message.reply_text(MSG_NOT_IN_SESSION)
        return

    labeled_options = [
        f"{string.ascii_uppercase[i]}) {opt}" for i, opt in enumerate(raw_options)
    ]
    item = {
        "type": "mcq", "q": question,
        "options": labeled_options, "correct": correct_index,  # None = unknown
        "poll_id": poll.id,
    }
    # A photo and/or case-study text sent before this poll belongs to it.
    item.update(_take_pending(user_id))

    PDF_BUFFER[user_id].append(item)
    item_index = len(PDF_BUFFER[user_id]) - 1

    if correct_index is None:
        # Telegram hid the answer (quiz still open, not ours) — queue it
        # for a quick button tap instead of silently guessing.
        POLL_WATCH[poll.id] = (user_id, item_index)
        queue = CLARIFY_QUEUE.setdefault(user_id, [])
        queue.append(item_index)
        if len(queue) == 1:  # nothing else currently being asked
            await _ask_next_clarification(context, user_id, update.effective_chat.id)

    await update_progress(context, user_id, update.effective_chat.id)

# ═══════════════════════════════════════════════════════════════
# POLL UPDATE HANDLER — passive correct-answer backfill. Telegram pushes a
# fresh Update.poll (with correct_option_ids filled in) to any bot that
# has previously seen a poll, once that poll is stopped — even for polls
# the bot didn't create. If the original quiz's creator later ends it, we
# quietly backfill the answer with no user action needed.
# ═══════════════════════════════════════════════════════════════
async def poll_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll = update.poll
    if poll is None or not poll.correct_option_ids:
        return

    watch = POLL_WATCH.pop(poll.id, None)
    if not watch:
        return
    user_id, item_index = watch

    items = PDF_BUFFER.get(user_id)
    if not items or item_index >= len(items) or items[item_index]["correct"] is not None:
        return  # buffer changed, or already resolved manually — skip

    item = items[item_index]
    correct_id = poll.correct_option_ids[0]
    item["correct"] = correct_id

    queue = CLARIFY_QUEUE.get(user_id, [])
    if item_index in queue:
        queue.remove(item_index)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ الكويز الأصلي لسؤال Q{item_index + 1} اتقفل وتليجرام بعت الإجابة الصح تلقائي: "
                f"{item['options'][correct_id]}"
            ),
        )
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# CLARIFY QUEUE — when Telegram hasn't revealed a forwarded quiz's correct
# answer, ask the user directly with A/B/C… buttons.
# ═══════════════════════════════════════════════════════════════
async def _ask_next_clarification(context, user_id: int, chat_id: int):
    """Pop-free peek at the front of the clarify queue and ask about it with
    inline A/B/C… buttons. Skips (and drops) any stale entries whose buffer
    item no longer exists (e.g. buffer was cleared mid-queue)."""
    queue = CLARIFY_QUEUE.get(user_id)
    while queue:
        item_index = queue[0]
        items = PDF_BUFFER.get(user_id)
        if not items or item_index >= len(items) or items[item_index]["correct"] is not None:
            queue.pop(0)  # stale or already resolved — skip it
            continue

        item        = items[item_index]
        q_num       = item_index + 1
        first_words = " ".join(item["q"].split()[:5])
        options_txt = "\n".join(item["options"])  # already "A) ..." labeled

        buttons = [
            InlineKeyboardButton(string.ascii_uppercase[i], callback_data=f"clarify:{item_index}:{i}")
            for i in range(len(item["options"]))
        ]
        rows = [buttons[i:i + 6] for i in range(0, len(buttons), 6)]

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"❓ <b>Choose the correct answer</b>\n"
                f"for Q{q_num}: {first_words}…\n\n{options_txt}"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return
    CLARIFY_QUEUE.pop(user_id, None)

# ═══════════════════════════════════════════════════════════════
# QUESTION REVIEW / EDIT — after a question lands in the buffer, tweak
# the question text, any option's text, or which option is correct.
# ═══════════════════════════════════════════════════════════════
def _review_text(item: dict) -> str:
    if item["type"] == "written":
        return f"📝 <b>{html.escape(item['title'])}</b>\n✅ {html.escape(item['content'])}"
    lines = [f"❓ {html.escape(item['q'])}"]
    for i, opt in enumerate(item["options"]):
        mark = "  ✅" if i == item.get("correct") else ""
        lines.append(html.escape(opt) + mark)
    return "\n".join(lines)

def _review_buttons(item_index: int, item: dict) -> InlineKeyboardMarkup:
    if item["type"] == "written":
        rows = [
            [InlineKeyboardButton("✏️ عدّل السؤال", callback_data=f"revedit:{item_index}:title")],
            [InlineKeyboardButton("✏️ عدّل الإجابة", callback_data=f"revedit:{item_index}:content")],
            [InlineKeyboardButton("✅ تمام، مفيش تعديل", callback_data=f"revedit:{item_index}:done")],
        ]
        return InlineKeyboardMarkup(rows)

    opt_buttons = [
        InlineKeyboardButton(f"✏️ {string.ascii_uppercase[i]}", callback_data=f"revedit:{item_index}:opt:{i}")
        for i in range(len(item["options"]))
    ]
    rows = [opt_buttons[i:i + 6] for i in range(0, len(opt_buttons), 6)]
    rows.append([InlineKeyboardButton("✏️ عدّل نص السؤال", callback_data=f"revedit:{item_index}:q")])
    if item.get("correct") is not None:
        rows.append([InlineKeyboardButton("🔁 غيّر الإجابة الصح", callback_data=f"revedit:{item_index}:correct")])
    rows.append([InlineKeyboardButton("✅ تمام، مفيش تعديل", callback_data=f"revedit:{item_index}:done")])
    return InlineKeyboardMarkup(rows)

# ═══════════════════════════════════════════════════════════════
# IMAGE HANDLER
# ═══════════════════════════════════════════════════════════════
async def _save_frame_photo(context: ContextTypes.DEFAULT_TYPE, user_id: int, file_id: str) -> str:
    """Downloads a Telegram photo (by file_id) and registers it as this
    user's per-page PDF frame/background — saved under BRANDING_BASE_DIR
    (not IMG_BASE_DIR), so it survives session resets and stays remembered
    across every future /pdf_start until /clear_frame or a bot restart."""
    frame_dir  = os.path.join(BRANDING_BASE_DIR, str(user_id))
    os.makedirs(frame_dir, exist_ok=True)
    frame_path = os.path.join(frame_dir, "frame.jpg")
    tg_file    = await context.bot.get_file(file_id)
    await tg_file.download_to_drive(frame_path)
    PDF_FRAME_PATH[user_id] = frame_path
    return frame_path

async def _save_cover_photo(context: ContextTypes.DEFAULT_TYPE, user_id: int, file_id: str) -> str:
    """Downloads a Telegram photo (by file_id) and registers it as this
    user's PDF cover page — same persistence as the frame (see above)."""
    cover_dir  = os.path.join(BRANDING_BASE_DIR, str(user_id))
    os.makedirs(cover_dir, exist_ok=True)
    cover_path = os.path.join(cover_dir, "cover.jpg")
    tg_file    = await context.bot.get_file(file_id)
    await tg_file.download_to_drive(cover_path)
    PDF_COVER_PATH[user_id] = cover_path
    return cover_path

async def _start_cover_step(context: ContextTypes.DEFAULT_TYPE, user_id: int, reply_target) -> None:
    """Step after font selection (font → COVER → frame → answer-key style →
    layout → finish). The cover becomes page 1 of every PDF. If one is
    already remembered (set earlier via this flow or /set_cover) it's reused
    automatically; otherwise the user is asked for a photo or can Skip.
    Either way the flow then moves on to the frame step."""
    path = PDF_COVER_PATH.get(user_id)
    if path and os.path.exists(path):
        await reply_target.reply_text(
            "📕 هستخدم الغلاف المسجل قبل كده (أول صفحة). لو عايز تغيّره ابعت /set_cover، أو /clear_cover تشيله.",
        )
        await _start_bg_step(context, user_id, reply_target)
        return
    PDF_COVER_PATH.pop(user_id, None)   # stale entry (file gone) — forget it

    AWAITING_COVER[user_id] = "setup"   # "setup" (vs plain True from /set_cover) = continue the flow after saving
    await reply_target.reply_text(
        "📕 ابعت صورة <b>الغلاف</b> — هتتحط كأول صفحة في كل PDF، أو دوس Skip لو مش عايز غلاف.\n"
        "هتفضل متسجلة وهتتستخدم تلقائي في كل PDF جاي لحد ما البوت يعمل restart، "
        "أو تغيّرها بـ /set_cover أو تشيلها بـ /clear_cover.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Skip", callback_data="cover_skip"),
        ]]),
    )

async def _start_bg_step(context: ContextTypes.DEFAULT_TYPE, user_id: int, reply_target) -> None:
    """Step after the cover (font → cover → frame → answer-key style →
    layout → finish). If a frame is already remembered for this user
    (set earlier via this flow or /set_frame), it's reused automatically
    — no re-prompt. Otherwise: a pinned chat photo is grabbed and used
    automatically, or failing that, the user is asked for one."""
    if user_id in PDF_FRAME_PATH:
        await reply_target.reply_text(
            "🖼 هستخدم الفريم المسجل قبل كده. لو عايز تغيّره ابعت /set_frame، أو /clear_frame تشيله.",
        )
        await _ask_ak_style(context, user_id, reply_target)
        return

    pinned_photo = None
    try:
        chat   = await context.bot.get_chat(user_id)
        pinned = chat.pinned_message
        if pinned and pinned.photo:
            pinned_photo = pinned.photo[-1]
    except Exception:
        pinned_photo = None

    if pinned_photo:
        try:
            await _save_frame_photo(context, user_id, pinned_photo.file_id)
            await reply_target.reply_text(
                "📌 لقيت صورة مثبتة في الشات وحطيتها كفريم للـ PDF أوتوماتيك — هتفضل متسجلة للمرات الجاية.",
            )
            await _ask_ak_style(context, user_id, reply_target)
            return
        except Exception:
            pass  # download failed for some reason — fall back to asking normally

    AWAITING_FRAME[user_id] = "setup"   # "setup" (vs plain True from /set_frame) = continue the flow after saving
    await reply_target.reply_text(
        "🖼 دلوقتي ابعت صورة تتحط كفريم/خلفية لكل صفحة في الـ PDF، أو دوس Skip لو مش عايز فريم.\n"
        "هتفضل متسجلة وهتتستخدم تلقائي في كل PDF جاي، لحد ما تغيّرها بـ /set_frame أو تشيلها بـ /clear_frame.\n"
        "(أو ثبّت صورة في الشات ده والبوت هياخدها تلقائي المرة الجاية.)",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Skip", callback_data="bg_skip"),
        ]]),
    )

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_chat.id
    if user_id in SLEEPING:
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        return

    # ── AWAITING COVER IMAGE (standalone /set_cover, or part of setup) ──
    if AWAITING_COVER.get(user_id):
        in_setup = AWAITING_COVER[user_id] == "setup"
        await _save_cover_photo(context, user_id, photo.file_id)
        del AWAITING_COVER[user_id]
        await update.message.reply_text("✅ اتسجل الغلاف! هيتحط كأول صفحة في كل PDF جاي لحد ما تغيّره أو تشيله.")
        if in_setup:
            await _start_bg_step(context, user_id, update.message)
        return

    # ── AWAITING FRAME IMAGE (part of the /pdf_start setup flow, or
    # standalone /set_frame) ─────────────────────────────────────────
    if AWAITING_FRAME.get(user_id):
        in_setup = AWAITING_FRAME[user_id] == "setup"
        await _save_frame_photo(context, user_id, photo.file_id)
        del AWAITING_FRAME[user_id]
        await update.message.reply_text("✅ اتسجل الفريم! هيتحط في كل صفحة في كل PDF جاي لحد ما تغيّره أو تشيله.")
        if in_setup:
            await _ask_ak_style(context, user_id, update.message)
        return

    if user_id not in PDF_BUFFER:
        await update.message.reply_text(MSG_NOT_IN_SESSION)
        return

    caption = (update.message.caption or "").strip()

    img_dir = os.path.join(IMG_BASE_DIR, str(user_id))
    os.makedirs(img_dir, exist_ok=True)
    img_path = os.path.join(img_dir, f"img_{photo.file_unique_id}.jpg")
    tg_file  = await context.bot.get_file(photo.file_id)
    await tg_file.download_to_drive(img_path)

    # ── Case 1: caption already IS a complete quiz question ─────────
    parsed = parse_mcq_block(caption) if caption else None
    if parsed:
        question, raw_options, correct_index, explanation = parsed
        labeled_options = [
            f"{string.ascii_uppercase[i]}) {opt}" for i, opt in enumerate(raw_options)
        ]
        item = {
            "type": "mcq", "q": question,
            "options": labeled_options, "correct": correct_index,
            "image": img_path,
        }
        case = PENDING_CASE.pop(user_id, None)   # case text sent just before still belongs to it
        if case:
            item["case"] = case
        PDF_BUFFER[user_id].append(item)
        await update_progress(context, user_id, update.effective_chat.id)
        return

    # ── Case 2: any other photo (with or without a caption) — park it. It
    # attaches to the NEXT poll / question that arrives, with no reply; the
    # progress message shows a 📎 line while it's waiting. A caption that
    # isn't a question is printed under the image. ─────────────────────
    _clear_pending_image(user_id)          # a newer photo replaces an unused older one
    PENDING_IMAGE[user_id] = img_path
    if caption:
        PENDING_IMG_CAPTION[user_id] = caption
    await update_progress(context, user_id, update.effective_chat.id)

async def handle_font_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Font file (.ttf/.otf) uploaded during the /pdf_start setup flow.
    Registered on a filter that only matches those two extensions, but
    still guarded by AWAITING_FONT — an unsolicited font upload outside
    the setup flow is just ignored, not treated as a command."""
    if not update.message or not update.message.document:
        return
    user_id = update.effective_chat.id
    if user_id in SLEEPING:
        return
    if not AWAITING_FONT.get(user_id):
        return

    doc      = update.message.document
    font_dir = os.path.join(FONT_BASE_DIR, str(user_id))
    os.makedirs(font_dir, exist_ok=True)
    ext       = ".otf" if (doc.file_name or "").lower().endswith(".otf") else ".ttf"
    font_path = os.path.join(font_dir, f"font{ext}")
    tg_file   = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(font_path)

    PDF_FONT_PATH[user_id] = font_path
    PDF_FONT_BOLD_PATH.pop(user_id, None)   # single upload has no bold companion — clear any stale preset one
    del AWAITING_FONT[user_id]
    await update.message.reply_text("✅ الخط اتسجل!")
    await _start_cover_step(context, user_id, update.message)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PDFs sent in a private DM: captioned = treated as a manual question
    (caption parsed as the MCQ text — no attachment, since Telegram polls
    can only carry a photo, not a PDF). Uncaptioned PDFs are silently ignored."""
    if not update.message or not update.message.document:
        return
    user_id = update.effective_chat.id
    if user_id in SLEEPING:
        return
    doc = update.message.document
    if doc.mime_type != "application/pdf":
        return

    caption = (update.message.caption or "").strip()
    if not caption:
        return

    if user_id not in PDF_BUFFER:
        await update.message.reply_text(MSG_NOT_IN_SESSION)
        return

    parsed = parse_mcq_block(caption)
    if not parsed:
        await update.message.reply_text(
            "⚠️ الكابشن مش صيغة سؤال كاملة (لازم سؤال + اختيارات + إجابة صح متعلّم عليها بـ z)."
        )
        return
    question, raw_options, correct_index, explanation = parsed
    labeled_options = [f"{string.ascii_uppercase[i]}) {opt}" for i, opt in enumerate(raw_options)]
    PDF_BUFFER[user_id].append({"type": "mcq", "q": question, "options": labeled_options, "correct": correct_index})
    await update_progress(
        context, user_id, update.effective_chat.id,
        latest_label=f"📄 {question[:50]}" + ("…" if len(question) > 50 else ""),
    )

# ═══════════════════════════════════════════════════════════════
# TEXT MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_chat.id
    if user_id in SLEEPING:
        return

    text = update.message.text.strip()

    # Real Telegram spoiler formatting (select text → "Spoiler") applied
    # anywhere in this message — used below to tell a written question's
    # hidden answer apart from its always-visible question text. PTB's
    # parse_entities() does the UTF-16 offset math correctly (important
    # for Arabic + emoji mixed in), so these substrings are exact.
    spoiler_texts = list(update.message.parse_entities([MessageEntity.SPOILER]).values())

    # ── AWAITING A QUESTION EDIT (from the review/edit prompt) ───
    pending_edit = PENDING_EDIT.pop(user_id, None)
    if pending_edit:
        items = PDF_BUFFER.get(user_id)
        idx   = pending_edit["index"]
        if not items or idx >= len(items):
            await update.message.reply_text("⚠️ السؤال ده مش موجود في البافر دلوقتي.")
            return
        item  = items[idx]
        field = pending_edit["field"]

        if field == "option":
            opt_idx = pending_edit["opt_index"]
            if 0 <= opt_idx < len(item["options"]):
                letter = string.ascii_uppercase[opt_idx]
                item["options"][opt_idx] = f"{letter}) {text}"
        elif field in ("q", "title", "content"):
            item[field] = text

        await update.message.reply_text(
            "👀 <b>راجع السؤال:</b>\n\n" + _review_text(item) + "\n\nفيه حاجة تانية عايز تعدلها؟",
            parse_mode=ParseMode.HTML,
            reply_markup=_review_buttons(idx, item),
        )
        return

    # ── AWAITING PDF NAME ────────────────────────────────────────
    if AWAITING_NAME.get(user_id):
        name = text.strip()
        PDF_NAMES[user_id] = name
        del AWAITING_NAME[user_id]
        AWAITING_FONT[user_id] = True
        await update.message.reply_text(
            f"📥 <b>الاسم اتسجل:</b> <i>{name}</i>\n\n"
            "اختار خط جاهز، أو ابعت ملف خط (.ttf أو .otf) بنفسك، أو دوس Skip لو عايز الخط الافتراضي.",
            parse_mode=ParseMode.HTML,
            reply_markup=font_prompt_keyboard(),
        )
        return

    # ── AWAITING FONT FILE (reminder — the real handling is in
    #    handle_font_upload/the font_skip button; this only fires if the
    #    user sends plain text instead) ─────────────────────────────
    if AWAITING_FONT.get(user_id):
        await update.message.reply_text(
            "⚠️ اختار خط من الأزرار فوق، ابعت ملف خط (.ttf أو .otf)، أو دوس Skip.",
        )
        return

    # ── AWAITING COVER / FRAME IMAGE (same — reminder only) ─────────
    if AWAITING_COVER.get(user_id):
        await update.message.reply_text(
            "⚠️ محتاج تبعت صورة عشان تتسجل كغلاف"
            + ("، أو دوس Skip فوق." if AWAITING_COVER[user_id] == "setup" else ".")
        )
        return
    if AWAITING_FRAME.get(user_id):
        await update.message.reply_text(
            "⚠️ محتاج تبعت صورة كفريم، أو دوس Skip فوق.",
        )
        return

    # ── AWAITING ANSWER-KEY STYLE (reminder only — real handling is the
    #    ak_style buttons) ──────────────────────────────────────────
    if AWAITING_AK_STYLE.get(user_id):
        await update.message.reply_text(
            "⚠️ اختار شكل صندوق الإجابات من الأزرار فوق.",
        )
        return

    # ── AWAITING COMBINED LAYOUT NUMBERS ─────────────────────────────
    if AWAITING_LAYOUT.get(user_id):
        stripped = text.strip()
        # A lone "-" (or empty message) means "everything default".
        if stripped in ("", "-", "،", "د"):
            tokens = []
        else:
            tokens = [t for t in re.split(r"[\s,،]+", stripped) if t]

        if len(tokens) > 5:
            await update.message.reply_text(
                "⚠️ أقصى حاجة 5 أرقام بس. ابعتهم تاني زي المثال فوق.",
            )
            return

        # Pad with "-" (→ default) for any trailing values left out.
        tokens += ["-"] * (5 - len(tokens))

        DEFAULTS = [
            DEFAULT_FONT_SIZE, DEFAULT_TOP_MARGIN_CM,
            DEFAULT_AK_NUDGE_CM, DEFAULT_AK_WALL_GAP_CM, DEFAULT_AK_UP_NUDGE_CM,
        ]
        # (min, max) sane clamps so a typo can't blow up the layout or send
        # the answer-key box flying off the page. Font size / top margin /
        # wall gap have no sensible negative meaning, so those stay
        # positive-only; the shift and lift allow negative values (negative
        # shift = push right instead of left, negative lift = push down
        # below the default corner spot instead of up).
        CLAMPS = [(4.0, 40.0), (0.5, 10.0), (-15.0, 15.0), (0.0, 5.0), (-2.0, 20.0)]

        parsed = []
        for i, tok in enumerate(tokens):
            if tok in ("-", "د", "x", "X"):
                parsed.append(DEFAULTS[i])
                continue
            try:
                val = float(tok.replace(",", "."))
            except ValueError:
                await update.message.reply_text(
                    f"⚠️ '{tok}' مش رقم. ابعت 5 أرقام (أو - للافتراضي) زي المثال فوق.",
                )
                return
            lo, hi = CLAMPS[i]
            parsed.append(max(lo, min(val, hi)))

        font_size, top_margin_cm, ak_nudge_cm, ak_wall_gap_cm, ak_up_nudge_cm = parsed
        PDF_FONT_SIZE[user_id]      = font_size
        PDF_TOP_MARGIN_CM[user_id] = top_margin_cm
        PDF_AK_NUDGE_CM[user_id]    = ak_nudge_cm
        PDF_AK_WALL_GAP_CM[user_id] = ak_wall_gap_cm
        PDF_AK_UP_NUDGE_CM[user_id] = ak_up_nudge_cm
        del AWAITING_LAYOUT[user_id]
        await _finish_pdf_setup(context, user_id, update.message)
        return

    if user_id not in PDF_BUFFER:
        await update.message.reply_text(MSG_NOT_IN_SESSION)
        return

    try:
        raw    = update.message.text
        mask   = _spoiler_mask(raw, update.message.entities)
        blocks = _split_blocks_with_mask(raw, mask)

        # ── WRITTEN, whole message: a (forwarded) written question — anything
        # NOT under a spoiler is the question, anything under one is the answer.
        whole = _whole_message_written_qa(raw, mask, blocks, _is_forwarded(update.message))
        if whole:
            title, content = whole
            PDF_BUFFER[user_id].append({"type": "written", "title": title, "content": content})
            await update_progress(context, user_id, update.effective_chat.id)
            return

        # Blank-line-separated paragraphs in a case study should stay together.
        # Keep the normal per-block path whenever any paragraph looks structured.
        if len(blocks) > 1 and len(raw.strip()) >= CASE_MIN_CHARS and not any(mask):
            has_structured_block = any(
                extract_written_qa(block, spoiler_texts, mask=block_mask)
                or _block_looks_like_mcq(block, normalize_mcq_block(block))
                for block, block_mask in blocks
            )
            if not has_structured_block:
                _add_pending_case(user_id, raw)
                await update_progress(context, user_id, update.effective_chat.id)
                return

        any_saved  = False
        case_added = False
        last_label = ""

        for block, block_mask in blocks:
            # ── WRITTEN (per block) ─────────────────────────────
            written = extract_written_qa(block, spoiler_texts, mask=block_mask)
            if written:
                title, content = written
                item = {
                    "type":    "written",
                    "title":   title,
                    "content": content,
                }
                PDF_BUFFER[user_id].append(item)
                any_saved  = True
                last_label = title[:50] + ("…" if len(title) > 50 else "")
                continue

            # ── MCQ ─────────────────────────────────────────────
            lines = normalize_mcq_block(block)
            is_attempt = _block_looks_like_mcq(block, lines)
            if len(lines) < 3:
                if is_attempt:
                    await update.message.reply_text(
                        "⚠️ <b>الصياغة غلط!</b>\n\n"
                        "الشكل الصح هو:\n"
                        "<code>السؤال\n"
                        "a) خيار 1\n"
                        "b) خيار 2 z  ← علّم الصح بـ z\n"
                        "c) خيار 3\n"
                        "ex: الشرح (اختياري)</code>",
                        parse_mode=ParseMode.HTML,
                    )
                elif len(block) >= CASE_MIN_CHARS:
                    # Plain prose, not a question → a case study for the next poll.
                    _add_pending_case(user_id, block)
                    case_added = True
                continue

            question, raw_options, correct_index, explanation = parse_mcq_lines(lines)

            if correct_index is None or correct_index >= len(raw_options):
                if not is_attempt:
                    if len(block) >= CASE_MIN_CHARS:
                        _add_pending_case(user_id, block)
                        case_added = True
                    continue
                await update.message.reply_text(
                    "⚠️ <b>ما فيش إجابة صح!</b>\n\n"
                    "علّم الإجابة الصحيحة بـ <code>z</code> في نهايتها:\n"
                    "<code>b) الإجابة الصح z</code>",
                    parse_mode=ParseMode.HTML,
                )
                continue

            # An image sent (with no caption / unparseable caption) just
            # before this message is paired with this question.
            labeled_options = [
                f"{string.ascii_uppercase[i]}) {opt}" for i, opt in enumerate(raw_options)
            ]
            item = {
                "type": "mcq", "q": question,
                "options": labeled_options, "correct": correct_index,
            }
            item.update(_take_pending(user_id))   # photo / case text sent before this question
            PDF_BUFFER[user_id].append(item)
            any_saved  = True
            last_label = question[:50] + ("…" if len(question) > 50 else "")

        if any_saved or case_added:
            await update_progress(context, user_id, update.effective_chat.id, last_label)

    except Exception as e:
        print("ERROR:", e)

# ═══════════════════════════════════════════════════════════════
# INLINE BUTTON HANDLER
# ═══════════════════════════════════════════════════════════════
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data.startswith("clarify:"):
        _, item_index_str, choice_str = query.data.split(":")
        item_index = int(item_index_str)
        choice     = int(choice_str)

        items = PDF_BUFFER.get(user_id)
        if not items or item_index >= len(items) or items[item_index]["correct"] is not None:
            await query.edit_message_text("⚠️ السؤال ده اتحل أو اتشال بالفعل.")
            return

        item = items[item_index]
        if not (0 <= choice < len(item["options"])):
            return

        item["correct"] = choice
        POLL_WATCH.pop(item.get("poll_id"), None)

        queue = CLARIFY_QUEUE.get(user_id, [])
        if item_index in queue:
            queue.remove(item_index)

        await query.edit_message_text(
            f"✅ Q{item_index + 1}: {html.escape(item['options'][choice])}",
            parse_mode=ParseMode.HTML,
        )

        if queue:
            await _ask_next_clarification(context, user_id, query.message.chat_id)
        else:
            CLARIFY_QUEUE.pop(user_id, None)
        return

    # ── QUESTION REVIEW / EDIT ──────────────────────────────────
    if query.data == "edit_pick":
        items = PDF_BUFFER.get(user_id, [])
        if not items:
            await query.answer("مفيش أسئلة دلوقتي", show_alert=True)
            return
        lines = ["✏️ <b>اختار رقم السؤال اللي عايز تعدله:</b>\n"]
        for i, item in enumerate(items):
            lines.append(f"{i + 1}. {html.escape(_item_preview_label(item))}")
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML,
            reply_markup=edit_pick_keyboard(items),
        )
        return

    if query.data == "edit_pick_back":
        items = PDF_BUFFER.get(user_id, [])
        await query.edit_message_text(
            build_progress_text(items, pending=_pending_summary(user_id)), parse_mode=ParseMode.HTML,
            reply_markup=export_keyboard(),
        )
        return

    if query.data.startswith("revedit:"):
        parts      = query.data.split(":")
        item_index = int(parts[1])
        action     = parts[2]

        items = PDF_BUFFER.get(user_id)
        if not items or item_index >= len(items):
            await query.edit_message_text("⚠️ السؤال ده مش موجود في البافر دلوقتي.")
            return
        item = items[item_index]

        if action == "open":
            await query.edit_message_text(
                "✏️ <b>إيه اللي عايز تعدله؟</b>\n\n" + _review_text(item),
                parse_mode=ParseMode.HTML,
                reply_markup=_review_buttons(item_index, item),
            )
            return

        if action == "done":
            await query.edit_message_text(
                "✅ <b>خلاص، اتسجل:</b>\n\n" + _review_text(item), parse_mode=ParseMode.HTML
            )
            return

        if action in ("q", "title", "content"):
            PENDING_EDIT[user_id] = {"index": item_index, "field": action}
            prompt = {
                "q":       "✏️ اكتب نص السؤال الجديد:",
                "title":   "✏️ اكتب نص السؤال الجديد:",
                "content": "✏️ اكتب الإجابة الجديدة:",
            }[action]
            await query.edit_message_text(prompt)
            return

        if action == "opt":
            opt_idx = int(parts[3])
            if not (0 <= opt_idx < len(item["options"])):
                return
            PENDING_EDIT[user_id] = {"index": item_index, "field": "option", "opt_index": opt_idx}
            letter = string.ascii_uppercase[opt_idx]
            await query.edit_message_text(f"✏️ اكتب النص الجديد للاختيار {letter} (من غير الحرف):")
            return

        if action == "correct":
            buttons = [
                InlineKeyboardButton(string.ascii_uppercase[i], callback_data=f"revcorrect:{item_index}:{i}")
                for i in range(len(item["options"]))
            ]
            rows = [buttons[i:i + 6] for i in range(0, len(buttons), 6)]
            await query.edit_message_text("🔁 اختار الإجابة الصح:", reply_markup=InlineKeyboardMarkup(rows))
            return
        return

    if query.data.startswith("revcorrect:"):
        _, item_index_str, choice_str = query.data.split(":")
        item_index = int(item_index_str)
        choice     = int(choice_str)

        items = PDF_BUFFER.get(user_id)
        if not items or item_index >= len(items):
            await query.edit_message_text("⚠️ السؤال ده مش موجود في البافر دلوقتي.")
            return
        item = items[item_index]
        if not (0 <= choice < len(item["options"])):
            return

        item["correct"] = choice
        await query.edit_message_text(
            "👀 <b>راجع السؤال:</b>\n\n" + _review_text(item) + "\n\nفيه حاجة تانية عايز تعدلها؟",
            parse_mode=ParseMode.HTML,
            reply_markup=_review_buttons(item_index, item),
        )
        return

    # ── PDF SETUP FLOW: font/background skip buttons ────────────────
    if query.data.startswith("font_preset:"):
        if not AWAITING_FONT.get(user_id):
            return
        idx   = int(query.data.split(":")[1])
        names = list(BUNDLED_FONTS.keys())
        if idx >= len(names):
            return
        name      = names[idx]
        font_path = BUNDLED_FONTS[name]["regular"]
        if not font_path or not os.path.exists(font_path):
            await query.answer(f"⚠️ ملف {name} مش موجود على السيرفر دلوقتي.", show_alert=True)
            return
        bold_path = BUNDLED_FONTS[name]["bold"]
        PDF_FONT_PATH[user_id] = font_path
        if bold_path and os.path.exists(bold_path):
            PDF_FONT_BOLD_PATH[user_id] = bold_path
        else:
            PDF_FONT_BOLD_PATH.pop(user_id, None)
        del AWAITING_FONT[user_id]
        await query.edit_message_text(f"✅ خط <b>{name}</b> اتحدد!", parse_mode=ParseMode.HTML)
        await _start_cover_step(context, user_id, query.message)
        return

    if query.data == "font_skip":
        if not AWAITING_FONT.get(user_id):
            return
        PDF_FONT_PATH.pop(user_id, None)
        PDF_FONT_BOLD_PATH.pop(user_id, None)
        del AWAITING_FONT[user_id]
        await query.edit_message_text("⏭ اتخطيت اختيار الخط.")
        await _start_cover_step(context, user_id, query.message)
        return

    if query.data == "cover_skip":
        if AWAITING_COVER.get(user_id) != "setup":
            return
        del AWAITING_COVER[user_id]
        await query.edit_message_text("⏭ اتخطيت الغلاف.")
        await _start_bg_step(context, user_id, query.message)
        return

    if query.data == "bg_skip":
        if not AWAITING_FRAME.get(user_id):
            return
        del AWAITING_FRAME[user_id]
        await _ask_ak_style(context, user_id, query.message, edit=True)
        return

    # ── PDF SETUP FLOW: answer-key style choice ──────────────────
    if query.data.startswith("ak_style:"):
        if not AWAITING_AK_STYLE.get(user_id):
            return
        style = query.data.split(":")[1]
        if style not in ("grouped", "column"):
            return
        PDF_AK_STYLE[user_id] = style
        del AWAITING_AK_STYLE[user_id]
        AWAITING_LAYOUT[user_id] = True
        await query.edit_message_text(LAYOUT_PROMPT_TEXT, parse_mode=ParseMode.HTML)
        return

    # ── PREVIEW BUTTON ──────────────────────────────────────────
    if query.data == "preview_pdf":
        await query.message.reply_text("⏳ جاري توليد المعاينة...")
        await _send_preview(context, query.message, user_id)
        return

    # ── EXPORT BUTTONS ──────────────────────────────────────────
    items = PDF_BUFFER.get(user_id, [])
    name  = PDF_NAMES.get(user_id, "questions")

    if query.data == "gen_pdf":
        if not items:
            await query.message.reply_text(MSG_EXPORT_EMPTY)
            return
        await query.message.reply_text(MSG_EXPORT_GENERATING.format(kind="PDF", count=len(items)))
        await _export_pdf_session(context, query.message, user_id, items, name)

    elif query.data == "clear_pdf":
        _reset_pdf_session(user_id)
        await query.message.reply_text(MSG_EXPORT_CLEARED_ALL)

# ═══════════════════════════════════════════════════════════════
# EXPORT — build+send PDF, then reset the session
# ═══════════════════════════════════════════════════════════════
async def _ask_ak_style(context: ContextTypes.DEFAULT_TYPE, user_id: int, reply_target, edit: bool = False) -> None:
    """Step after background (font → background → answer-key style → nudge
    → finish) — asks whether the per-page answer key should be laid out
    as grouped rows ("1-A  2-D  3-E") or a single vertical column
    ("1. A" / "2. D" / one per line)."""
    AWAITING_AK_STYLE[user_id] = True
    text = (
        "🔑 <b>شكل صندوق الإجابات إيه؟</b>\n"
        "اختار واحد من اللي تحت 👇"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("↔️ صفوف (1-A  2-D)", callback_data="ak_style:grouped"),
        InlineKeyboardButton("↕️ عمود واحد (1. A)", callback_data="ak_style:column"),
    ]])
    send = reply_target.edit_text if edit else reply_target.reply_text
    await send(text, parse_mode=ParseMode.HTML, reply_markup=kb)

async def _finish_pdf_setup(context: ContextTypes.DEFAULT_TYPE, user_id: int, reply_target, edit: bool = False) -> None:
    """Last step of the /pdf_start flow (name → font → background) — opens
    the actual question buffer and shows the 'PDF mode activated' message.
    edit=True rewrites reply_target in place (the bg_skip button flow);
    edit=False sends a fresh reply (there's no bot-owned message to edit
    when this follows an uploaded background photo instead)."""
    PDF_BUFFER[user_id] = []
    PROGRESS_MSG_ID.pop(user_id, None)
    _clear_pending_attachments(user_id)
    _clear_clarify_queue(user_id)
    _clear_pending_edit(user_id)
    name = PDF_NAMES.get(user_id, "questions")
    text = (
        f"📥 <b>PDF mode activated</b> — File name: <i>{name}</i>\n\n"
        "• ابعت أسئلة نصية (MCQ أو مكتوبة)\n"
        "• أو <b>فوروارد</b> كويزات\n"
        "• ابعت <b>نص طويل (حالة)</b> أو <b>صورة</b> قبل الكويز وهتتعلق عليه تلقائي\n\n"
        "اضغط <b>Export as PDF</b> لما تخلص 👇"
    )
    send = reply_target.edit_text if edit else reply_target.reply_text
    await send(text, parse_mode=ParseMode.HTML, reply_markup=preview_keyboard())

def _reset_pdf_session(user_id: int) -> None:
    """Clears everything tied to an in-progress PDF-collection session —
    used after a successful export and by explicit clear/cancel alike."""
    _cleanup_images(user_id)
    _clear_pending_attachments(user_id)
    _clear_clarify_queue(user_id)
    _clear_pending_edit(user_id)
    PDF_BUFFER.pop(user_id, None)
    _PROGRESS_DIRTY.pop(user_id, None)
    PDF_NAMES.pop(user_id, None)
    AWAITING_NAME.pop(user_id, None)
    AWAITING_FONT.pop(user_id, None)
    AWAITING_FRAME.pop(user_id, None)
    AWAITING_COVER.pop(user_id, None)
    AWAITING_AK_STYLE.pop(user_id, None)
    AWAITING_LAYOUT.pop(user_id, None)
    PDF_AK_STYLE.pop(user_id, None)
    PDF_AK_NUDGE_CM.pop(user_id, None)
    PDF_FONT_SIZE.pop(user_id, None)
    PDF_TOP_MARGIN_CM.pop(user_id, None)
    PDF_AK_WALL_GAP_CM.pop(user_id, None)
    PDF_AK_UP_NUDGE_CM.pop(user_id, None)
    PROGRESS_MSG_ID.pop(user_id, None)
    font_path = PDF_FONT_PATH.pop(user_id, None)
    # Only delete it if it's a per-user upload (under FONT_BASE_DIR) — never
    # a bundled preset (under FONTS_DIR), which is a shared asset every
    # future user picks from, not something owned by this one session.
    if font_path and font_path.startswith(FONT_BASE_DIR) and os.path.exists(font_path):
        try:
            os.remove(font_path)
        except Exception:
            pass
    PDF_FONT_BOLD_PATH.pop(user_id, None)   # always a bundled preset path (or absent) — never a per-user file, nothing to delete
    # NOTE: PDF_COVER_PATH / PDF_FRAME_PATH are deliberately NOT touched
    # here — unlike everything above, they're meant to stay remembered
    # across sessions (until /clear_cover, /clear_frame, or a bot restart).

async def _export_pdf_session(context: ContextTypes.DEFAULT_TYPE, message, session_id: int,
                               items: list, name: str) -> bool:
    """Builds the PDF from items and sends TWO versions via
    message.reply_document — one with correct options marked in green plus
    the answer key, one with no options marked but still carrying the answer
    key — then resets the session. Returns False (having already replied
    with the reason) if a build blew up."""
    safe = re.sub(r"[^\w\s\-]", "", name).strip().replace(" ", "_") or "questions"
    font_path      = PDF_FONT_PATH.get(session_id)
    font_bold_path = PDF_FONT_BOLD_PATH.get(session_id)
    bg_path        = PDF_FRAME_PATH.get(session_id)
    ak_style       = PDF_AK_STYLE.get(session_id, "grouped")
    ak_nudge_cm    = PDF_AK_NUDGE_CM.get(session_id, DEFAULT_AK_NUDGE_CM)
    font_size      = PDF_FONT_SIZE.get(session_id, DEFAULT_FONT_SIZE)
    top_margin_cm  = PDF_TOP_MARGIN_CM.get(session_id, DEFAULT_TOP_MARGIN_CM)
    ak_wall_gap_cm = PDF_AK_WALL_GAP_CM.get(session_id, DEFAULT_AK_WALL_GAP_CM)
    ak_up_nudge_cm = PDF_AK_UP_NUDGE_CM.get(session_id, DEFAULT_AK_UP_NUDGE_CM)
    cover_path     = PDF_COVER_PATH.get(session_id)

    import asyncio
    try:
        answered_bytes = await asyncio.to_thread(
            build_pdf, items, name, font_path=font_path,
            font_bold_path=font_bold_path, bg_image_path=bg_path,
            show_answers=True, ak_style=ak_style, ak_nudge_cm=ak_nudge_cm,
            font_size=font_size, top_margin_cm=top_margin_cm,
            ak_wall_gap_cm=ak_wall_gap_cm, ak_up_nudge_cm=ak_up_nudge_cm,
            cover_image_path=cover_path,
        )
        blank_bytes = await asyncio.to_thread(
            build_pdf, items, name, font_path=font_path,
            font_bold_path=font_bold_path, bg_image_path=bg_path,
            show_answers=False, ak_style=ak_style, ak_nudge_cm=ak_nudge_cm,
            font_size=font_size, top_margin_cm=top_margin_cm,
            ak_wall_gap_cm=ak_wall_gap_cm, ak_up_nudge_cm=ak_up_nudge_cm,
            cover_image_path=cover_path,
        )
    except Exception as e:
        print("PDF ERROR:", e)
        traceback.print_exc()
        await message.reply_text(
            f"{quizzy_block(QUIZZY_OOPS_ART, random.choice(QUIZZY_ERROR_LINES))}\n\n<code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )
        return False
    await message.reply_document(
        document=answered_bytes, filename=f"{safe}_answered.pdf",
        caption=MSG_PDF_CAPTION.format(count=len(items), name=name, label=LABEL_ANSWERED,
                                        quizzy_line=random.choice(QUIZZY_SUCCESS_LINES)),
        parse_mode=ParseMode.HTML,
    )
    await message.reply_document(
        document=blank_bytes, filename=f"{safe}_blank.pdf",
        caption=MSG_PDF_CAPTION.format(count=len(items), name=name, label=LABEL_BLANK,
                                        quizzy_line=random.choice(QUIZZY_SUCCESS_LINES)),
        parse_mode=ParseMode.HTML,
    )

    _reset_pdf_session(session_id)
    return True

# ═══════════════════════════════════════════════════════════════
# PDF COMMANDS
# ═══════════════════════════════════════════════════════════════
async def pdf_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    _reset_pdf_session(user_id)
    AWAITING_NAME[user_id] = True
    await update.message.reply_text(MSG_PDF_ASK_NAME, parse_mode=ParseMode.HTML)

async def pdf_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    items   = PDF_BUFFER.get(user_id, [])
    if not items:
        await update.message.reply_text(MSG_PDF_EMPTY)
        return
    name = PDF_NAMES.get(user_id, "questions")
    await update.message.reply_text(MSG_PDF_GENERATING.format(count=len(items)))
    await _export_pdf_session(context, update.message, user_id, items, name)

async def pdf_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    _reset_pdf_session(user_id)
    await update.message.reply_text(MSG_PDF_CLEARED)

async def set_cover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Standalone command (works outside a /pdf_start session too) — the
    next photo sent becomes the PDF cover page, remembered across every
    future PDF until /clear_cover or a bot restart."""
    user_id = update.effective_chat.id
    AWAITING_COVER[user_id] = True
    await update.message.reply_text(
        "🖼 ابعت الصورة اللي هتتحط <b>كغلاف</b> (أول صفحة) في كل PDF تعمله بعد كده.\n"
        "هتفضل متسجلة لحد ما تغيّرها تاني أو تشيلها بـ /clear_cover.",
        parse_mode=ParseMode.HTML,
    )

async def clear_cover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    AWAITING_COVER.pop(user_id, None)
    path = PDF_COVER_PATH.pop(user_id, None)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    await update.message.reply_text("🗑 شيلت الغلاف." if path else "مفيش غلاف متسجل أصلاً.")

async def set_frame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Standalone command (works outside a /pdf_start session too) — the
    next photo sent becomes the per-page PDF frame/background, remembered
    across every future PDF until /clear_frame or a bot restart."""
    user_id = update.effective_chat.id
    AWAITING_FRAME[user_id] = True
    await update.message.reply_text(
        "🖼 ابعت الصورة اللي هتتحط <b>كفريم</b> في كل صفحة في كل PDF تعمله بعد كده.\n"
        "هتفضل متسجلة لحد ما تغيّرها تاني أو تشيلها بـ /clear_frame.",
        parse_mode=ParseMode.HTML,
    )

async def clear_frame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    AWAITING_FRAME.pop(user_id, None)
    path = PDF_FRAME_PATH.pop(user_id, None)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    await update.message.reply_text("🗑 شيلت الفريم." if path else "مفيش فريم متسجل أصلاً.")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bails out of whatever's in progress: PDF collection session (or its
    font/background setup step) or a pending image waiting for its question."""
    user_id = update.effective_chat.id
    was_doing_something = bool(
        PDF_BUFFER.get(user_id) or AWAITING_NAME.get(user_id)
        or AWAITING_FONT.get(user_id) or AWAITING_FRAME.get(user_id) or AWAITING_COVER.get(user_id)
        or AWAITING_AK_STYLE.get(user_id) or AWAITING_LAYOUT.get(user_id)
        or PENDING_IMAGE.get(user_id) or PENDING_CASE.get(user_id)
    )
    _reset_pdf_session(user_id)
    if was_doing_something:
        await update.message.reply_text(MSG_CANCEL_DONE)
    else:
        await update.message.reply_text(MSG_CANCEL_NOTHING)

# ═══════════════════════════════════════════════════════════════
# START / HELP
# ═══════════════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    SLEEPING.discard(chat_id)
    await update.message.reply_text(
        f"{quizzy_block(QUIZZY_WELCOME_ART, random.choice(QUIZZY_WELCOME_LINES))}\n\n"
        "📄 <b>أنا كارديكال، بس تقدر تناديني كاردي 😉 </b>\n\n"
        "استخدم /pdf_start عشان تبدأ تجمع أسئلة وتصدرها PDF.\n"
        "/c لعرض كل الأوامر.",
        parse_mode=ParseMode.HTML,
    )

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        "📖 <b>Available commands:</b>\n",
        "/start — greeting",
        "/pdf_start — starts a session collecting questions for a PDF",
        "/pdf_generate — builds a PDF from what you've collected so far",
        "/pdf_clear — clears the current session",
        "/pdf_preview — sends a sample PDF (random short + long questions) using your current layout numbers",
        "/set_cover — set a cover page image (remembered until you change it or the bot restarts)",
        "/clear_cover — remove the saved cover image",
        "/set_frame — set a per-page frame/background image (remembered until you change it or the bot restarts)",
        "/clear_frame — remove the saved frame image",
        "/cancel — cancels whatever's currently in progress",
        "😴 /sleep — pauses the bot temporarily in this chat",
        "/c — this list",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def how_to_use_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HOW_TO_USE_TEXT, parse_mode=ParseMode.HTML)

# ═══════════════════════════════════════════════════════════════
# GLOBAL ERROR HANDLER — no ERROR_LOG_GROUP_ID channel here (that whole
# system stayed behind in the main bot); just a stderr traceback so a bad
# update never silently vanishes without at least a console trace.
# ═══════════════════════════════════════════════════════════════
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("UNHANDLED ERROR:", context.error)
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .rate_limiter(AIORateLimiter())
    .build()
)

app.add_error_handler(global_error_handler)

app.add_handler(CommandHandler("start",        start))
app.add_handler(CommandHandler("c",            commands_cmd))
app.add_handler(CommandHandler("how_to_use",   how_to_use_cmd))
app.add_handler(CommandHandler("cancel",       cancel_cmd))
app.add_handler(CommandHandler("sleep",        sleep_cmd))
app.add_handler(CommandHandler("pdf_start",    pdf_start))
app.add_handler(CommandHandler("pdf_generate", pdf_generate))
app.add_handler(CommandHandler("pdf_clear",    pdf_clear))
app.add_handler(CommandHandler("pdf_preview",  pdf_preview_cmd))
app.add_handler(CommandHandler("set_cover",    set_cover_cmd))
app.add_handler(CommandHandler("clear_cover",  clear_cover_cmd))
app.add_handler(CommandHandler("set_frame",    set_frame_cmd))
app.add_handler(CommandHandler("clear_frame",  clear_frame_cmd))

# Poll handler before text handler (forwarded OR own quiz polls)
app.add_handler(MessageHandler(filters.POLL, handle_poll))

# Image handler (photos)
app.add_handler(MessageHandler(filters.PHOTO, handle_image))

# Font-file handler (.ttf/.otf uploads during /pdf_start setup) — must be
# registered before the general PDF/document handler below since it's a
# different mime/extension entirely.
app.add_handler(MessageHandler(
    filters.Document.FileExtension("ttf") | filters.Document.FileExtension("otf"),
    handle_font_upload,
))

# PDF handler — captioned PDFs in a private DM are parsed as manual MCQs.
app.add_handler(MessageHandler(filters.Document.PDF, handle_document))

# Inline buttons
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(PollHandler(poll_update_handler))

# Text handler last
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

if __name__ == "__main__":
    print("Quizician PDF bot starting…")
    app.run_polling()
