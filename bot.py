import re
import string
import random
import os
import html
import tempfile
import traceback
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
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

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Image as RLImage, KeepTogether, Flowable
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
PDF_BG_IMAGE_PATH      = {}    # user_id -> path to an uploaded per-page background image, or absent for none
AWAITING_FONT          = {}    # user_id -> True, while the /pdf_start setup flow is waiting on a font file/skip
AWAITING_BG            = {}    # user_id -> True, while the /pdf_start setup flow is waiting on a background image/skip
SLEEPING               = set()
PROGRESS_MSG_ID        = {}    # user_id -> message_id of the live progress message
PENDING_IMAGE          = {}    # user_id -> local path of an image awaiting its question
CLARIFY_QUEUE          = {}    # user_id -> list of PDF_BUFFER indices awaiting a correct-answer tap
POLL_WATCH             = {}    # poll_id -> (user_id, item_index) for passive auto-detection
PENDING_EDIT           = {}    # user_id -> {"index": int, "field": "q"/"title"/"content"/"option", "opt_index": int?}
                                # awaiting free-text replacement for one field of a just-added question

PDF_MAX_IMG_WIDTH = 13 * cm

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

def strip_spoiler_markers(text: str) -> str:
    return re.sub(r"\|\|(.+?)\|\|", r"\1", text, flags=re.DOTALL)

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

def parse_written_question(block: str):
    block = strip_spoiler_markers(block)
    lines = [l.rstrip() for l in block.split("\n") if l.strip()]
    if len(lines) < 2:
        return None
    title = re.sub(r'[\""\']+$', "", lines[0]).strip()
    content_lines = lines[1:]
    content = "\n".join(content_lines).strip()
    if not content:
        return None
    if content.startswith(".") and content.endswith("."):
        content = content[1:-1].strip()
        return title, content
    if content:
        return title, content
    return None

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
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

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
def build_progress_text(items: list, latest_label: str = "") -> str:
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
    return text

async def update_progress(context, user_id: int, chat_id: int, latest_label: str = ""):
    """Edit the existing progress message, or send a new one and store its id."""
    items    = PDF_BUFFER.get(user_id, [])
    text     = build_progress_text(items, latest_label)
    keyboard = export_keyboard()
    msg_id   = PROGRESS_MSG_ID.get(user_id)

    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass  # message too old / deleted — fall through to send new

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    PROGRESS_MSG_ID[user_id] = sent.message_id

