
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "paanch": 5,
    "एक": 1,
    "दो": 2,
    "तीन": 3,
    "चार": 4,
    "पांच": 5,
    "पाँच": 5,
}

UNITS = {
    "strips": "STRIP",
    "strip": "STRIP",
    "स्ट्रिप": "STRIP",
    "पत्ते": "STRIP",
    "पते": "STRIP",
    "पत्ती": "STRIP",
    "पट्टी": "STRIP",
    "tablets": "TABLET",
    "tablet": "TABLET",
    "गोलियाँ": "TABLET",
    "गोलियां": "TABLET",
    "गोली": "TABLET",
    "bottles": "BOTTLE",
    "bottle": "BOTTLE",
    "बोतल": "BOTTLE",
    "boxes": "BOX",
    "box": "BOX",
}

UPDATE_MARKERS = (
    "quantity",
    "update",
    "change",
    "modify",
    "increase",
    "decrease",
    "instead",
    "kar do",
    "kardo",
    "badal",
    "badhao",
    "nahi",
    "nahin",
    "मात्रा",
    "बदल",
    "कर दो",
    "नहीं",
    "बढ़ा",
    "घटा",
)

NUMBER_PART = "|".join(
    re.escape(word)
    for word in sorted(NUMBER_WORDS, key=len, reverse=True)
)

UNIT_PART = "|".join(
    re.escape(unit)
    for unit in sorted(UNITS, key=len, reverse=True)
)

QUANTITY_PATTERN = re.compile(
    rf"(?<!\w)(\d+|{NUMBER_PART})"
    rf"\s*({UNIT_PART})(?!\w)",
    re.IGNORECASE,
)

STRENGTH_PATTERN = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)"
    r"\s*(mcg|mg|ml|gm|g)(?!\w)",
    re.IGNORECASE,
)

# Strength must appear directly after the medicine
# name, e.g. "Brufen 400 ki quantity..."
REQUESTED_STRENGTH_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d+)?)"
    r"\s*(mcg|mg|ml|gm|g)?"
    r"(?=\s|$|[.,])",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", text)
        .casefold()
        .split()
    )


@dataclass
class CorrectionResult:
    intent: str
    item_id: Any | None = None
    medicine_id: str | None = None
    quantity: int | None = None
    unit: str | None = None
    message: str = ""


def is_correction_request(text: str) -> bool:
    value = normalize(text)

    return any(
        marker in value
        for marker in UPDATE_MARKERS
    )


def parse_quantity(text: str):
    value = normalize(text)
    matches = list(QUANTITY_PATTERN.finditer(value))

    if not matches:
        return None, None

    match = matches[-1]

    raw_number = match.group(1)
    raw_unit = match.group(2)

    quantity = (
        int(raw_number)
        if raw_number.isdigit()
        else NUMBER_WORDS.get(raw_number)
    )

    return quantity, UNITS.get(raw_unit)


def extract_catalogue_strength(description: str):
    match = STRENGTH_PATTERN.search(
        normalize(description)
    )

    if not match:
        return None

    return (
        match.group(1),
        match.group(2),
    )


def get_requested_strength(
    text: str,
    medicine_name: str,
):
    value = normalize(text)
    name = normalize(medicine_name)

    # Match the full medicine name at word boundaries.
    pattern = re.compile(
        rf"(?<!\w){re.escape(name)}(?!\w)"
    )

    match = pattern.search(value)

    if not match:
        return False, None

    remaining = value[match.end():]

    strength_match = REQUESTED_STRENGTH_PATTERN.match(
        remaining
    )

    if strength_match is None:
        return True, None

    return True, (
        strength_match.group(1),
        strength_match.group(2),
    )


def detect_correction(
    text: str,
    draft_items: list[dict],
) -> CorrectionResult:

    # Step 1: Check correction intent.
    if not is_correction_request(text):
        return CorrectionResult(
            intent="NOT_UPDATE"
        )

    # Step 2: Parse new quantity.
    quantity, unit = parse_quantity(text)

    if quantity is None or unit is None:
        return CorrectionResult(
            intent="CLARIFY_QUANTITY",
            message=(
                "Please specify the new quantity "
                "and unit."
            ),
        )

    if quantity <= 0:
        return CorrectionResult(
            intent="INVALID_QUANTITY",
            message="Quantity must be positive.",
        )

    # Step 3: Match against existing draft items.
    candidates = []

    for item in draft_items:
        medicine_name = item.get("medicine_name", "")

        found, requested_strength = (
            get_requested_strength(
                text,
                medicine_name,
            )
        )

        if not found:
            continue

        catalogue_strength = (
            extract_catalogue_strength(
                item.get("description", "")
            )
        )

        # Customer specified a strength.
        if requested_strength is not None:
            if catalogue_strength is None:
                continue

            req_value, req_unit = requested_strength
            cat_value, cat_unit = catalogue_strength

            # Compare strength numbers numerically:
            # 400 == 400, but 400 != 200.
            try:
                same_value = (
                    float(req_value) == float(cat_value)
                )
            except ValueError:
                same_value = False

            if not same_value:
                continue

            # If customer said "400mg", ensure
            # catalogue unit also matches.
            if req_unit is not None:
                if req_unit != cat_unit:
                    continue

        candidates.append(item)

    # Step 4: No matching draft item.
    if len(candidates) == 0:
        return CorrectionResult(
            intent="CLARIFY_MEDICINE",
            message=(
                "I could not identify the existing "
                "medicine. Please specify its exact "
                "name and strength."
            ),
        )

    # Step 5: Multiple variants remain ambiguous.
    if len(candidates) > 1:
        return CorrectionResult(
            intent="CLARIFY_MEDICINE",
            message=(
                "Multiple medicine variants match. "
                "Please specify the exact strength."
            ),
        )

    # Step 6: Return a validated update action.
    item = candidates[0]

    return CorrectionResult(
        intent="UPDATE_ITEM",
        item_id=item["id"],
        medicine_id=item["medicine_id"],
        quantity=quantity,
        unit=unit,
        message="Quantity update recognized.",
    )
