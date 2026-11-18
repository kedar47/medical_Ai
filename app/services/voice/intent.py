import json
import logging
import re
import unicodedata

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.enums import OrderUnit, VoiceIntent
from app.services.matching_service import normalize_text
from app.services.voice.types import ParsedIntent, VoicePendingState

logger = logging.getLogger(__name__)

HINDI_HINTS = {
    "mujhe",
    "mujhko",
    "chahiye",
    "chahiyo",
    "karo",
    "kardo",
    "kijiye",
    "patte",
    "patta",
    "goli",
    "goliyan",
    "dawa",
    "dawai",
    "batao",
    "kitni",
    "kitna",
    "haan",
    "nahi",
    "theek",
    "thik",
    "bhijwao",
    "bhijwa",
    "band",
    "khatam",
    "hatao",
    "badal",
    "wala",
    "wali",
    "kya",
    "hai",
    "hain",
    "dena",
    "dedo",
    "aakhri",
    "akhri",
    "pehla",
    "pehli",
    "doosra",
    "doosri",
    "teesra",
}

UNAMBIGUOUS_HINDI_HINTS = {
    "mujhe",
    "mujhko",
    "chahiye",
    "chahiyo",
    "karo",
    "kardo",
    "kijiye",
    "patte",
    "patta",
    "goli",
    "dawa",
    "dawai",
    "batao",
    "kitni",
    "kitna",
    "haan",
    "nahi",
    "theek",
    "thik",
    "bhijwao",
    "bhijwa",
    "hatao",
    "badal",
    "aakhri",
    "akhri",
    "pehla",
    "pehli",
    "doosra",
    "doosri",
    "teesra",
}

# Whisper spells "चाहिए" in English letters many ways on phone audio:
# chahiye, chahie, chaiye, chiyaye, chaahiye, chahiyee.
_CHAHIYE = re.compile(r"^ch(?:a{1,2}h?i{1,2}|i)y?(?:a?y)?e{1,2}$")

# English words that show the caller really switched to English. A Hindi
# session switches on one request word ("add clomin", "help") or two small
# words, so a medicine name, "yes", or Whisper noise such as "Thank you" or
# "THAT IS CLIP" keeps a Hindi call in Hindi. Units and numbers are left out:
# Hindi-mode Whisper writes पत्ते as "STRIPS" and numbers as digits.
ENGLISH_REQUEST_WORDS = {
    "add", "need", "want", "please", "give", "remove", "delete", "change",
    "update", "help", "show",
}
ENGLISH_SMALL_WORDS = {
    "i", "me", "my", "the", "of", "to", "is", "it", "can", "would", "like",
    "make", "quantity", "last", "item", "what", "how",
}


def fold_latin(token: str) -> str:
    """Drop accents from an English-letter token (CHAHIÉ -> chahie)."""
    if any("ऀ" <= character <= "ॿ" for character in token):
        return token
    decomposed = unicodedata.normalize("NFKD", token)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _is_hindi_filler(token: str) -> bool:
    return token in FILLER_WORDS or bool(_CHAHIYE.match(token))


GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "namaste",
    "namaskar",
    "नमस्ते",
    "नमस्कार",
    "हैलो",
    "हेलो",
}

CONFIRM_PHRASES = (
    "confirm",
    "please confirm",
    "place order",
    "place the order",
    "book order",
    "book the order",
    "order confirm",
    "confirm order",
    "order kar do",
    "order kardo",
    "confirm kar do",
    "confirm kardo",
    "yes",
    "yes please",
    "ok",
    "okay",
    "haan",
    "han",
    "ha",
    "theek hai",
    "thik hai",
    "हाँ",
    "हां",
    "कन्फर्म",
    "कन्फर्म करो",
    "ऑर्डर कन्फर्म",
    "ऑर्डर कर दो",
    "ठीक है",
    "हो गया",
    # Live-call spellings: "order confirmed.", "2 STOPS KANFUM".
    "confirmed",
    "order confirmed",
    "conform",
    "kanfarm",
    "kanfirm",
    "kanfum",
    # "ऑर्डर कर दो" as Whisper writes it ("ORAT KARDO").
    "orat kardo",
    "orat kar do",
    "ordar kardo",
    "ordar kar do",
    "haan ji",
    "ji haan",
    "कंफर्म",
    "कनफर्म",
    "कंफर्म करो",
    "कन्फर्म कीजिए",
    "कंफर्म कीजिए",
    "जी हाँ",
    "हाँ जी",
)

