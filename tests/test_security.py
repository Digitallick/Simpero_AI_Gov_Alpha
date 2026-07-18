from app.core.security import _extract_org


def test_v2_token_shape():
    org_id, role = _extract_org({"o": {"id": "org_abc", "rol": "admin"}})
    assert (org_id, role) == ("org_abc", "admin")


def test_v1_token_shape():
    org_id, role = _extract_org({"org_id": "org_abc", "org_role": "org:admin"})
    assert (org_id, role) == ("org_abc", "admin")


def test_no_org():
    assert _extract_org({"sub": "user_1"}) == (None, None)
    assert _extract_org({"o": {}, "org_id": ""}) == (None, None)
