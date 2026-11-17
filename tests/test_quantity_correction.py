
import pytest

from app.services.voice.quantity_correction import (
    detect_correction,
)
from app.services.voice.confirmation_guard import (
    ConfirmationGuard,
)


# --------------------------------------------------
# TEST DATA
# --------------------------------------------------

@pytest.fixture
def draft_items():
    return [
        {
            "id": 1,
            "medicine_id": "MED-00014",
            "medicine_name": "BRUFEN",
            "description": "400MG 15'S",
            "quantity": 2,
            "unit": "STRIP",
        },
        {
            "id": 2,
            "medicine_id": "MED-00013",
            "medicine_name": "BRUFEN",
            "description": "200MG 15'S",
            "quantity": 1,
            "unit": "STRIP",
        },
    ]


# --------------------------------------------------
# 1. HINGLISH QUANTITY UPDATE
# --------------------------------------------------

def test_hinglish_quantity_update(draft_items):
    result = detect_correction(
        "Brufen 400 ki quantity 3 strips kar do",
        draft_items,
    )

    assert result.intent == "UPDATE_ITEM"
    assert result.item_id == 1
    assert result.medicine_id == "MED-00014"
    assert result.quantity == 3
    assert result.unit == "STRIP"


# --------------------------------------------------
# 2. ENGLISH QUANTITY UPDATE
# --------------------------------------------------

def test_english_quantity_update(draft_items):
    result = detect_correction(
        "Change Brufen 200 to three strips",
        draft_items,
    )

    assert result.intent == "UPDATE_ITEM"
    assert result.item_id == 2
    assert result.medicine_id == "MED-00013"
    assert result.quantity == 3
    assert result.unit == "STRIP"


# --------------------------------------------------
# 3. HINDI QUANTITY UPDATE
# --------------------------------------------------

def test_hindi_quantity_update(draft_items):
    result = detect_correction(
        "ब्रूफेन 400 की मात्रा 3 स्ट्रिप कर दो",
        draft_items,
    )

    assert result.intent in (
        "UPDATE_ITEM",
        "CLARIFY_MEDICINE",
    )

    # If medicine aliases are configured,
    # it should resolve the verified variant.
    if result.intent == "UPDATE_ITEM":
        assert result.medicine_id == "MED-00014"
        assert result.quantity == 3


# --------------------------------------------------
# 4. AMBIGUOUS MEDICINE VARIANT
# --------------------------------------------------

def test_ambiguous_variant(draft_items):
    result = detect_correction(
        "Brufen ki quantity 3 strips kar do",
        draft_items,
    )

    assert result.intent == "CLARIFY_MEDICINE"
    assert result.medicine_id is None


# --------------------------------------------------
# 5. INCOMPLETE CORRECTION
# --------------------------------------------------

def test_incomplete_correction(draft_items):
    result = detect_correction(
        "Brufen 400 ki quantity change karo",
        draft_items,
    )

    assert result.intent == "CLARIFY_QUANTITY"
    assert result.quantity is None


# --------------------------------------------------
# 6. UNKNOWN MEDICINE
# --------------------------------------------------

def test_unknown_medicine(draft_items):
    result = detect_correction(
        "UnknownMed 500 ki quantity 3 strips kar do",
        draft_items,
    )

    assert result.intent == "CLARIFY_MEDICINE"
    assert result.medicine_id is None


# --------------------------------------------------
# 7. NUMBER WORD HANDLING
# --------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Brufen 400 ki quantity teen strips kar do",
            3,
        ),
        (
            "Change Brufen 400 to two strips",
            2,
        ),
        (
            "Brufen 400 ki quantity 5 strips kar do",
            5,
        ),
    ],
)
def test_number_words(draft_items, text, expected):
    result = detect_correction(
        text,
        draft_items,
    )

    assert result.intent == "UPDATE_ITEM"
    assert result.medicine_id == "MED-00014"
    assert result.quantity == expected


# --------------------------------------------------
# 8. NO UPDATE INTENT
# --------------------------------------------------

def test_no_update_intent(draft_items):
    result = detect_correction(
        "Mujhe Brufen 400 ki 2 strips chahiye",
        draft_items,
    )

    assert result.intent == "NOT_UPDATE"


# --------------------------------------------------
# 9. FAILED CORRECTION BLOCKS CONFIRMATION
# --------------------------------------------------

def test_failed_correction_blocks_confirmation():
    guard = ConfirmationGuard()

    guard.correction_started()

    assert guard.pending_correction is True
    assert not guard.can_confirm("version-1")


# --------------------------------------------------
# 10. SUCCESSFUL CORRECTION REQUIRES NEW SUMMARY
# --------------------------------------------------

def test_successful_correction_requires_new_summary():
    guard = ConfirmationGuard()

    guard.correction_started()
    guard.correction_completed()

    assert not guard.can_confirm("version-2")

    guard.summary_presented("version-2")

    assert guard.can_confirm("version-2")


# --------------------------------------------------
# 11. STALE SUMMARY CANNOT BE CONFIRMED
# --------------------------------------------------

def test_stale_summary_blocked():
    guard = ConfirmationGuard()

    guard.summary_presented("version-1")

    assert guard.can_confirm("version-1")

    # Draft changed.
    guard.correction_started()
    guard.correction_completed()

    assert not guard.can_confirm("version-1")

    guard.summary_presented("version-2")

    assert guard.can_confirm("version-2")
    assert not guard.can_confirm("version-1")


# --------------------------------------------------
# 12. NO CONFIRMATION DURING PENDING CORRECTION
# --------------------------------------------------

def test_pending_correction_prevents_confirmation():
    guard = ConfirmationGuard()

    guard.summary_presented("version-1")

    assert guard.can_confirm("version-1")

    guard.correction_started()

    assert not guard.can_confirm("version-1")

    # A summary cannot be accepted while
    # the correction is unresolved.
    assert guard.summary_presented("version-2") is False

    assert not guard.can_confirm("version-2")