CANCEL_PHRASES = (
    "cancel",
    "cancel order",
    "cancel the order",
    "order cancel",
    "cancel kar do",
    "cancel kardo",
    "cancel kar",
    "mat bhejo",
    "नहीं भेजो",
    "कैंसिल",
    "कैंसिल करो",
    "रद्द",
    "रद्द करो",
)

SUMMARY_PHRASES = (
    "summary",
    "order summary",
    "show summary",
    "show the summary",
    "review order",
    "review my order",
    "order review",
    "order batao",
    "order dikhao",
    "kya order",
    "kya hai order",
    "list batao",
    "सारांश",
    "सारांश दो",
    "ऑर्डर बताओ",
    "ऑर्डर दिखाओ",
    "ऑर्डर बताइए",
    "ऑर्डर दिखाइए",
    "ऑर्डर सारांश",
)

END_PHRASES = (
    "bye",
    "goodbye",
    "good bye",
    "end call",
    "call end",
    "end the call",
    "hang up",
    "phone rakho",
    "phone rakh do",
    "call band",
    "band karo",
    "अलविदा",
    "फोन रखो",
    "फोन रख दो",
    "कॉल बंद",
    "कॉल खत्म",
)

HELP_PHRASES = (
    "help",
    "madad",
    "मदद",
    "what can you do",
    "how does this work",
    "how to use",
    "kya kar sakte",
    "क्या कर सकते",
    "कैसे करें",
)

REMOVE_PHRASES = (
    "remove",
    "delete",
    "remove item",
    "delete item",
    "remove last",
    "delete last",
    "hatao",
    "hata do",
    "hata",
    "nikaal",
    "nikalo",
    "nikaal do",
    "हटाओ",
    "हटा दो",
    "निकालो",
    "निकाल दो",
)

UPDATE_PHRASES = (
    "update",
    "update item",
    "change",
    "change quantity",
    "change item",
    "modify",
    "badal",
    "badal do",
    "बदल",
    "बदल दो",
    "quantity change",
    "make it",
)

QUANTITY_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "a": 1,
    "an": 1,
    "ek": 1,
    "do": 2,
    "doe": 2,
    "dho": 2,
    "doh": 2,
    "dou": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "panch": 5,
    "paanch": 5,
    "chhah": 6,
    "cheh": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
    "gyarah": 11,
    "barah": 12,
    "pandrah": 15,
    "bees": 20,
    "एक": 1,
    "दो": 2,
    "तीन": 3,
    "चार": 4,
    "पांच": 5,
    "पाँच": 5,
    "छह": 6,
    "छः": 6,
    "सात": 7,
    "आठ": 8,
    "नौ": 9,
    "दस": 10,
    "ग्यारह": 11,
    "बारह": 12,
    "पंद्रह": 15,
    "बीस": 20,
}