# ═══════════════════════════════════════════════════════════════
# KEYBOARD HELPERS
# ═══════════════════════════════════════════════════════════════
def export_keyboard():
    row = [InlineKeyboardButton("📄 Export as PDF", callback_data="gen_pdf")]
    return InlineKeyboardMarkup([
        row,
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
    "/pdf_start — pick a name, a font, and a page background, then start sending content.\n\n"
    "<b>2) Normal MCQ</b>\n"
    "<code>Question?\n"
    "a) Option A\n"
    "b) Option B z   ← mark correct with z\n"
    "c) Option C\n"
    "ex: Explanation here (optional)</code>\n\n"
    "<b>3) Single-line MCQ</b>\n"
    "<code>Question? a) A b) B z c) C</code>\n\n"
    "<b>4) Written / Flashcard</b>\n"
    "<code>Title\n"
    "answer line 1\n"
    "answer line 2</code>\n\n"
    "<b>5) Images</b>\n"
    "Send a photo — with a full question as its caption to pair them, with a "
    "plain caption to add it as a standalone image, or with no caption to be "
    "asked for the question next.\n\n"
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
    """Zero-size flowable placed right after a scored MCQ (inside the same
    KeepTogether block, so it always lands on that question's page). When
    Platypus actually draws it, self.canv.getPageNumber() tells us which
    physical page the question ended up on — the only way to know that,
    since page breaks aren't decided until the story is flowed. Recorded
    into `record[key]`, read back afterwards to group answer_pairs by
    page for the per-page answer key."""
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
                 font_name=FONT_NAME, font_name_bold=FONT_NAME_BOLD, **kwargs):
        super().__init__(*args, **kwargs)
        self._ak_pages      = []
        self._answer_pairs  = answer_pairs or []
        self._page_of_idx   = page_of_idx if page_of_idx is not None else {}
        self._ak_font       = font_name
        self._ak_font_bold  = font_name_bold

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
        per_row = 5
        lines   = [
            "   ".join(f"{n}-{letter}" for n, letter in pairs[i:i + per_row])
            for i in range(0, len(pairs), per_row)
        ]

        pad     = 0.35 * cm
        line_h  = 0.42 * cm
        title_h = 0.5 * cm
        box_w   = 6.6 * cm
        box_h   = pad * 2 + title_h + line_h * len(lines)

        x_right  = A4[0] - 2 * cm
        x_left   = x_right - box_w
        y_bottom = 1.3 * cm

        self.saveState()
        self.setFillColor(colors.HexColor("#F5F7F8"))
        self.setStrokeColor(colors.HexColor("#90A4AE"))
        self.setLineWidth(0.75)
        self.roundRect(x_left, y_bottom, box_w, box_h, 4, stroke=1, fill=1)

        self.setFillColor(colors.HexColor("#1A1A2E"))
        y = y_bottom + box_h - pad - 0.3 * cm
        self._draw_safe_string(x_left + pad, y, "Answer Key", self._ak_font_bold, 10, bold=True)

        for line in lines:
            y -= line_h
            self._draw_safe_string(x_left + pad, y, line, self._ak_font, 9, bold=False)

        self.restoreState()

# ═══════════════════════════════════════════════════════════════
# PDF BUILDER
# ═══════════════════════════════════════════════════════════════
def build_pdf(items: list, doc_title: str = "questions", font_path: str = None,
              font_bold_path: str = None, bg_image_path: str = None,
              show_answers: bool = True) -> BytesIO:
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
        canvas.setStrokeColor(colors.HexColor("#CFD8DC"))
        canvas.setLineWidth(0.5)
        canvas.line(2 * cm, A4[1] - 1.65 * cm, A4[0] - 2 * cm, A4[1] - 1.65 * cm)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.5*cm,
        # Extra bottom margin (vs. the plain 2cm content uses elsewhere)
        # reserves room for the per-page answer-key box so normal flowing
        # text doesn't get laid out underneath where that box will later
        # be stamped on top of it.
        bottomMargin=3.6*cm,
    )

    Q_STYLE = ParagraphStyle(
        "QStyle", fontName=font_name_bold, fontSize=12, leading=16,
        textColor=colors.HexColor("#1A1A2E"), spaceAfter=6, spaceBefore=14,
    )
    OPT_STYLE = ParagraphStyle(
        "OptStyle", fontName=font_name, fontSize=11, leading=15,
        textColor=colors.HexColor("#1A1A2E"), leftIndent=14, spaceAfter=3,
    )
    OPT_CORRECT = ParagraphStyle(
        "OptCorrect", fontName=font_name_bold, fontSize=11, leading=15,
        textColor=colors.HexColor("#1B5E20"), leftIndent=14, spaceAfter=3,
    )
    WRITTEN_TITLE = ParagraphStyle(
        "WTitle", fontName=font_name_bold, fontSize=12, leading=16,
        textColor=colors.HexColor("#1A1A2E"), spaceAfter=4, spaceBefore=14,
    )
    WRITTEN_BODY = ParagraphStyle(
        "WBody", fontName=font_name, fontSize=11, leading=15,
        textColor=colors.HexColor("#37474F"), leftIndent=14, spaceAfter=6,
    )
    NUM_STYLE = ParagraphStyle(
        "NumStyle", fontName=font_name_bold, fontSize=9,
        textColor=colors.HexColor("#90A4AE"), spaceAfter=2,
    )
    IMG_CAPTION = ParagraphStyle(
        "ImgCaption", fontName=font_name, fontSize=9, leading=12,
        textColor=colors.HexColor("#78909C"), spaceAfter=6, spaceBefore=4,
    )

    HR_COLOR = colors.HexColor("#CFD8DC")
    story    = []

    answer_pairs = []  # (question_number, letter) for the per-page answer key
    page_of_idx  = {}   # question_number -> 1-indexed page it lands on, filled in during doc.build()

    for idx, item in enumerate(items, 1):
        block = []  # everything for this one question — kept together on one page

        q_num_label = f"~Q{idx}" if item.get("type") == "mcq" and item.get("correct") is None else f"Q{idx}"
        block.append(Paragraph(q_num_label, NUM_STYLE))

        if item["type"] == "mcq":
            block.append(Paragraph(pdf_safe_markup(item["q"], font_name_bold, bold=True), Q_STYLE))
            if item.get("image"):
                try:
                    img = RLImage(item["image"])
                    if img.imageWidth > PDF_MAX_IMG_WIDTH:
                        scale          = PDF_MAX_IMG_WIDTH / img.imageWidth
                        img.drawWidth  = PDF_MAX_IMG_WIDTH
                        img.drawHeight = img.imageHeight * scale
                    block.append(Spacer(1, 6))
                    block.append(img)
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
                block.append(_PageRecorder(page_of_idx, idx))

        elif item["type"] == "written":
            block.append(Paragraph(pdf_safe_markup(item["title"], font_name_bold, bold=True), WRITTEN_TITLE))
            for line in item["content"].split("\n"):
                line = line.strip()
                if line:
                    block.append(Paragraph(f"{_pdf_marker('•', font_name)} {pdf_safe_markup(_strip_leading_bullet(line), font_name, bold=False)}", WRITTEN_BODY))

        elif item["type"] == "image":
            img_path = item["path"]
            try:
                img = RLImage(img_path)
                if img.imageWidth > PDF_MAX_IMG_WIDTH:
                    scale          = PDF_MAX_IMG_WIDTH / img.imageWidth
                    img.drawWidth  = PDF_MAX_IMG_WIDTH
                    img.drawHeight = img.imageHeight * scale
                block.append(Spacer(1, 8))
                block.append(img)
                if item.get("caption"):
                    safe_cap = pdf_safe_markup(item["caption"], font_name, bold=False)
                    block.append(Paragraph(f"{safe_cap}", IMG_CAPTION))
                block.append(Spacer(1, 4))
            except Exception as e:
                block.append(Paragraph(f"[Image error: {pdf_safe_markup(str(e), font_name)}]", WRITTEN_BODY))

        story.append(KeepTogether(block))

        if idx < len(items):
            story.append(Spacer(1, 6))
            story.append(HRFlowable(width="100%", thickness=0.5, color=HR_COLOR, spaceAfter=4))

    def _make_canvas(*args, **kwargs):
        return _AnswerKeyCanvas(
            *args, answer_pairs=answer_pairs, page_of_idx=page_of_idx,
            font_name=font_name, font_name_bold=font_name_bold, **kwargs,
        )

    doc.build(story, onFirstPage=draw_header, onLaterPages=draw_header, canvasmaker=_make_canvas)
    buffer.seek(0)
    return buffer

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

    pending_img = PENDING_IMAGE.pop(user_id, None)

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
    if pending_img:
        item["image"] = pending_img

    PDF_BUFFER[user_id].append(item)
    item_index = len(PDF_BUFFER[user_id]) - 1

    label = ("🖼 " if pending_img else "") + ("~" if correct_index is None else "") \
            + question[:50] + ("…" if len(question) > 50 else "")
    await update_progress(context, user_id, update.effective_chat.id, latest_label=label)

    if correct_index is None:
        # Telegram hid the answer (quiz still open, not ours) — queue it
        # for a quick button tap instead of silently guessing.
        POLL_WATCH[poll.id] = (user_id, item_index)
        queue = CLARIFY_QUEUE.setdefault(user_id, [])
        queue.append(item_index)
        if len(queue) == 1:  # nothing else currently being asked
            await _ask_next_clarification(context, user_id, update.effective_chat.id)
    else:
        short = question[:50] + ("…" if len(question) > 50 else "")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ اتسجل: {html.escape(short)}",
            parse_mode=ParseMode.HTML,
        )

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
        return f"📝 <b>{html.escape(item['title'])}</b>\n{html.escape(item['content'])}"
    lines = [f"❓ {html.escape(item['q'])}"]
    for i, opt in enumerate(item["options"]):
        mark = "  ✅" if i == item.get("correct") else ""
        lines.append(html.escape(opt) + mark)
    return "\n".join(lines)

