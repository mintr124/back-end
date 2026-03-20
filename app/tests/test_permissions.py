from types import SimpleNamespace

from app.services.permission_service import PermissionService


def test_department_manager_can_update_same_department():
    svc = PermissionService()
    user = SimpleNamespace(
        role="department_manager",
        department_id="dept-1",
        clearance_level="confidential",
    )
    doc = SimpleNamespace(
        department_id="dept-1",
        sensitivity_level="internal",
        allowed_roles=["department_manager", "admin_auditor"],
        status="ready",
    )

    ok, reason = svc.can_update_document(user, doc)
    assert ok is True
    assert reason == "ok"


def test_employee_cannot_update():
    svc = PermissionService()
    user = SimpleNamespace(
        role="employee",
        department_id="dept-1",
        clearance_level="internal",
    )
    doc = SimpleNamespace(
        department_id="dept-1",
        sensitivity_level="internal",
        allowed_roles=["department_manager", "admin_auditor"],
        status="ready",
    )

    ok, _ = svc.can_update_document(user, doc)
    assert ok is False