UNIT_ALIASES: dict[OrderUnit, tuple[str, ...]] = {
    OrderUnit.STRIP: (
        "strips",
        "strip",
        "pattas",
        "patta",
        "patte",
        "pattey",
        "pate",
        "patey",
        "pathe",
        "fatte",
        "phatte",
        # Whisper's spellings of स्ट्रिप on phone audio ("DOSCRIP", "dos trip").
        "scrip",
        "scrips",
        "stip",
        "stips",
        "strep",
        "streps",
        "stripe",
        "stripes",
        "trip",
        "trips",
        "steps",
        "istrip",
        "istrips",
        "shrip",
        "shrips",
        "इस्ट्रिप",
        "स्त्रिप",
        "स्ट्रिप्स",
        "स्ट्रिप",
        "स्ट्रीप्स",
        "स्ट्रीप",
        "पत्ते",
        "पत्ता",
        "पत्तों",
        "पत्ती",
        "पट्टी",
        "पट्टे",
        "पते",
        "पटे",
    ),
    OrderUnit.TABLET: (
        "tablets",
        "tablet",
        "tabs",
        "tab",
        "golis",
        "goli",
        "गोलियां",
        "गोली",
        "टैबलेट",
        "टैबलेट्स",
    ),
    OrderUnit.BOTTLE: (
        "bottles",
        "bottle",
        "botal",
        "बोतल",
        "बोतलें",
        "सिरप",
        "syrup",
    ),
    OrderUnit.BOX: (
        "boxes",
        "box",
        "dabba",
        "dabbe",
        "डिब्बा",
        "डिब्बे",
        "बॉक्स",
    ),
    OrderUnit.UNIT: ("units", "unit", "यूनिट"),
}

UNIT_ALIAS_LOOKUP = {
    alias.casefold(): unit
    for unit, aliases in UNIT_ALIASES.items()
    for alias in aliases
}

_QUANTITY_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(QUANTITY_WORDS, key=len, reverse=True)
)
_UNIT_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(UNIT_ALIAS_LOOKUP, key=len, reverse=True)
)
_QUANTITY_BEFORE_UNIT = re.compile(
    rf"(?<!\w)(?P<qty>\d+|{_QUANTITY_PATTERN})\s*"
    rf"(?:x\s*)?(?P<unit>{_UNIT_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_QUANTITY_AFTER_UNIT = re.compile(
    rf"(?<!\w)(?P<unit>{_UNIT_PATTERN})\s*"
    rf"(?:x\s*)?(?P<qty>\d+|{_QUANTITY_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_UNIT_ONLY = re.compile(
    rf"(?<!\w)(?P<unit>{_UNIT_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_QUANTITY_KEYWORD = re.compile(
    rf"(?:quantity|qty|मात्रा|संख्या|kitni|kitna)[^\d]{{0,20}}"
    rf"(?P<qty>\d+|{_QUANTITY_PATTERN})",
    re.IGNORECASE,
)
_QUANTITY_ACTION = re.compile(
    rf"(?<!\w)(?P<qty>\d+|{_QUANTITY_PATTERN})\s*"
    rf"(?:kar do|kardo|कर दो|kijiye|कीजिए)(?!\w)",
    re.IGNORECASE,
)
_STANDALONE_NUMBER = re.compile(r"(?<![\w-])\d{1,4}(?![\w-])")
_STRENGTH_WITH_UNIT = re.compile(
    r"(?<!\d)\d{1,4}(?:\s*(?:mg|mcg|ml|gm|g|iu|%))(?!\w)",
    re.IGNORECASE,
)
# Pack size read back from a catalogue option, e.g. "15S" or "15'S".
_PACK_SIZE = re.compile(r"(?<!\w)\d{1,3}\s*'?\s*s(?!\w)", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")

FILLER_WORDS = {
    "i",
    "want",
    "need",
    "add",
    "please",
    "some",
    "of",
    "the",
    "a",
    "an",
    "for",
    "me",
    "my",
    "medicine",
    "medicines",
    "med",
    "dawa",
    "dawai",
    "davai",
    "चाहिए",
    "चाहिये",
    "चाहीए",
    "चाइए",
    "चालिए",
    "muje",
    "mujhey",
    "मुझे",
    "मुझको",
    "को",
    "कृपया",
    "जोड़ो",
    "जोड़िए",
    "जोड़",
    "डालो",
    "डालिए",
    "karo",
    "kar",
    "kardo",
    "dena",
    "dedo",
    "de",
    "dijiye",
    "लाओ",
    "लाइए",
    "chahiye",
    "mujhe",
    "mujhko",
    "ka",
    "ki",
    "ke",
    "se",
    "mein",
    "and",
    "aur",
    "का",
    "की",
    "के",
    "से",
    "में",
    "और",
    "also",
    "bhi",
    "भी",
    "जरूरत",
    "जरुरत",
    "है",
    "हैं",
    "hai",
    "hain",
    "want",
    "give",
    "lao",
    "do",
}

ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "pehla": 1,
    "pehli": 1,
    "pahla": 1,
    "pahli": 1,
    "doosra": 2,
    "doosri": 2,
    "dusra": 2,
    "dusri": 2,
    "teesra": 3,
    "teesri": 3,
    # Whisper spellings on phone audio.
    "pela": 1,
    "pehela": 1,
    "pahala": 1,
    "pahela": 1,
    "pehle": 1,
    "pahle": 1,
    "doosara": 2,
    "dusara": 2,
    "doosre": 2,
    "dusre": 2,
    "tisra": 3,
    "tisri": 3,
    "tesra": 3,
    "teesara": 3,
    "teesre": 3,
    "tisre": 3,
    "पहला": 1,
    "पहले": 1,
    "दुसरा": 2,
    "दूसरे": 2,
    "तीसरे": 3,
    "पहली": 1,
    "दूसरा": 2,
    "दूसरी": 2,
    "तीसरा": 3,
    "तीसरी": 3,
    "चौथा": 4,
    "पांचवां": 5,
}

