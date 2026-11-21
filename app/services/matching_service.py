import unicodedata

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.medicine_aliases import DEFAULT_MEDICINE_ALIASES
from app.database.models import Medicine
from app.repositories.medicine_repository import MedicineRepository
from app.schemas.matching import MedicineMatchCandidate, MedicineMatchResponse


_DEVANAGARI_TO_LATIN = {
    "क्ष": "ksh",
    "त्र": "tr",
    "ज्ञ": "gya",
    "श्र": "shr",
    "अ": "a",
    "आ": "a",
    "इ": "i",
    "ई": "i",
    "उ": "u",
    "ऊ": "u",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ा": "a",
    "ि": "i",
    "ी": "i",
    "ु": "u",
    "ू": "u",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ं": "n",
    "ँ": "n",
    "्": "",
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "़": "",
}

_PARACETAMOL_TARGETS = ("dolo 650", "crocin", "paracet")
_GENERIC_INGREDIENT_GROUPS = {
    "paracetamol": _PARACETAMOL_TARGETS,
    "acetaminophen": _PARACETAMOL_TARGETS,
    "पैरासिटामोल": _PARACETAMOL_TARGETS,
    "पैरासेटामोल": _PARACETAMOL_TARGETS,
}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = []
    for character in normalized:
        category = unicodedata.category(character)
        if character.isalnum() or category.startswith("M"):
            characters.append(character)
        else:
            characters.append(" ")
    return " ".join("".join(characters).split())


def transliterate_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    output: list[str] = []
    index = 0
    while index < len(normalized):
        character = normalized[index]
        matched = False
        for width in (2, 1):
            part = normalized[index:index + width]
            if part in _DEVANAGARI_TO_LATIN:
                output.append(_DEVANAGARI_TO_LATIN[part])
                index += width
                matched = True
                break
        if matched:
            continue
        if character.isdigit():
            digit = unicodedata.digit(character, None)
            output.append(str(digit) if digit is not None else character)
        elif not unicodedata.category(character).startswith("M"):
            output.append(character)
        index += 1
    return " ".join("".join(output).split())


def _score_text(value: str, targets: set[str]) -> float:
    normalized = normalize_text(value)
    transliterated = normalize_text(transliterate_text(value))
    scores = [
        fuzz.ratio(probe, target) / 100.0
        for probe in (normalized, transliterated)
        for target in targets
        if probe and target
    ]
    return max(scores, default=0.0)


def _candidate(
    medicine: Medicine, match_type: str
) -> MedicineMatchCandidate:
    return MedicineMatchCandidate(
        medicine_id=medicine.medicine_id,
        medicine_name=medicine.medicine_name,
        description=medicine.description,
        match_type=match_type,
    )


