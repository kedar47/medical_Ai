from fastapi.testclient import TestClient


def search(client: TestClient, text: str) -> dict:
    response = client.post("/medicine-matching/search", json={"text": text})
    assert response.status_code == 200
    return response.json()


def test_exact_unique_name_returns_one_candidate(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "  clomin tab  ")

    assert payload["status"] == "EXACT_MATCH"
    assert payload["requested_text"] == "clomin tab"
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["medicine_id"] == "MED-00003"
    assert payload["candidates"][0]["match_type"] == "EXACT_NAME"


def test_exact_brufen_requires_clarification_and_never_selects_variant(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "BRUFEN")

    assert payload["status"] == "CLARIFICATION_REQUIRED"
    assert len(payload["candidates"]) == 3
    assert {item["medicine_id"] for item in payload["candidates"]} == {
        "MED-00013",
        "MED-00014",
        "MED-00015",
    }
    assert all(
        item["medicine_name"] == "BRUFEN"
        for item in payload["candidates"]
    )


def test_latin_alias_returns_all_variants_for_clarification(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "brufin")

    assert payload["status"] == "CLARIFICATION_REQUIRED"
    assert len(payload["candidates"]) == 3
    assert {item["match_type"] for item in payload["candidates"]} == {"ALIAS"}


def test_devanagari_alias_matches_without_losing_marks(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "ब्रूफेन")

    assert payload["status"] == "CLARIFICATION_REQUIRED"
    assert len(payload["candidates"]) == 3
    assert {item["medicine_id"] for item in payload["candidates"]} == {
        "MED-00013",
        "MED-00014",
        "MED-00015",
    }


def test_alias_target_with_multiple_packaging_variants_requires_clarification(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "कोरफ्लैम")

    assert payload["status"] == "CLARIFICATION_REQUIRED"
    assert len(payload["candidates"]) == 2
    assert {item["description"] for item in payload["candidates"]} == {
        "10'S",
        "20'S",
    }


def test_fuzzy_match_is_suggestion_only(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "BRUFN")

    assert payload["status"] == "NOT_FOUND"
    assert payload["candidates"]
    assert {item["match_type"] for item in payload["candidates"]} == {
        "FUZZY_SUGGESTION"
    }
    assert {item["medicine_name"] for item in payload["candidates"]} == {
        "BRUFEN"
    }


def test_description_text_never_becomes_an_exact_match(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "400MG 15'S")

    assert payload["status"] != "EXACT_MATCH"
    assert all(
        item["match_type"] != "EXACT_NAME"
        for item in payload["candidates"]
    )


def test_name_with_strength_does_not_auto_select_packaging_variant(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "BRUFEN 400MG")

    assert payload["status"] == "NOT_FOUND"
    assert all(
        item["medicine_id"] != "MED-00014"
        for item in payload["candidates"]
    )


def test_inactive_medicine_is_not_matched(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "OLD MEDICINE")

    assert payload["status"] == "NOT_FOUND"
    assert payload["candidates"] == []


def test_unrelated_text_returns_not_found_without_confirmed_candidates(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = search(client, "INSULIN GLARGINE")

    assert payload["status"] == "NOT_FOUND"
    assert all(
        item["match_type"] == "FUZZY_SUGGESTION"
        for item in payload["candidates"]
    )


def test_blank_matching_text_returns_422(client: TestClient) -> None:
    assert (
        client.post("/medicine-matching/search", json={"text": "   "}).status_code
        == 422
    )
    assert (
        client.post("/medicine-matching/search", json={"text": ""}).status_code
        == 422
    )
    assert (
        client.post("/medicine-matching/search", json={}).status_code == 422
    )