CONTROL_INTENTS = {
    VoiceIntent.CONFIRM_ORDER,
    VoiceIntent.CANCEL_ORDER,
    VoiceIntent.SHOW_SUMMARY,
    VoiceIntent.END_CALL,
    VoiceIntent.HELP,
}


def detect_language(text: str, default: str = "hi") -> str:
    if any("\u0900" <= character <= "\u097f" for character in text):
        return "hi"
    normalized = normalize_text(text)
    tokens = {fold_latin(token) for token in normalized.split()}
    if tokens & UNAMBIGUOUS_HINDI_HINTS:
        return "hi"
    if any(_CHAHIYE.match(token) for token in tokens):
        return "hi"
    if len(tokens & HINDI_HINTS) >= 2:
        return "hi"
    if not any(character.isalpha() for character in text):
        return default
    if default == "hi":
        if tokens & ENGLISH_REQUEST_WORDS or len(tokens & ENGLISH_SMALL_WORDS) >= 2:
            return "en"
        return "hi"
    return "en"


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    if not normalized_phrase:
        return False
    text_tokens = normalized_text.split()
    phrase_tokens = normalized_phrase.split()
    length = len(phrase_tokens)
    return any(
        text_tokens[index : index + length] == phrase_tokens
        for index in range(len(text_tokens) - length + 1)
    )


def _contains_any(normalized_text: str, phrases: tuple[str, ...]) -> bool:
    return any(
        _contains_phrase(normalized_text, phrase) for phrase in phrases
    )


def _parse_quantity_value(value: str) -> int | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    if normalized.isdigit():
        quantity = int(normalized)
        return quantity if quantity > 0 else None
    quantity = QUANTITY_WORDS.get(normalized.casefold())
    return quantity if quantity and quantity > 0 else None


def _unit_for_alias(value: str) -> OrderUnit | None:
    return UNIT_ALIAS_LOOKUP.get(value.casefold())


