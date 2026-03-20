from types import SimpleNamespace

from app.services.auth_service import auth_service


def test_token_roundtrip():
    user = SimpleNamespace(
        id="user-1",
        email="an.nguyen@company.com",
        role="employee",
        department_id="dept-1",
        clearance_level="internal",
    )

    token = auth_service.create_token(user)
    payload = auth_service.decode_access_token(token)

    assert payload["sub"] == "user-1"
    assert payload["role"] == "employee"