def _review_buttons(item_index: int, item: dict) -> InlineKeyboardMarkup:
    if item["type"] == "written":
        rows = [
            [InlineKeyboardButton("✏️ عدّل العنوان", callback_data=f"revedit:{item_index}:title")],
            [InlineKeyboardButton("✏️ عدّل المحتوى", callback_data=f"revedit:{item_index}:content")],
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
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_chat.id
    if user_id in SLEEPING:
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        return

    # ── AWAITING BACKGROUND IMAGE (part of the /pdf_start setup flow) ──
    if AWAITING_BG.get(user_id):
        bg_dir  = os.path.join(IMG_BASE_DIR, str(user_id))
        os.makedirs(bg_dir, exist_ok=True)
        bg_path = os.path.join(bg_dir, "page_background.jpg")
        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(bg_path)
        PDF_BG_IMAGE_PATH[user_id] = bg_path
        del AWAITING_BG[user_id]
        await _finish_pdf_setup(context, user_id, update.message)
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
        PDF_BUFFER[user_id].append({
            "type": "mcq", "q": question,
            "options": labeled_options, "correct": correct_index,
            "image": img_path,
        })
        await update_progress(
            context, user_id, update.effective_chat.id,
            latest_label=f"🖼 {question[:50]}" + ("…" if len(question) > 50 else ""),
        )
        return

    # ── Case 2: non-empty caption that ISN'T a full question — save as a
    # standalone image item (e.g. comparison charts / tables) ──────────
    if caption:
        PDF_BUFFER[user_id].append({
            "type": "image", "path": img_path, "caption": caption,
        })
        await update_progress(
            context, user_id, update.effective_chat.id,
            latest_label=f"Image — {caption}",
        )
        return

    # ── Case 3: no caption — park the image and ask for the question ──
    _clear_pending_image(user_id)
    PENDING_IMAGE[user_id] = img_path
    await update.message.reply_text(
        "🖼 <b>استلمت الصورة!</b>\n"
        "دلوقتي ابعت السؤال والاختيارات (بنفس صيغة الأسئلة المعتادة) "
        "وهيتضاف الصورة تلقائي للسؤال ده.",
        parse_mode=ParseMode.HTML,
    )

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
    AWAITING_BG[user_id] = True
    await update.message.reply_text(
        "✅ الخط اتسجل!\n\n"
        "دلوقتي ابعت صورة تتحط كخلفية لكل صفحة في الـ PDF، أو دوس Skip لو مش عايز خلفية.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Skip", callback_data="bg_skip"),
        ]]),
    )

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

    # ── AWAITING BACKGROUND IMAGE (same — reminder only) ────────────
    if AWAITING_BG.get(user_id):
        await update.message.reply_text(
            "⚠️ محتاج تبعت صورة كخلفية، أو دوس Skip فوق.",
        )
        return

    if user_id not in PDF_BUFFER:
        await update.message.reply_text(MSG_NOT_IN_SESSION)
        return

    try:
        blocks     = re.split(r"\n\s*\n", text)
        any_saved  = False
        last_label = ""

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            # ── WRITTEN ─────────────────────────────────────────
            written = parse_written_question(block)
            if written:
                title, content = written
                PDF_BUFFER[user_id].append({
                    "type":    "written",
                    "title":   title,
                    "content": content,
                })
                any_saved  = True
                last_label = title[:50] + ("…" if len(title) > 50 else "")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"✅ اتسجل: <b>{html.escape(last_label)}</b>",
                    parse_mode=ParseMode.HTML,
                )
                continue

            # ── MCQ ─────────────────────────────────────────────
            lines = normalize_mcq_block(block)
            if len(lines) < 3:
                if _looks_like_mcq_attempt(lines):
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
                continue

            question, raw_options, correct_index, explanation = parse_mcq_lines(lines)

            if correct_index is None or correct_index >= len(raw_options):
                await update.message.reply_text(
                    "⚠️ <b>ما فيش إجابة صح!</b>\n\n"
                    "علّم الإجابة الصحيحة بـ <code>z</code> في نهايتها:\n"
                    "<code>b) الإجابة الصح z</code>",
                    parse_mode=ParseMode.HTML,
                )
                continue

            # An image sent (with no caption / unparseable caption) just
            # before this message is paired with this question.
            pending_img = PENDING_IMAGE.pop(user_id, None)

            labeled_options = [
                f"{string.ascii_uppercase[i]}) {opt}" for i, opt in enumerate(raw_options)
            ]
            item = {
                "type": "mcq", "q": question,
                "options": labeled_options, "correct": correct_index,
            }
            if pending_img:
                item["image"] = pending_img
            PDF_BUFFER[user_id].append(item)
            any_saved  = True
            last_label = ("🖼 " if pending_img else "") + question[:50] + ("…" if len(question) > 50 else "")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ اتسجل: {html.escape(last_label)}",
                parse_mode=ParseMode.HTML,
            )

        if any_saved:
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
            build_progress_text(items), parse_mode=ParseMode.HTML,
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
                "title":   "✏️ اكتب العنوان الجديد:",
                "content": "✏️ اكتب المحتوى الجديد:",
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
        AWAITING_BG[user_id] = True
        await query.edit_message_text(
            f"✅ خط <b>{name}</b> اتحدد!\n\n"
            "دلوقتي ابعت صورة تتحط كخلفية لكل صفحة في الـ PDF، أو دوس Skip لو مش عايز خلفية.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Skip", callback_data="bg_skip"),
            ]]),
        )
        return

    if query.data == "font_skip":
        if not AWAITING_FONT.get(user_id):
            return
        PDF_FONT_PATH.pop(user_id, None)
        PDF_FONT_BOLD_PATH.pop(user_id, None)
        del AWAITING_FONT[user_id]
        AWAITING_BG[user_id] = True
        await query.edit_message_text(
            "⏭ اتخطيت اختيار الخط.\n\n"
            "دلوقتي ابعت صورة تتحط كخلفية لكل صفحة في الـ PDF، أو دوس Skip لو مش عايز خلفية.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Skip", callback_data="bg_skip"),
            ]]),
        )
        return

    if query.data == "bg_skip":
        if not AWAITING_BG.get(user_id):
            return
        del AWAITING_BG[user_id]
        await _finish_pdf_setup(context, user_id, query.message, edit=True)
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
async def _finish_pdf_setup(context: ContextTypes.DEFAULT_TYPE, user_id: int, reply_target, edit: bool = False) -> None:
    """Last step of the /pdf_start flow (name → font → background) — opens
    the actual question buffer and shows the 'PDF mode activated' message.
    edit=True rewrites reply_target in place (the bg_skip button flow);
    edit=False sends a fresh reply (there's no bot-owned message to edit
    when this follows an uploaded background photo instead)."""
    PDF_BUFFER[user_id] = []
    PROGRESS_MSG_ID.pop(user_id, None)
    _clear_pending_image(user_id)
    _clear_clarify_queue(user_id)
    _clear_pending_edit(user_id)
    name = PDF_NAMES.get(user_id, "questions")
    text = (
        f"📥 <b>PDF mode activated</b> — File name: <i>{name}</i>\n\n"
        "• ابعت أسئلة نصية (MCQ أو مكتوبة)\n"
        "• أو <b>فوروارد</b> كويزات أو صور/جداول مقارنة\n\n"
        "اضغط <b>Export as PDF</b> لما تخلص 👇"
    )
    send = reply_target.edit_text if edit else reply_target.reply_text
    await send(text, parse_mode=ParseMode.HTML)