_STRIP_WORD = r"(?:strips?|scrips?|stips?|streps?|stripes?|trips?|istrips?|shrips?)"
# "DOESTRIP", "DOSCRIP", "teenstrip": number and unit heard as one word.
_MERGED_QUANTITY_UNIT = re.compile(
    rf"(?<!\w)(ek|do|doe|dho|doh|teen|char|chaar|panch|paanch|\d{{1,3}})(?={_STRIP_WORD}(?!\w))",
    re.IGNORECASE,
)
# "those strips", "egg strip": Whisper hears दो / एक as English words.
_NUMBER_AS_ENGLISH = re.compile(
    rf"(?<!\w)(those|though|dose|doze|dough|dos|egg|ake|aek)\s+(?={_STRIP_WORD}(?!\w))",
    re.IGNORECASE,
)
_NUMBER_AS_ENGLISH_VALUE = {"egg": "ek", "ake": "ek", "aek": "ek"}
# "2-strip": a hyphen between a number and a word.
_NUMBER_HYPHEN = re.compile(r"(?<=\d)-(?=[^\W\d_])")
# दो on its own, as an answer to "how many?".
DO_AS_ENGLISH_WORDS = {"those", "though", "dough", "doe", "dho", "doh"}


def normalize_spoken_quantities(text: str) -> str:
    text = _NUMBER_HYPHEN.sub(" ", text)
    text = _MERGED_QUANTITY_UNIT.sub(lambda match: match.group(1) + " ", text)
    return _NUMBER_AS_ENGLISH.sub(
        lambda match: _NUMBER_AS_ENGLISH_VALUE.get(match.group(1).lower(), "do") + " ", text
    )


_AMBIGUOUS_QUANTITY_CONNECTOR = r"(?:or|ya|या|और|to|[-–—])"
_AMBIGUOUS_QUANTITY_RANGE = re.compile(
    rf"(?<!\w)(?P<first>\d{{1,3}}|{_QUANTITY_PATTERN})\s*"
    rf"{_AMBIGUOUS_QUANTITY_CONNECTOR}\s*"
    rf"(?P<second>\d{{1,3}}|{_QUANTITY_PATTERN})(?!\w)",
    re.IGNORECASE,
)


def quantity_values(text: str) -> list[int]:
    cleaned = normalize_spoken_strengths(normalize_spoken_quantities(text))
    cleaned = re.sub(r"(?<!\w)MED-\d+(?!\w)", " ", cleaned, flags=re.IGNORECASE)
    for regex in (_STRENGTH_WITH_UNIT, _PACK_SIZE):
        cleaned = regex.sub(" ", cleaned)
    values: list[int] = []
    for token in normalize_text(cleaned).split():
        if token in {"a", "an"}:
            continue
        value = _parse_quantity_value(token)
        if value is None:
            # "CHARIS TIP" for "चार स्ट्रिप": a Hindi number word with sounds
            # stuck to it. Only words of 3+ letters, so "do" is not guessed.
            value = next(
                (number for word, number in _HINDI_NUMBER_PREFIXES if token.startswith(word)),
                None,
            )
        if value is not None and value not in values:
            values.append(value)
    return values


def has_ambiguous_quantity(text: str) -> bool:
    cleaned = normalize_spoken_quantities(text)
    range_match = _AMBIGUOUS_QUANTITY_RANGE.search(cleaned)
    if range_match is not None:
        first = _parse_quantity_value(range_match.group("first"))
        second = _parse_quantity_value(range_match.group("second"))
        if first is not None and second is not None and first != second:
            return True

    values = {
        value
        for regex in (_QUANTITY_BEFORE_UNIT, _QUANTITY_AFTER_UNIT)
        for found in regex.finditer(cleaned)
        if (value := _parse_quantity_value(found.group("qty"))) is not None
    }
    return len(values) > 1


def has_ambiguous_quantity_answer(text: str) -> bool:
    return len(quantity_values(text)) > 1


def first_quantity_word(text: str) -> int | None:
    """Return the only quantity candidate in a bare quantity answer.

    Multiple candidates ("4-8", "five or six") are uncertain, so no number is
    returned and the caller hears a repeat prompt instead.
    """
    values = quantity_values(text)
    return values[0] if len(values) == 1 else None


_HINDI_NUMBER_PREFIXES = sorted(
    (
        (word, number) for word, number in QUANTITY_WORDS.items()
        if word.isascii() and len(word) >= 3 and word not in {"one", "two", "six", "ten", "an"}
        and word in {"teen", "char", "chaar", "panch", "paanch", "saat", "aath", "das", "barah", "bees"}
    ),
    key=lambda pair: -len(pair[0]),
)


