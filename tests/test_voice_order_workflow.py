from sqlalchemy import select

from app.database.models import Order, OrderLineItem


def _session(client, language="en"):
    response = client.post("/voice/sessions", json={"language": language})
    assert response.status_code == 201, response.text
    return response.json()


def _turn(client, session, text):
    response = client.post(
        f"/voice/sessions/{session['voice_session_id']}/turns",
        json={"text": text, "speak": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_quantity_correction_preserves_items_and_persists_once(client, seed_catalogue, db_session):
    session = _session(client)
    first = _turn(client, session, "add two strips of brufen 400")
    assert [i["medicine_id"] for i in first["order_summary"]["items"]] == ["MED-00014"]
    assert first["order_summary"]["items"][0]["quantity"] == 2

    second = _turn(client, session, "add one strip of brufen 200")
    items = second["order_summary"]["items"]
    assert [(i["medicine_id"], i["quantity"]) for i in items] == [
        ("MED-00014", 2),
        ("MED-00013", 1),
    ]

    corrected = _turn(client, session, "Brufen 400 ki quantity 3 strips kar do")
    assert corrected["intent"] == "update_item"
    items = corrected["order_summary"]["items"]
    assert len(items) == 2
    assert [(i["medicine_id"], i["quantity"], i["unit"]) for i in items] == [
        ("MED-00014", 3, "STRIP"),
        ("MED-00013", 1, "STRIP"),
    ]

    repeat = _turn(client, session, "Brufen 400 ki quantity 3 strips kar do")
    assert len(repeat["order_summary"]["items"]) == 2

    _turn(client, session, "show summary")
    confirmed = _turn(client, session, "confirm kar do")
    assert confirmed["order"] is not None
    assert confirmed["order"]["total_line_items"] == 2

    db_session.expire_all()
    order = db_session.scalar(select(Order))
    assert order is not None
    line_items = db_session.scalars(
        select(OrderLineItem).where(OrderLineItem.order_id == order.id)
    ).all()
    assert sorted(
        (item.medicine_id, item.quantity, item.unit) for item in line_items
    ) == [("MED-00013", 1, "STRIP"), ("MED-00014", 3, "STRIP")]


def test_confirmation_phrase_with_correction_word_is_not_an_update(client, seed_catalogue):
    session = _session(client)
    _turn(client, session, "add one strip of clomin")
    _turn(client, session, "show summary")

    result = _turn(client, session, "order kar do")
    assert result["intent"] == "confirm_order"
    assert result["order"] is not None


def test_hindi_confirmation_phrase_with_kar_do_is_not_an_update(client, seed_catalogue):
    session = _session(client, "hi")
    _turn(client, session, "एक स्ट्रिप क्लोमिन जोड़ो")
    _turn(client, session, "सारांश")

    result = _turn(client, session, "ऑर्डर कर दो")
    assert result["intent"] == "confirm_order"
    assert result["order"] is not None


def test_shared_voice_engines_are_process_wide(seed_catalogue):
    from app.core.config import get_settings
    from app.services.voice import (
        build_stt,
        build_tts,
        reset_shared_engines,
    )

    reset_shared_engines()
    try:
        settings = get_settings()
        assert build_stt(settings) is build_stt(settings)
        assert build_tts(settings) is build_tts(settings)
    finally:
        reset_shared_engines()
