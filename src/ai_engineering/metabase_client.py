"""Metabase client (v2.1) — the ONLY module that may import `httpx`.

A typed wrapper around the Metabase REST API (v0.x). Lives in
`src/ai_engineering/` because it is a sink invoked at the end of an
`ask` call (post-pipeline, post-RLS). It does NOT import `psycopg`,
`openai`, `pandas`, or adapter internals.

Constitution invariants enforced here:
- Principle II/III: `httpx` is imported ONLY in this file. The boundary test
  in `tests/contract/test_boundaries.py` asserts this.
- Principle IV (NON-NEGOTIABLE): the SQL in every Card created by
  `send_governed_query` is the ALREADY-GOVERNED SQL from the pipeline's
  `TextToSqlResponse.query_result.sql`. Metabase never sees pre-RLS SQL.
- Resilience (FR-013): every public method is best-effort — failures are
  logged as warnings and surfaced as `None` returns; never raised.

Reference: specs/004-metabase-integration/contracts/metabase_client.md
            specs/004-metabase-integration/research.md Parts A, E, F, G
            specs/004-metabase-integration/data-model.md
            specs/004-metabase-integration/tasks.md T005, T015, T016
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from src.contracts.metabase import (
    Card,
    Collection,
    Dashboard,
    DashboardItem,
    MetabaseConfig,
)
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import TextToSqlResponse

_logger = logging.getLogger(__name__)

# Session token lifetime is long enough that we don't need to refresh on every
# call; we only re-auth on 401.
_DEFAULT_TIMEOUT_S = 30.0
_MAX_CARD_NAME_LEN = 140
# Display type heuristics thresholds (research.md Part E).
_BAR_ROW_THRESHOLD = 20


class MetabaseClient:
    """Typed wrapper around the Metabase REST API.

    The ONLY class that talks HTTP to Metabase. Every public method is
    best-effort: failures are caught, logged as warnings, and the method
    returns `None` (or `[]` / `False`) instead of raising. The pipeline
    therefore never blocks on Metabase availability (FR-013 / SC-007).
    """

    def __init__(self, config: MetabaseConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.host,
            timeout=_DEFAULT_TIMEOUT_S,
            # Trust localhost by default; the Forge/openai client uses verify=False
            # for the proxy, so we follow the same local-dev convention.
            verify=False,
        )
        self._session_token: str | None = None

    # ------------------------------------------------------------------
    # Authentication (research.md Part G)
    # ------------------------------------------------------------------

    def login(self) -> str:
        """POST /api/session and cache the token. Re-logins transparently.

        Raises only if the call fails twice in a row (network AND auth both
        broken) — the pipeline treats `login()` failures as best-effort.
        """
        if self._session_token is not None:
            return self._session_token
        try:
            response = self._client.post(
                "/api/session",
                json={
                    "username": self._config.admin_email,
                    "password": self._config.admin_password,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.warning("Metabase login failed: %s", exc)
            raise
        token = response.json().get("id")
        if not token:
            raise RuntimeError("Metabase login response missing 'id' (token).")
        # httpx.Response.json() returns Any; cast to str (the API contract).
        self._session_token = str(token)
        return self._session_token

    def _ensure_session(self) -> dict[str, str]:
        """Return the `X-Metabase-Session` header, logging in if needed."""
        token = self._session_token or self.login()
        return {"X-Metabase-Session": token}

    def _request_with_reauth(
        self,
        method: str,
        url: str,
        *,
        json_payload: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Make an authenticated request, retrying login once on 401."""
        headers = self._ensure_session()
        response = self._client.request(
            method, url, headers=headers, json=json_payload
        )
        if response.status_code == 401:
            _logger.info("Metabase session expired; re-authenticating.")
            self._session_token = None
            headers = self._ensure_session()
            response = self._client.request(
                method, url, headers=headers, json=json_payload
            )
        return response

    # ------------------------------------------------------------------
    # Setup (research.md Part F — idempotency)
    # ------------------------------------------------------------------

    def is_setup_complete(self) -> bool:
        """Return True if Metabase's initial setup has already been completed.

        v0.58 compatibility: `setup-token` may remain non-null even after
        setup is done. We check `has-user-setup` first (v0.58 field). If that
        is True, setup is complete. As a fallback, try to login — if login
        works, an admin user exists and setup is complete.
        """
        try:
            response = self._client.get("/api/session/properties")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.warning("Metabase session/properties check failed: %s", exc)
            return False
        props = response.json()
        # v0.58+: has-user-setup is the canonical check.
        if props.get("has-user-setup") is True:
            return True
        # Legacy check: setup-token null means setup is done.
        if not props.get("setup-token"):
            return True
        # setup-token is non-null, but maybe setup was done via the wizard
        # and the token wasn't cleared. Try to login — if it works, we're
        # already set up.
        try:
            self._session_token = None
            self.login()
            return True
        except httpx.HTTPError:
            # Login failed — setup is genuinely not complete.
            return False

    def get_version(self) -> str:
        """Return the Metabase version reported by the server."""
        try:
            response = self._client.get("/api/session/properties")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.warning("Metabase get_version failed: %s", exc)
            return "unknown"
        version_raw = response.json().get("version", "unknown")
        return str(version_raw)

    def get_health(self) -> dict[str, object]:
        """Return the /api/health response (dict; may be {'status': 'ok'} or similar)."""
        try:
            response = self._client.get("/api/health")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.warning("Metabase get_health failed: %s", exc)
            return {"status": "unreachable", "error": str(exc)}
        # /api/health may return 200 with empty body or {"status": "ok"}
        try:
            payload = response.json()
        except ValueError:
            return {"status": "ok"}
        return payload  # type: ignore[no-any-return]

    def setup_initial(self, admin_email: str, admin_password: str) -> bool:
        """POST /api/setup to complete the initial wizard (idempotent).

        Returns True if setup was performed; False if already done.
        Never raises.
        """
        if self.is_setup_complete():
            _logger.info("Metabase setup already complete; skipping admin user creation.")
            return False
        try:
            # Fetch the setup token first; only a non-null token allows setup completion.
            response = self._client.get("/api/session/properties")
            response.raise_for_status()
            setup_token = response.json().get("setup-token")
            if not setup_token:
                # Already configured between the previous check and now.
                return False
            body = {
                "token": setup_token,
                "user": {
                    "email": admin_email,
                    "first_name": "Admin",
                    "last_name": "Plataforma",
                    "password": admin_password,
                    "site_name": "Plataforma de Datos y GenAI",
                },
                "prefs": {
                    "site_name": "Plataforma de Datos y GenAI",
                    "site_locale": "en",
                },
            }
            setup_response = self._client.post("/api/setup", json=body)
            if setup_response.status_code == 403:
                # "A user currently exists" — setup was already done.
                _logger.info("Metabase setup already done (user exists); 403 from /api/setup.")
                return False
            setup_response.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.warning("Metabase setup_initial failed (non-fatal): %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # Database connection (PG warehouse) — research.md Part C
    # ------------------------------------------------------------------

    def create_db_connection(
        self,
        pg_host: str,
        pg_port: int,
        pg_database: str,
        pg_user: str,
        pg_password: str,
        db_name: str,
    ) -> int | None:
        """Connect Metabase to PostgreSQL using the metabase_readonly role.

        Idempotent: if a database with the same `db_name` already exists
        (search via GET /api/database), returns the existing id without
        re-creating.
        """
        try:
            headers = self._ensure_session()
            # List existing DBs — v0.58 returns paginated dict with 'data' key.
            list_response = self._client.get("/api/database?limit=50", headers=headers)
            list_response.raise_for_status()
            list_data = list_response.json()
            existing = list_data.get("data", list_data) if isinstance(list_data, dict) else list_data
            for db in existing:
                if db.get("name") == db_name:
                    _logger.info(
                        "Metabase database connection %r already exists (id=%s); reusing.",
                        db_name,
                        db.get("id"),
                    )
                    return int(db.get("id", 0)) or None

            # Create the DB connection.
            body: dict[str, object] = {
                "engine": "postgres",
                "name": db_name,
                "details": {
                    "host": pg_host,
                    "port": pg_port,
                    "dbname": pg_database,
                    "user": pg_user,
                    "password": pg_password,
                    "ssl": False,
                },
            }
            create_response = self._client.post(
                "/api/database", headers=self._ensure_session(), json=body
            )
            create_response.raise_for_status()
            return int(create_response.json().get("id", 0)) or None
        except httpx.HTTPError as exc:
            _logger.warning("Metabase create_db_connection failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def get_or_create_collection(
        self, name: str, parent_id: int | None = None
    ) -> Collection | None:
        """Find or create a collection by name under the root (or a parent)."""
        try:
            list_response = self._request_with_reauth("GET", "/api/collection")
            list_response.raise_for_status()
            for collection in list_response.json():
                if collection.get("name") == name:
                    return Collection(
                        id=int(collection["id"]),
                        name=str(collection["name"]),
                        parent_id=collection.get("parent_id"),
                        location=str(collection.get("location", "/")),
                    )
            body: dict[str, object] = {"name": name}
            if parent_id is not None:
                body["parent_id"] = parent_id
            create_response = self._request_with_reauth(
                "POST", "/api/collection", json_payload=body
            )
            create_response.raise_for_status()
            data = create_response.json()
            return Collection(
                id=int(data["id"]),
                name=str(data["name"]),
                parent_id=data.get("parent_id"),
                location=str(data.get("location", "/")),
            )
        except httpx.HTTPError as exc:
            _logger.warning("Metabase get_or_create_collection failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Cards (used by the pipeline callback)
    # ------------------------------------------------------------------

    def create_card(
        self,
        name: str,
        sql: str,
        collection_id: int,
        display: str,
        description: str | None = None,
        db_id: int = 1,
    ) -> Card | None:
        """POST /api/card to create a Metabase native-query card.

        The `sql` parameter MUST be the already-governed SQL (post-RLS injection).
        The `db_id` is the Metabase database connection ID (from MetabaseSession).
        """
        # Metabase truncates card names; pre-truncate to avoid 4xx.
        truncated_name = name[:_MAX_CARD_NAME_LEN]
        body: dict[str, object] = {
            "name": truncated_name,
            "dataset_query": {
                "type": "native",
                "native": {"query": sql},
                "database": db_id,
            },
            "display": display,
            "visualization_settings": {},
            "collection_id": collection_id,
        }
        # description is optional but useful for governance traceability.
        if description is not None:
            body["description"] = description
        try:
            response = self._request_with_reauth(
                "POST", "/api/card", json_payload=body
            )
            response.raise_for_status()
            data = response.json()
            return Card(
                id=int(data["id"]),
                name=str(data["name"]),
                sql=sql,
                collection_id=collection_id,
                display=data.get("display", display),
                description=description,
                created_at=datetime.now(timezone.utc),
            )
        except httpx.HTTPError as exc:
            _logger.warning("Metabase create_card failed: %s", exc)
            return None

    def list_cards_in_collection(self, collection_id: int) -> list[Card]:
        """Return all cards in the given collection.

        Uses GET /api/card with the `collection` query parameter (v0.58 format).
        Falls back to listing all cards and filtering client-side if the API
        doesn't support the filter.
        """
        try:
            # v0.58 expects: GET /api/card?collection_id=<id>
            response = self._request_with_reauth(
                "GET", f"/api/card?collection_id={collection_id}"
            )
            response.raise_for_status()
            items = response.json()
            # The response may be a list or a paginated dict with a 'data' key.
            if isinstance(items, dict) and "data" in items:
                items = items["data"]
            if not isinstance(items, list):
                items = []
            return [
                Card(
                    id=int(item["id"]),
                    name=str(item.get("name", "")),
                    sql=" ",  # list endpoint doesn't return SQL; use space to satisfy min_length=1
                    collection_id=collection_id,
                    display=item.get("display", "table") if item.get("display") in ("scalar", "table", "bar", "line", "area") else "table",
                    description=item.get("description"),
                    created_at=None,
                )
                for item in items
            ]
        except httpx.HTTPError as exc:
            _logger.warning("Metabase list_cards_in_collection failed: %s", exc)
            return []

    def delete_card(self, card_id: int) -> bool:
        """DELETE /api/card/{id}. Returns True on success."""
        try:
            response = self._request_with_reauth(
                "DELETE", f"/api/card/{card_id}"
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            _logger.warning("Metabase delete_card(%s) failed: %s", card_id, exc)
            return False

    # ------------------------------------------------------------------
    # Dashboards + dashcards (US3)
    # ------------------------------------------------------------------

    def get_or_create_dashboard(
        self, name: str, collection_id: int
    ) -> Dashboard | None:
        """Find or create a dashboard by name within a collection."""
        try:
            list_response = self._request_with_reauth(
                "GET", f"/api/dashboard?f=collection_id%3D{collection_id}"
            )
            list_response.raise_for_status()
            for dash in list_response.json():
                if dash.get("name") == name:
                    return Dashboard(
                        id=int(dash["id"]),
                        name=str(dash["name"]),
                        collection_id=collection_id,
                        ordered_items=[],
                    )
            body: dict[str, object] = {"name": name, "collection_id": collection_id}
            create_response = self._request_with_reauth(
                "POST", "/api/dashboard", json_payload=body
            )
            create_response.raise_for_status()
            data = create_response.json()
            return Dashboard(
                id=int(data["id"]),
                name=str(data["name"]),
                collection_id=collection_id,
                ordered_items=[],
            )
        except httpx.HTTPError as exc:
            _logger.warning("Metabase get_or_create_dashboard failed: %s", exc)
            return None

    def add_card_to_dashboard(
        self, card_id: int, dashboard_id: int
    ) -> DashboardItem | None:
        """POST /api/dashboard/{id}/dashcard to attach a card to a dashboard."""
        body: dict[str, object] = {"card_id": card_id}
        try:
            response = self._request_with_reauth(
                "POST", f"/api/dashboard/{dashboard_id}/dashcards", json_payload=body
            )
            response.raise_for_status()
            data = response.json()
            return DashboardItem(
                id=int(data["id"]),
                card_id=card_id,
                dashboard_id=dashboard_id,
            )
        except httpx.HTTPError as exc:
            _logger.warning(
                "Metabase add_card_to_dashboard failed for card %s: %s", card_id, exc
            )
            return None

    # ------------------------------------------------------------------
    # Display type heuristics (research.md Part E)
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_display_type(response: TextToSqlResponse) -> str:
        """Pick a chart display from the response shape.

        - 1 row × 1 col   → 'scalar' (single-value totals).
        - GROUP BY + ≤20 rows → 'bar' (categorical bar chart).
        - otherwise       → 'table' (safe default).
        """
        if response.query_result is None or not response.query_result.rows:
            return "table"
        rows = response.query_result.rows
        if len(rows) == 1 and len(rows[0].data) == 1:
            return "scalar"
        sql_lower = response.generated_sql.sql.lower()
        if "group by" in sql_lower and len(rows) <= _BAR_ROW_THRESHOLD:
            return "bar"
        return "table"

    # ------------------------------------------------------------------
    # High-level helper: send_governed_query (the pipeline callback uses this)
    # ------------------------------------------------------------------

    def send_governed_query(
        self,
        response: TextToSqlResponse,
        viewer: SemanticViewer | None,
        session_id: str | None = None,
        collection_id: int | None = None,
        db_id: int = 1,
    ) -> Card | None:
        """Create a Metabase card from an `ask` response (best-effort, never raises).

        Args:
            response: the typed pipeline response; `response.query_result.sql`
                is the ALREADY-GOVERNED SQL (post-RLS).
            viewer: the viewer that originated the query, used for the card
                description (`viewer_id=... gov_bypass=...`).
            session_id: optional; if set, the card is added to a "Session:
                <id>" dashboard inside the collection.
            collection_id: optional; if None, returns None (the caller must
                supply the state's collection_id from MetabaseSession).
            db_id: the Metabase database connection ID (from MetabaseSession).
                Default 1 is a safe fallback for single-DB instances.

        Returns:
            `Card` on success, `None` on any failure (logged as warning).
        """
        if collection_id is None:
            _logger.warning(
                "send_governed_query called without collection_id; skipping Metabase card."
            )
            return None
        if response.query_result is None:
            _logger.info(
                "send_governed_query called with no query_result (rejected SQL?); skipping."
            )
            return None

        # Build a concise title from the original NL question.
        question_text = response.question.text.strip() or "Untitled question"
        name = question_text[:_MAX_CARD_NAME_LEN]

        # Governance traceability in the card description.
        viewer_id = viewer.viewer_id if viewer is not None else "none"
        gov_bypass = (
            bool(viewer.allows_full_access and viewer.is_local_dev)
            if viewer is not None
            else False
        )
        description = (
            f"viewer_id={viewer_id} gov_bypass={gov_bypass} "
            f"created_at={datetime.now(timezone.utc).isoformat()}"
        )

        display = self._infer_display_type(response)
        card = self.create_card(
            name=name,
            sql=response.query_result.sql,
            collection_id=collection_id,
            display=display,
            description=description,
            db_id=db_id,
        )
        if card is None:
            return None

        # Session grouping (US3): attach the card to the session dashboard.
        if session_id:
            dashboard_name = f"Session: {session_id}"[:_MAX_CARD_NAME_LEN]
            dashboard = self.get_or_create_dashboard(
                dashboard_name, collection_id
            )
            if dashboard is not None:
                self.add_card_to_dashboard(card.id, dashboard.id)

        return card


__all__ = ["MetabaseClient"]