_NAME_SUFFIX_UNITS = {"tab", "tabs"}


def _unit_is_part_of_name(text: str, match) -> bool:
    """"CLOMIN TAB" is a medicine name, not the unit tablet: a short unit word
    right after another word (not a number) belongs to that name."""
    if match.group("unit").casefold() not in _NAME_SUFFIX_UNITS:
        return False
    if "qty" in match.groupdict() and match.start("qty") < match.start("unit"):
        return False  # "2 tab": number first, so it is the unit.
    before = re.search(r"([^\W\d_]+)\W*$", text[: match.start("unit")])
    return bool(before) and before.group(1).casefold() not in QUANTITY_WORDS


def _first_unit_match(regex, text):
    for match in regex.finditer(text):
        if not _unit_is_part_of_name(text, match):
            return match
    return None


def extract_quantity_unit(
    text: str,
) -> tuple[int | None, OrderUnit | None, str]:
    text = normalize_spoken_quantities(text)
    candidates = []
    for regex in (_QUANTITY_BEFORE_UNIT, _QUANTITY_AFTER_UNIT):
        match = _first_unit_match(regex, text)
        if match is not None:
            candidates.append(match)
    if candidates:
        match = min(candidates, key=lambda item: item.start())
        quantity = _parse_quantity_value(match.group("qty"))
        unit = _unit_for_alias(match.group("unit"))
        remaining = text[: match.start()] + " " + text[match.end() :]
        return quantity, unit, remaining

    match = _first_unit_match(_UNIT_ONLY, text)
    if match is None:
        return None, None, text
    unit = _unit_for_alias(match.group("unit"))
    remaining = text[: match.start()] + " " + text[match.end() :]
    return None, unit, remaining


def extract_quantity(text: str) -> int | None:
    quantity, _, _ = extract_quantity_unit(text)
    if quantity is not None:
        return quantity
    for regex in (_QUANTITY_KEYWORD, _QUANTITY_ACTION):
        match = regex.search(text)
        if match is not None:
            quantity = _parse_quantity_value(match.group("qty"))
            if quantity is not None:
                return quantity
    normalized = normalize_text(text)
    return _parse_quantity_value(normalized)


def normalize_spoken_strengths(text: str) -> str:
    # Exact linguistic forms only, never guess an unclear spoken strength.
    # ASR may insert a comma between a strength number and its spoken unit.
    text = re.sub(r"[,،।]", " ", text)
    # Latin forms include phone-audio mishearings of "MG" right after a number
    # ("200, NJ"). Only the digits pick a variant, and the caller still hears
    # the order summary and must confirm it.
    units = (
        r"(?:एम\s*जी|मिली\s*ग्राम|मिलीग्राम|mg|mgs|milli\s*grams?|"
        r"m\s*g|em\s*ji|em\s*jee|nj|mj)"
    )
    hundreds = {
        200: ("दो सौ", "टू हंड्रेड", "टू हन्ड्रेड", "two hundred"),
        400: ("चार सौ", "फोर हंड्रेड", "four hundred"),
        600: ("छह सौ", "छः सौ", "सिक्स हंड्रेड", "six hundred"),
    }
    for value, forms in hundreds.items():
        alternatives = "|".join(re.escape(form) for form in forms)
        text = re.sub(rf"(?<!\w)(?:{alternatives})\s*{units}(?!\w)", f"{value}mg", text, flags=re.IGNORECASE)
    text = re.sub(rf"(?<!\w)(\d+)\s*{units}(?!\w)", lambda match: f"{int(match.group(1))}mg", text, flags=re.IGNORECASE)
    return text