class MedicineMatchingService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.repository = MedicineRepository(db)

    def _aliases(self) -> dict[str, str]:
        aliases = dict(DEFAULT_MEDICINE_ALIASES)
        aliases.update(self.settings.medicine_aliases)
        normalized: dict[str, str] = {}
        for alias, target in aliases.items():
            alias_key = normalize_text(str(alias))
            target_key = normalize_text(str(target))
            if alias_key and target_key:
                normalized[alias_key] = target_key
        return normalized

    def _generic_groups(self) -> dict[str, tuple[str, ...]]:
        groups: dict[str, tuple[str, ...]] = {}
        for name, targets in _GENERIC_INGREDIENT_GROUPS.items():
            normalized_targets = tuple(normalize_text(target) for target in targets)
            for key in (name, transliterate_text(name)):
                normalized_key = normalize_text(key)
                if normalized_key:
                    groups[normalized_key] = normalized_targets
        return groups

    def _candidates_for_names(
        self, medicines: list[Medicine], names: set[str] | tuple[str, ...], match_type: str
    ) -> list[MedicineMatchCandidate]:
        return [
            _candidate(medicine, match_type)
            for medicine in sorted(medicines, key=lambda item: item.medicine_id)
            if normalize_text(medicine.medicine_name) in names
        ]

    def suggestions_from_utterance(self, text: str) -> list[MedicineMatchCandidate]:
        """Known names inside noisy speech are suggestions, never selections.

        Unknown residual strength/other words must not be discarded to auto-add.
        The caller must explicitly identify the catalogue row on the next turn.
        """
        tokens = normalize_text(text).split()
        medicines = self.repository.list_all_active()
        names = {normalize_text(m.medicine_name) for m in medicines}
        lexicon = {name: name for name in names}
        lexicon.update(self._aliases())
        spans: dict[str, list[tuple[int, int]]] = {}
        for phrase, name in lexicon.items():
            parts = phrase.split()
            for i in range(len(tokens) - len(parts) + 1):
                if tokens[i:i + len(parts)] == parts:
                    spans.setdefault(name, []).append((i, i + len(parts)))
        # "नेबीस्टार 5" names NEBISTAR-5; the NEBISTAR inside it is not a
        # second medicine. A name heard only inside a longer name is dropped.
        mentioned = {
            name for name, own in spans.items()
            if not all(
                any(a <= i and j <= b and (b - a) > (j - i) for other, theirs in spans.items()
                    if other != name for a, b in theirs)
                for i, j in own
            )
        }
        return [_candidate(m, "FUZZY_SUGGESTION") for m in sorted(medicines, key=lambda m: m.medicine_id)
                if normalize_text(m.medicine_name) in mentioned][:self.settings.fuzzy_match_limit]

    def score_candidates(
        self, text: str, limit: int | None = None
    ) -> list[tuple[MedicineMatchCandidate, float]]:
        aliases_by_target: dict[str, set[str]] = {}
        for alias, target in self._aliases().items():
            aliases_by_target.setdefault(target, set()).add(alias)
        scored: list[tuple[MedicineMatchCandidate, float]] = []
        for medicine in self.repository.list_all_active():
            name_key = normalize_text(medicine.medicine_name)
            targets = {
                name_key,
                normalize_text(transliterate_text(medicine.medicine_name)),
            }
            for alias in aliases_by_target.get(name_key, set()):
                targets.add(alias)
                targets.add(normalize_text(transliterate_text(alias)))
            targets.update(self._generic_groups().get(name_key, ()))
            scored.append((
                _candidate(medicine, "FUZZY_SUGGESTION"),
                _score_text(text, targets),
            ))
        scored.sort(
            key=lambda entry: (
                -entry[1],
                entry[0].medicine_name,
                entry[0].medicine_id,
            )
        )
        if limit is None:
            return scored
        return scored[:limit]

    def search(self, text: str) -> MedicineMatchResponse:
        normalized_text = normalize_text(text)
        medicines = self.repository.list_all_active()
        by_name: dict[str, list[Medicine]] = {}
        for medicine in medicines:
            by_name.setdefault(normalize_text(medicine.medicine_name), []).append(
                medicine
            )

        if not normalized_text:
            return MedicineMatchResponse(
                status="NOT_FOUND",
                requested_text=text,
                candidates=[],
                message="No medicine text was provided.",
            )

        aliases = self._aliases()
        matched_name = normalized_text
        match_type = "EXACT_NAME"
        if normalized_text not in by_name and normalized_text in aliases:
            matched_name = aliases[normalized_text]
            match_type = "ALIAS"

        generic_targets = self._generic_groups().get(normalized_text)
        if generic_targets:
            candidates = self._candidates_for_names(
                medicines, set(generic_targets), "ALIAS"
            )
            if candidates:
                return MedicineMatchResponse(
                    status="CLARIFICATION_REQUIRED",
                    requested_text=text,
                    candidates=candidates,
                    message=(
                        "The generic name matches multiple catalogue brands. "
                        "The customer must select the specific medicine_id."
                    ),
                )

        matched = sorted(
            by_name.get(matched_name, []),
            key=lambda medicine: medicine.medicine_id,
        )
        if matched:
            candidates = [
                _candidate(medicine, match_type) for medicine in matched
            ]
            if len(candidates) == 1:
                return MedicineMatchResponse(
                    status="EXACT_MATCH",
                    requested_text=text,
                    candidates=candidates,
                    message=(
                        "One approved catalogue medicine matched. Add it using "
                        "its medicine_id."
                    ),
                )
            return MedicineMatchResponse(
                status="CLARIFICATION_REQUIRED",
                requested_text=text,
                candidates=candidates,
                message=(
                    "Multiple catalogue variants match. The customer must "
                    "select a specific medicine_id before the item can be added."
                ),
            )

        scored = [
            entry for entry in self.score_candidates(text)
            if entry[1] >= self.settings.fuzzy_match_threshold
        ]
        suggestions = [
            candidate
            for candidate, _ in scored[:self.settings.fuzzy_match_limit]
        ]

        message = (
            "No approved catalogue medicine matched exactly. Suggestions, if "
            "shown, are unverified and require the customer to select the "
            "correct catalogue medicine_id."
        )
        return MedicineMatchResponse(
            status="NOT_FOUND",
            requested_text=text,
            candidates=suggestions,
            message=message,
        )
