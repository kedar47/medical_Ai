import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas.matching import MedicineMatchCandidate
from app.services.matching_service import normalize_text

_STRENGTH_PATTERN = re.compile(
    r"(?<!\d)(?P<value>\d{1,4})"
    r"(?:\s*(?P<unit>mg|mcg|ml|gm|g|iu|%))?(?!\w)",
    re.IGNORECASE,
)
_PACK_SIZE_PATTERN = re.compile(
    r"(?<!\w)(?P<value>\d{1,3})\s*(?:'?\s*s|\*\s*s)(?!\w)",
    re.IGNORECASE,
)
_STRENGTH_UNIT_PATTERN = re.compile(
    r"(?<!\w)(?P<unit>mg|mgs|milligrams?|milli\s*grams?|mcg|micrograms?|"
    r"ml|millilitres?|milliliters?|gm|grams?|g|iu|%)(?!\w)",
    re.IGNORECASE,
)

_UNIT_ALIASES = {
    "mg": "mg",
    "mgs": "mg",
    "milligram": "mg",
    "milligrams": "mg",
    "milligram": "mg",
    "milligram ": "mg",
    "milli gram": "mg",
    "milli grams": "mg",
    "mcg": "mcg",
    "microgram": "mcg",
    "micrograms": "mcg",
    "ml": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "gm": "gm",
    "gram": "gm",
    "grams": "gm",
    "g": "gm",
    "iu": "iu",
    "%": "%",
}

_FORM_ALIASES = {
    "tab": "tab",
    "tabs": "tab",
    "tablet": "tab",
    "tablets": "tab",
    "cap": "cap",
    "caps": "cap",
    "capsule": "cap",
    "capsules": "cap",
    "inj": "inj",
    "injs": "inj",
    "injection": "inj",
    "injections": "inj",
    "syrup": "syrup",
    "susp": "suspension",
    "suspension": "suspension",
    "drop": "drops",
    "drops": "drops",
    "gel": "gel",
    "cream": "cream",
    "ointment": "ointment",
    "powder": "powder",
    "sachet": "sachet",
    "plain": "plain",
    "plus": "plus",
    "mint": "mint",
    "mixed": "mixed",
    "fruit": "fruit",
    "sr": "sr",
    "cr": "cr",
    "xr": "xr",
    "forte": "forte",
}

_FORM_LABELS = {
    "tab": "TAB",
    "cap": "CAP",
    "inj": "INJ",
    "syrup": "SYRUP",
    "suspension": "SUSPENSION",
    "drops": "DROPS",
    "gel": "GEL",
    "cream": "CREAM",
    "ointment": "OINTMENT",
    "powder": "POWDER",
    "sachet": "SACHET",
    "plain": "PLAIN",
    "plus": "PLUS",
    "mint": "MINT",
    "mixed": "MIXED",
    "fruit": "FRUIT",
    "sr": "SR",
    "cr": "CR",
    "xr": "XR",
    "forte": "FORTE",
}

_SPOKEN_FORM_ALIASES = {
    **_FORM_ALIASES,
    "syp": "syrup",
    "pow": "powder",
}

_ID_PATTERN = re.compile(r"\bmed-\d+\b", re.IGNORECASE)
_VOLUME_PACK_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:l|lt|litre|litres|liter|liters)\b",
    re.IGNORECASE,
)
_PACKAGING_TOKENS = {"box", "pack", "packs", "strip", "strips"}


@dataclass(frozen=True)
class MedicineAttributes:
    strengths: frozenset[str] = frozenset()
    strength_units: frozenset[str] = frozenset()
    formulations: frozenset[str] = frozenset()


def _canonical_unit(value: str) -> str | None:
    return _UNIT_ALIASES.get(normalize_text(value).replace(" ", " "))


def _canonical_formulation(value: str) -> str | None:
    return _FORM_ALIASES.get(normalize_text(value))


def pack_size_tokens(value: str) -> set[str]:
    return {
        str(int(match.group("value")))
        for match in _PACK_SIZE_PATTERN.finditer(value)
    }


def attributes_from_text(
    text: str,
    *,
    strengths: set[str] | frozenset[str] | None = None,
) -> MedicineAttributes:
    units = {
        canonical
        for match in _STRENGTH_UNIT_PATTERN.finditer(text)
        if (canonical := _canonical_unit(match.group("unit"))) is not None
    }
    formulations = {
        canonical
        for token in normalize_text(text).split()
        if (canonical := _canonical_formulation(token)) is not None
    }
    return MedicineAttributes(
        strengths=frozenset(str(strength) for strength in (strengths or ())),
        strength_units=frozenset(units),
        formulations=frozenset(formulations),
    )


def attributes_from_candidate(
    candidate: MedicineMatchCandidate,
) -> MedicineAttributes:
    value = f"{candidate.medicine_name} {candidate.description}"
    pack_sizes = {
        str(int(match.group("value")))
        for match in _PACK_SIZE_PATTERN.finditer(value)
    }
    strengths: set[str] = set()
    units: set[str] = set()
    for match in _STRENGTH_PATTERN.finditer(value):
        strength = str(int(match.group("value")))
        if strength in pack_sizes:
            continue
        strengths.add(strength)
        unit = match.group("unit")
        if unit is not None:
            canonical = _canonical_unit(unit)
            if canonical is not None:
                units.add(canonical)
    formulations = {
        canonical
        for token in normalize_text(value).split()
        if (canonical := _canonical_formulation(token)) is not None
    }
    return MedicineAttributes(
        strengths=frozenset(strengths),
        strength_units=frozenset(units),
        formulations=frozenset(formulations),
    )