def clean_medicine_query(text: str) -> str:
    remaining = normalize_spoken_strengths(text)
    for regex in (_STRENGTH_WITH_UNIT, _PACK_SIZE, _STANDALONE_NUMBER):
        remaining = regex.sub(" ", remaining)
    normalized = normalize_text(remaining)
    tokens = [
        folded
        for folded in (fold_latin(token) for token in normalized.split())
        if not _is_hindi_filler(folded)
    ]
    return " ".join(tokens)


def extract_ordinal(text: str) -> int | None:
    normalized = normalize_text(text)
    if normalized.isdigit():
        value = int(normalized)
        return value if value > 0 else None
    tokens = normalized.split()
    for token in tokens:
        ordinal = ORDINAL_WORDS.get(token)
        if ordinal is not None:
            return ordinal
    match = re.search(
        r"(?:option|number|candidate|item|विकल्प|नंबर)\s+(\d+)",
        normalized,
    )
    if match is not None:
        value = int(match.group(1))
        return value if value > 0 else None
    return None


def extract_strength_tokens(text: str) -> set[str]:
    remaining = normalize_spoken_strengths(extract_quantity_unit(text)[2])
    return {str(int(value)) for value in _DIGITS.findall(remaining)}


def description_strength_tokens(description: str) -> set[str]:
    return set(_DIGITS.findall(description))


def contains_medicine_id(text: str) -> str | None:
    match = re.search(r"\bMED-\d+\b", text, re.IGNORECASE)
    return match.group(0).upper() if match is not None else None


def mentions_last_item(text: str) -> bool:
    normalized = normalize_text(text)
    tokens = set(normalized.split())
    return bool(
        tokens
        & {
            "last",
            "latest",
            "akhri",
            "aakhri",
            "आखिरी",
            "अंतिम",
        }
    )


def _detect_intent(normalized_text: str, has_item_signal: bool) -> VoiceIntent:
    if _contains_any(normalized_text, CANCEL_PHRASES):
        return VoiceIntent.CANCEL_ORDER
    if _contains_any(normalized_text, END_PHRASES):
        return VoiceIntent.END_CALL
    if _contains_any(normalized_text, SUMMARY_PHRASES):
        return VoiceIntent.SHOW_SUMMARY
    if _contains_any(normalized_text, CONFIRM_PHRASES):
        return VoiceIntent.CONFIRM_ORDER
    if _contains_any(normalized_text, HELP_PHRASES):
        return VoiceIntent.HELP
    if _contains_any(normalized_text, REMOVE_PHRASES):
        return VoiceIntent.REMOVE_ITEM
    if _contains_any(normalized_text, UPDATE_PHRASES):
        return VoiceIntent.UPDATE_ITEM
    tokens = normalized_text.split()
    if tokens and set(tokens).issubset(GREETING_WORDS):
        return VoiceIntent.HELP
    if extract_ordinal(normalized_text) is not None:
        return VoiceIntent.SELECT_CANDIDATE
    if has_item_signal:
        return VoiceIntent.ADD_ITEM
    return VoiceIntent.UNKNOWN


class DeterministicIntentParser:
    def parse(
        self,
        text: str,
        *,
        language: str,
        pending: VoicePendingState | None = None,
    ) -> ParsedIntent:
        detected_language = detect_language(text, default=language)
        normalized = normalize_text(text)
        quantity, unit, remaining = extract_quantity_unit(text)
        medicine_query = clean_medicine_query(remaining)
        medicine_id = contains_medicine_id(text)
        has_item_signal = bool(medicine_query or medicine_id or quantity or unit)
        intent = _detect_intent(normalized, has_item_signal)

        if intent in {VoiceIntent.UPDATE_ITEM, VoiceIntent.REMOVE_ITEM}:
            item_reference = text
        else:
            item_reference = None

        return ParsedIntent(
            intent=intent,
            language=detected_language,
            medicine_query=medicine_query or None,
            medicine_id=medicine_id,
            quantity=quantity,
            unit=unit,
            item_reference=item_reference,
            source="deterministic",
        )


class LLMIntentOutput(BaseModel):
    intent: str = "unknown"
    medicine_name: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    unit: str | None = None
    item_reference: str | None = None
    candidate_reference: str | None = None


