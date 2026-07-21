"""
Gmail integration service: OAuth2 authorization, token management, email listing,
inbox sync into ChromaDB, and account disconnection.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import pickle
import re as _re_html
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.gmail_sync import GmailToken, GmailSyncedEmail
from app.services.embedding_service import embedding_service
from app.repositories.chroma_repository import ChromaRepository
from app.utils.ids import new_uuid

logger = logging.getLogger(__name__)

# Allows OAuth2 over plain HTTP in local development; must be removed in production.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Gmail readonly scope — sufficient for listing and fetching email content.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Separate ChromaDB collection to keep Gmail embeddings isolated from document chunks.
GMAIL_CHROMA_COLLECTION = "gmail_chunks"

# Path to credentials.json; mount into the container or place alongside this file.
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "..", "gmail_credentials.json")


# Return the gmail_chunks ChromaDB collection, creating it if it does not exist.
def _get_gmail_collection():
    import chromadb
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    return client.get_or_create_collection(
        name=GMAIL_CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


# Serialize a Credentials object to a base64 string for DB storage.
def _token_to_str(creds: Credentials) -> str:
    return base64.b64encode(pickle.dumps(creds)).decode()


# Deserialize a base64 string back into a Credentials object.
def _str_to_token(s: str) -> Credentials:
    return pickle.loads(base64.b64decode(s.encode()))


# Handles Gmail OAuth2 token lifecycle, email listing, sync, and disconnection.
class GmailService:

    # Build the Google OAuth2 authorization URL, stripping any PKCE challenge params.
    def get_auth_url(self, redirect_uri: str) -> str:
        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE, scopes=SCOPES, redirect_uri=redirect_uri
        )
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
        # Strip PKCE code_challenge params — not supported by this flow.
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.pop("code_challenge", None)
        params.pop("code_challenge_method", None)
        new_query = urlencode({k: v[0] for k, v in params.items()})
        url = urlunparse(parsed._replace(query=new_query))
        return url

    # Exchange an authorization code for OAuth2 credentials.
    def exchange_code(self, code: str, redirect_uri: str) -> Credentials:
        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE, scopes=SCOPES, redirect_uri=redirect_uri
        )
        # include_client_id bypasses PKCE code_verifier requirement.
        flow.fetch_token(
            code=code,
            include_client_id=True,
        )
        return flow.credentials

    # Persist or update the serialized credentials for a user.
    def save_token(self, db: Session, user_id: str, creds: Credentials) -> None:
        token_str = _token_to_str(creds)
        existing = db.query(GmailToken).filter(GmailToken.user_id == user_id).first()
        if existing:
            existing.token_json = token_str
        else:
            db.add(GmailToken(id=new_uuid(), user_id=user_id, token_json=token_str))
        db.commit()

    # Load and auto-refresh credentials for a user; returns None if not connected.
    def load_token(self, db: Session, user_id: str) -> Credentials | None:
        row = db.query(GmailToken).filter(GmailToken.user_id == user_id).first()
        if not row:
            return None
        creds = _str_to_token(row.token_json)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.save_token(db, user_id, creds)
        return creds

    # Return True if the user has a stored Gmail token.
    def is_connected(self, db: Session, user_id: str) -> bool:
        return db.query(GmailToken).filter(GmailToken.user_id == user_id).first() is not None

    # Build an authenticated Gmail API service client.
    def _build_service(self, creds: Credentials):
        return build("gmail", "v1", credentials=creds)
    
    # Strip HTML tags, style/script blocks, and redundant whitespace from an email body.
    @staticmethod
    def _strip_html(html: str) -> str:
        if not html:
            return ""
        # Remove <style> and <script> blocks including their CSS/JS content.
        html = _re_html.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=_re_html.DOTALL | _re_html.IGNORECASE)
        # Remove HTML comments (<!--...-->) which often contain email client padding noise.
        html = _re_html.sub(r"<!--.*?-->", "", html, flags=_re_html.DOTALL)
        # Replace remaining tags with a space to preserve word boundaries.
        html = _re_html.sub(r"<[^>]+>", " ", html)
        # Decode common HTML entities.
        html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                    .replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", '"').replace("&#39;", "'"))
        # Collapse consecutive whitespace and excess blank lines.
        html = _re_html.sub(r"[ \t]+", " ", html)
        html = _re_html.sub(r"\n{3,}", "\n\n", html)
        return html.strip()

    # Extract the text/plain part of a message payload for RAG embedding.
    def _get_body(self, payload: dict) -> str:
        def extract(parts):
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                if "parts" in part:
                    result = extract(part["parts"])
                    if result:
                        return result
            return ""

        if "parts" in payload:
            return extract(payload["parts"])
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        return ""

    # Extract the text/html part of a message payload for frontend display.
    def _get_html_body(self, payload: dict) -> str:
        def extract(parts):
            for part in parts:
                if part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                if "parts" in part:
                    result = extract(part["parts"])
                    if result:
                        return result
            return ""

        if "parts" in payload:
            result = extract(payload["parts"])
            if result:
                return result
        # Single-part email where the top-level mimeType is text/html.
        if payload.get("mimeType") == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        return ""

    # Return the value of a named header (case-insensitive), or empty string if absent.
    def _get_header(self, headers: list, name: str) -> str:
        for h in headers:
            if h["name"].lower() == name.lower():
                return h["value"]
        return ""

    # List up to max_results inbox emails, marking which ones have already been synced.
    def list_emails(self, db: Session, user_id: str, max_results: int = 50) -> list[dict]:
        creds = self.load_token(db, user_id)
        if not creds:
            raise ValueError("Gmail chưa được kết nối")

        service = self._build_service(creds)

        # Load previously synced message IDs to set the synced flag in the response.
        synced_ids = {
            row.message_id
            for row in db.query(GmailSyncedEmail).filter(
                GmailSyncedEmail.user_id == user_id
            ).all()
        }

        result = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            labelIds=["INBOX"],
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return []

        # Fetch all messages in a single batch request instead of N sequential calls.
        emails = []
        batch = service.new_batch_http_request()
        msg_data = {}

        def callback(request_id, response, exception):
            if exception:
                logger.warning("Batch fetch error for %s: %s", request_id, exception)
                return
            msg_data[request_id] = response

        for ref in messages:
            batch.add(
                service.users().messages().get(
                    userId="me", id=ref["id"], format="full"
                ),
                request_id=ref["id"],
                callback=callback,
            )

        batch.execute()

        for ref in messages:
            msg = msg_data.get(ref["id"])
            if not msg:
                continue
            headers = msg["payload"]["headers"]
            body = self._get_body(msg["payload"])
            body_html = self._get_html_body(msg["payload"])
            emails.append({
                "message_id": ref["id"],
                "thread_id": msg.get("threadId", ""),
                "subject": self._get_header(headers, "Subject"),
                "from": self._get_header(headers, "From"),
                "to": self._get_header(headers, "To"),
                "date": self._get_header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
                "body": body,
                "body_html": body_html,
                "label_ids": msg.get("labelIds", []),
                "synced": ref["id"] in synced_ids,
            })

        return emails

    # Embed unsynced inbox emails into ChromaDB and record them in MySQL.
    def sync_emails(self, db: Session, user_id: str, max_results: int = 50) -> dict:
        import time

        creds = self.load_token(db, user_id)
        if not creds:
            raise ValueError("Gmail chưa được kết nối")

        service = self._build_service(creds)
        result = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            labelIds=["INBOX"],
        ).execute()

        messages = result.get("messages", [])

        synced_ids = {
            row.message_id
            for row in db.query(GmailSyncedEmail).filter(
                GmailSyncedEmail.user_id == user_id
            ).all()
        }

        # Chroma is empty but MySQL still has records — reset sync history to force re-embed.
        collection = _get_gmail_collection()
        chroma_count = collection.count()
        if chroma_count == 0 and synced_ids:
            logger.warning(
                "Chroma empty but MySQL has %d records — resetting sync history",
                len(synced_ids),
            )
            db.query(GmailSyncedEmail).filter(
                GmailSyncedEmail.user_id == user_id
            ).delete()
            db.commit()
            synced_ids = set()

        new_emails = [m for m in messages if m["id"] not in synced_ids]
        if not new_emails:
            return {"synced": 0, "skipped": len(messages)}

        # Fetch in small batches to avoid Gmail API rate limit (429).
        BATCH_SIZE = 10
        all_msg_data: dict = {}

        for i in range(0, len(new_emails), BATCH_SIZE):
            batch_chunk = new_emails[i:i + BATCH_SIZE]
            batch = service.new_batch_http_request()
            chunk_data: dict = {}

            def make_callback(chunk_dict: dict):
                def callback(request_id, response, exception):
                    if exception:
                        logger.warning("Batch fetch error for %s: %s", request_id, exception)
                        return
                    chunk_dict[request_id] = response
                return callback

            for ref in batch_chunk:
                batch.add(
                    service.users().messages().get(
                        userId="me", id=ref["id"], format="full"
                    ),
                    request_id=ref["id"],
                    callback=make_callback(chunk_data),
                )

            batch.execute()
            all_msg_data.update(chunk_data)

            # Brief delay between batches to stay within rate limits.
            if i + BATCH_SIZE < len(new_emails):
                time.sleep(1.0)

        synced_count = 0

        for ref in new_emails:
            msg = all_msg_data.get(ref["id"])
            if not msg:
                continue

            headers = msg["payload"]["headers"]
            body = self._get_body(msg["payload"])
            subject = self._get_header(headers, "Subject")
            sender = self._get_header(headers, "From")
            date_str = self._get_header(headers, "Date")
            snippet = msg.get("snippet", "")

            # If body is empty or contains raw HTML, strip it
            if not body.strip() or "<" in body:
                html_body = self._get_html_body(msg["payload"])
                if html_body:
                    body = GmailService._strip_html(html_body)
                elif "<" in body:
                    body = GmailService._strip_html(body)

            text = f"Subject: {subject}\nFrom: {sender}\nDate: {date_str}\n\n{body or snippet}"
            text = text.strip()[:8000]

            if not text:
                continue

            try:
                embedding = embedding_service.embed(text)
                chunk_id = f"gmail_{user_id}_{ref['id']}"

                collection.upsert(
                    ids=[chunk_id],
                    documents=[text],
                    embeddings=[embedding],
                    metadatas=[{
                        "source": "gmail",
                        "user_id": user_id,
                        "message_id": ref["id"],
                        "subject": subject,
                        "from": sender,
                        "date": date_str,
                        "document_id": chunk_id,
                        "document_title": subject or "(no subject)",
                        "document_version_id": "",
                        "sensitivity": "2",
                    }],
                )

                db.add(GmailSyncedEmail(
                    id=new_uuid(),
                    user_id=user_id,
                    message_id=ref["id"],
                    subject=subject,
                    sender=sender,
                    date_str=date_str,
                    embedded=True,
                ))
                synced_count += 1

            except Exception as e:
                logger.warning("Failed to embed email %s: %s", ref["id"], e)

        db.commit()
        return {"synced": synced_count, "skipped": len(messages) - len(new_emails)}

    # Sync a single email by message_id into ChromaDB and MySQL.
    def sync_single_email(self, db: Session, user_id: str, message_id: str) -> dict:
        already = db.query(GmailSyncedEmail).filter(
            GmailSyncedEmail.user_id == user_id,
            GmailSyncedEmail.message_id == message_id,
        ).first()
        if already:
            return {"synced": 0, "skipped": 1, "already_synced": True}

        creds = self.load_token(db, user_id)
        if not creds:
            raise ValueError("Gmail chưa được kết nối")

        service = self._build_service(creds)
        try:
            msg = service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
        except Exception as e:
            raise ValueError(f"Không thể tải email: {e}")

        headers = msg["payload"]["headers"]
        body = self._get_body(msg["payload"])
        subject = self._get_header(headers, "Subject")
        sender = self._get_header(headers, "From")
        date_str = self._get_header(headers, "Date")
        snippet = msg.get("snippet", "")

        if not body.strip() or "<" in body:
            html_body = self._get_html_body(msg["payload"])
            if html_body:
                body = GmailService._strip_html(html_body)
            elif "<" in body:
                body = GmailService._strip_html(body)

        text = f"Subject: {subject}\nFrom: {sender}\nDate: {date_str}\n\n{body or snippet}"
        text = text.strip()[:8000]
        if not text:
            raise ValueError("Email không có nội dung để đồng bộ")

        collection = _get_gmail_collection()
        embedding = embedding_service.embed(text)
        chunk_id = f"gmail_{user_id}_{message_id}"

        collection.upsert(
            ids=[chunk_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[{
                "source": "gmail",
                "user_id": user_id,
                "message_id": message_id,
                "subject": subject,
                "from": sender,
                "date": date_str,
                "document_id": chunk_id,
                "document_title": subject or "(no subject)",
                "document_version_id": "",
                "sensitivity": "2",
            }],
        )

        db.add(GmailSyncedEmail(
            id=new_uuid(),
            user_id=user_id,
            message_id=message_id,
            subject=subject,
            sender=sender,
            date_str=date_str,
            embedded=True,
        ))
        db.commit()
        return {"synced": 1, "skipped": 0, "already_synced": False}

    # Remove all user Gmail data: delete ChromaDB chunks and MySQL token/sync records.
    def disconnect(self, db: Session, user_id: str) -> None:
        import chromadb

        # Build chunk IDs from MySQL sync records.
        synced = db.query(GmailSyncedEmail).filter(
            GmailSyncedEmail.user_id == user_id
        ).all()
        chunk_ids = [f"gmail_{user_id}_{row.message_id}" for row in synced]

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)

        for col_name in ["document_chunks", "gmail_chunks"]:
            try:
                col = client.get_or_create_collection(
                    name=col_name, metadata={"hnsw:space": "cosine"}
                )
                # Delete by known chunk IDs first.
                if chunk_ids:
                    col.delete(ids=chunk_ids)

                # Fallback: delete any leftover chunks matching the user prefix.
                results = col.get(include=[])
                leftover = [id_ for id_ in (results.get("ids") or [])
                            if id_.startswith(f"gmail_{user_id}_")]
                if leftover:
                    col.delete(ids=leftover)
                    logger.info("Cleaned %d leftover gmail chunks from '%s'", len(leftover), col_name)
            except Exception as e:
                logger.warning("Failed to clean '%s': %s", col_name, e)

        # Remove MySQL sync records and token.
        db.query(GmailSyncedEmail).filter(GmailSyncedEmail.user_id == user_id).delete()
        db.query(GmailToken).filter(GmailToken.user_id == user_id).delete()
        db.commit()


# Module-level singleton; imported by the gmail API router.
gmail_service = GmailService()