def strictly_matches(
    candidate: MedicineMatchCandidate,
    spoken: MedicineAttributes,
) -> bool:
    catalogue = attributes_from_candidate(candidate)
    if spoken.strengths and not spoken.strengths <= catalogue.strengths:
        return False
    if spoken.strength_units and not (
        spoken.strength_units & catalogue.strength_units
    ):
        return False
    if spoken.formulations and not (
        spoken.formulations <= catalogue.formulations
    ):
        return False
    return True


def select_strict_candidate(
    candidates: list[MedicineMatchCandidate],
    spoken: MedicineAttributes,
) -> MedicineMatchCandidate | None:
    matches = [
        candidate for candidate in candidates if strictly_matches(candidate, spoken)
    ]
    return matches[0] if len(matches) == 1 else None


def spoken_strengths(text: str) -> set[str]:
    from app.services.voice.intent import extract_strength_tokens

    return extract_strength_tokens(text)


NOT_FOUND_TEXT = {
    "hi": "यह दवा स्वीकृत कैटलॉग में नहीं मिली। कृपया नाम दोबारा या थोड़ा साफ़ बोलें।",
    "en": "I could not find that medicine in the approved catalogue. Please repeat or clarify the name.",
}


def not_found_text(language: str) -> str:
    return NOT_FOUND_TEXT["hi" if language != "en" else "en"]


def _spoken_strength_phrases(value: str) -> list[tuple[str, str]]:
    pack_sizes = pack_size_tokens(value)
    phrases: dict[str, str] = {}
    for match in _STRENGTH_PATTERN.finditer(value):
        strength = str(int(match.group("value")))
        if strength in pack_sizes:
            continue
        unit = match.group("unit")
        phrase = f"{strength}{unit.upper()}" if unit else strength
        previous = phrases.get(strength)
        if previous is None or len(phrase) > len(previous):
            phrases[strength] = phrase
    return list(phrases.items())


def _spoken_forms(value: str) -> list[str]:
    forms: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s/,;:]+", normalize_text(value)):
        canonical = _SPOKEN_FORM_ALIASES.get(token)
        if canonical is None:
            continue
        label = _FORM_LABELS.get(canonical)
        if label is not None and label not in seen:
            seen.add(label)
            forms.append(label)
    return forms


def _spoken_base_name(value: str, strengths: set[str]) -> str:
    value = _ID_PATTERN.sub(" ", value or "")
    value = _PACK_SIZE_PATTERN.sub(" ", value)
    value = _VOLUME_PACK_PATTERN.sub(" ", value)
    tokens: list[str] = []
    for token in normalize_text(value).split():
        if token in _PACKAGING_TOKENS or not any(char.isalnum() for char in token):
            continue
        if token in _SPOKEN_FORM_ALIASES:
            continue
        if re.fullmatch(r"\d{1,4}(?:mg|mcg|ml|gm|g|iu|%)", token):
            continue
        if any(digit_value in strengths for digit_value in re.findall(r"\d+", token)):
            continue
        tokens.append(token.upper())
    return " ".join(tokens)


def _spoken_name_pair(
    medicine: MedicineMatchCandidate | str,
    description: str | None,
) -> tuple[str, str]:
    if isinstance(medicine, MedicineMatchCandidate):
        return medicine.medicine_name, medicine.description or ""
    if hasattr(medicine, "medicine_name") and hasattr(medicine, "description"):
        return str(medicine.medicine_name), str(medicine.description or "")
    return str(medicine), description or ""


def _build_spoken_name(
    medicine: MedicineMatchCandidate | str,
    description: str | None,
    *,
    include_forms: bool,
) -> str:
    name, detail = _spoken_name_pair(medicine, description)
    candidate = MedicineMatchCandidate(
        medicine_id="",
        medicine_name=name,
        description=detail,
        match_type="EXACT_NAME",
    )
    attributes = attributes_from_candidate(candidate)
    value = f"{name} {detail}"
    strengths = [
        phrase
        for strength, phrase in _spoken_strength_phrases(value)
        if strength in attributes.strengths
    ]
    forms = _spoken_forms(value)
    parts = [_spoken_base_name(value, set(attributes.strengths))]
    for form in forms:
        if include_forms or not strengths or form not in {"TAB", "CAP"}:
            if form not in parts:
                parts.append(form)
    parts.extend(strengths)
    return " ".join(part for part in parts if part)


def spoken_name(
    medicine: MedicineMatchCandidate | str,
    description: str | None = None,
    *,
    language: str = "hi",
    alternatives: Sequence[MedicineMatchCandidate | str] | None = None,
) -> str:
    name, detail = _spoken_name_pair(medicine, description)
    if alternatives is None:
        return _build_spoken_name(name, detail, include_forms=False)
    pairs = [_spoken_name_pair(alternative, None) for alternative in alternatives]
    target = (name, detail)
    if target not in pairs:
        pairs.append(target)
    labels = [
        _build_spoken_name(alt_name, alt_detail, include_forms=False)
        for alt_name, alt_detail in pairs
    ]
    include_forms = labels.count(labels[pairs.index(target)]) > 1
    return _build_spoken_name(name, detail, include_forms=include_forms)
