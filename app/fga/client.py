import httpx
from app.core.config import settings


class FGAClient:
    def __init__(self):
        self._http = httpx.Client(timeout=5.0)

    @property
    def _base(self) -> str:
        return f"{settings.openfga_url}/stores/{settings.openfga_store_id}"

    def check(self, user: str, relation: str, object: str, context: dict | None = None) -> bool:
        try:
            body: dict = {"tuple_key": {"user": user, "relation": relation, "object": object}}
            if context:
                body["context"] = context
            resp = self._http.post(f"{self._base}/check", json=body)
            resp.raise_for_status()
            return resp.json().get("allowed", False)
        except Exception:
            return False

    def write(self, tuples: list[dict]) -> None:
        if not tuples:
            return
        for t in tuples:
            try:
                self._http.post(
                    f"{self._base}/write",
                    json={"writes": {"tuple_keys": [t]}},
                ).raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    continue
                raise

    def delete(self, tuples: list[dict]) -> None:
        if not tuples:
            return
        for t in tuples:
            # Strip condition field — delete only needs the base tuple key
            base = {k: v for k, v in t.items() if k != "condition"}
            try:
                self._http.post(
                    f"{self._base}/write",
                    json={"deletes": {"tuple_keys": [base]}},
                ).raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    continue
                raise

    def create_store(self, name: str) -> str:
        resp = self._http.post(
            f"{settings.openfga_url}/stores",
            json={"name": name},
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def write_model(self, model: dict) -> str:
        resp = self._http.post(
            f"{self._base}/authorization-models",
            json=model,
        )
        resp.raise_for_status()
        return resp.json()["authorization_model_id"]

    def read(self, object: str) -> list[dict]:
        resp = self._http.post(
            f"{self._base}/read",
            json={"tuple_key": {"object": object}},
        )
        resp.raise_for_status()
        return [t["key"] for t in resp.json().get("tuples", [])]

    def list_objects(
        self, user: str, relation: str, object_type: str, context: dict | None = None
    ) -> list[str]:
        try:
            body: dict = {"user": user, "relation": relation, "type": object_type}
            if context:
                body["context"] = context
            resp = self._http.post(f"{self._base}/list-objects", json=body)
            resp.raise_for_status()
            return resp.json().get("objects", [])
        except Exception:
            return []


fga_client = FGAClient()