def _reset_pdf_session(user_id: int) -> None:
    """Clears everything tied to an in-progress PDF-collection session —
    used after a successful export and by explicit clear/cancel alike."""
    _cleanup_images(user_id)
    _clear_pending_image(user_id)
    _clear_clarify_queue(user_id)
    _clear_pending_edit(user_id)
    PDF_BUFFER.pop(user_id, None)
    PDF_NAMES.pop(user_id, None)
    AWAITING_NAME.pop(user_id, None)
    AWAITING_FONT.pop(user_id, None)
    AWAITING_BG.pop(user_id, None)
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
    bg_path = PDF_BG_IMAGE_PATH.pop(user_id, None)
    if bg_path and os.path.exists(bg_path):
        try:
            os.remove(bg_path)
        except Exception:
            pass

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
    bg_path        = PDF_BG_IMAGE_PATH.get(session_id)

    import asyncio
    try:
        answered_bytes = await asyncio.to_thread(
            build_pdf, items, name, font_path=font_path,
            font_bold_path=font_bold_path, bg_image_path=bg_path,
            show_answers=True,
        )
        blank_bytes = await asyncio.to_thread(
            build_pdf, items, name, font_path=font_path,
            font_bold_path=font_bold_path, bg_image_path=bg_path,
            show_answers=False,
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

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bails out of whatever's in progress: PDF collection session (or its
    font/background setup step) or a pending image waiting for its question."""
    user_id = update.effective_chat.id
    was_doing_something = bool(
        PDF_BUFFER.get(user_id) or AWAITING_NAME.get(user_id)
        or AWAITING_FONT.get(user_id) or AWAITING_BG.get(user_id)
        or PENDING_IMAGE.get(user_id)
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