class OllamaIntentParser:
    def __init__(self, settings: Settings):
        self.settings = settings

    def parse(
        self,
        text: str,
        *,
        language: str,
        pending: VoicePendingState | None = None,
    ) -> ParsedIntent:
        pending_text = (
            pending.model_dump_json() if pending is not None else "none"
        )
        system_prompt = (
            "Extract medicine-order intent from the customer's Hindi, English, "
            "or Hinglish utterance. Return one JSON object only with keys: "
            "intent, medicine_name, quantity, unit, item_reference, "
            "candidate_reference. Allowed intents are add_item, update_item, "
            "remove_item, help, unknown. Never invent medicine ids or "
            "medicines. Unit must be one of TABLET, STRIP, BOTTLE, BOX, UNIT. "
            "Do not confirm, cancel, summarize, or end a call. Current pending "
            f"state: {pending_text}"
        )
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "options": {"temperature": 0, "num_predict": 200},
        }
        url = self.settings.ollama_base_url.rstrip("/") + "/api/chat"
        with httpx.Client(
            timeout=self.settings.ollama_timeout_seconds
        ) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        if isinstance(content, str):
            content = content.strip().strip("`")
            if content.startswith("json"):
                content = content[4:].lstrip()
        try:
            output = LLMIntentOutput.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ValueError("Invalid JSON returned by Ollama.") from exc

        try:
            intent = VoiceIntent(output.intent)
        except ValueError:
            intent = VoiceIntent.UNKNOWN
        try:
            unit = OrderUnit(output.unit) if output.unit else None
        except ValueError:
            unit = None

        return ParsedIntent(
            intent=intent,
            language=detect_language(text, default=language),
            medicine_query=output.medicine_name,
            quantity=output.quantity,
            unit=unit,
            item_reference=output.item_reference,
            candidate_reference=output.candidate_reference,
            source="ollama",
        )


class HybridIntentParser:
    def __init__(
        self,
        deterministic: DeterministicIntentParser,
        llm: OllamaIntentParser,
    ):
        self.deterministic = deterministic
        self.llm = llm

    def parse(
        self,
        text: str,
        *,
        language: str,
        pending: VoicePendingState | None = None,
    ) -> ParsedIntent:
        deterministic = self.deterministic.parse(
            text, language=language, pending=pending
        )
        if deterministic.intent in CONTROL_INTENTS:
            return deterministic
        if deterministic.intent in {
            VoiceIntent.ADD_ITEM,
            VoiceIntent.UPDATE_ITEM,
            VoiceIntent.REMOVE_ITEM,
            VoiceIntent.SELECT_CANDIDATE,
        }:
            return deterministic
        try:
            llm_result = self.llm.parse(
                text, language=language, pending=pending
            )
        except Exception as exc:
            logger.warning("Ollama intent extraction failed: %s", exc)
            return deterministic
        if llm_result.intent in CONTROL_INTENTS:
            return deterministic
        if (
            llm_result.intent == VoiceIntent.UNKNOWN
            and not llm_result.medicine_query
        ):
            return deterministic
        deterministic.medicine_query = (
            llm_result.medicine_query or deterministic.medicine_query
        )
        deterministic.quantity = (
            llm_result.quantity or deterministic.quantity
        )
        deterministic.unit = llm_result.unit or deterministic.unit
        deterministic.item_reference = (
            llm_result.item_reference or deterministic.item_reference
        )
        deterministic.candidate_reference = (
            llm_result.candidate_reference or deterministic.candidate_reference
        )
        deterministic.intent = llm_result.intent
        deterministic.source = "ollama"
        return deterministic


def build_intent_parser(settings: Settings) -> DeterministicIntentParser:
    deterministic = DeterministicIntentParser()
    if not settings.voice_llm_enabled:
        return deterministic
    return HybridIntentParser(
        deterministic,
        OllamaIntentParser(settings),
    )
