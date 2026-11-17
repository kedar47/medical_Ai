from enum import Enum


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class OrderStatus(str, Enum):
    PENDING_PHARMACY_REVIEW = "PENDING_PHARMACY_REVIEW"


class OrderUnit(str, Enum):
    TABLET = "TABLET"
    STRIP = "STRIP"
    BOTTLE = "BOTTLE"
    BOX = "BOX"
    UNIT = "UNIT"


class VoiceSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    ENDED = "ENDED"
    EXPIRED = "EXPIRED"


class VoiceIntent(str, Enum):
    ADD_ITEM = "add_item"
    SELECT_CANDIDATE = "select_candidate"
    UPDATE_ITEM = "update_item"
    REMOVE_ITEM = "remove_item"
    SHOW_SUMMARY = "show_summary"
    CONFIRM_ORDER = "confirm_order"
    CANCEL_ORDER = "cancel_order"
    END_CALL = "end_call"
    HELP = "help"
    UNKNOWN = "unknown"
