import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHROMA_HOST = os.getenv("CHROMA_BRIDGE_HOST", "127.0.0.1")
CHROMA_PORT = int(os.getenv("CHROMA_BRIDGE_PORT", "12346"))
CHROMA_TIMEOUT = float(os.getenv("CHROMA_BRIDGE_TIMEOUT", "10"))
CONFIG_FOLDERS = [
    folder.strip()
    for folder in os.getenv("CHROMA_CONFIG_FOLDERS", "configs,config").split(",")
    if folder.strip()
]


@dataclass
class ChromaDocument:
    id: str
    title: str
    content: str
    full_content: Optional[str] = None

    @property
    def resolved_content(self) -> str:
        return self.full_content or self.content or ""


class ChromaBridgeClient:
    """Tiny JSON-over-telnet client for chroma.py."""

    def __init__(self, host: str = CHROMA_HOST, port: int = CHROMA_PORT, timeout: float = CHROMA_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout

    def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.host, self.port))
            self._read_json_line(sock)  # welcome banner
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            return self._read_json_line(sock)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _read_json_line(self, sock: socket.socket) -> dict[str, Any]:
        buf = b""
        start = time.time()
        while time.time() - start < self.timeout:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
                if line:
                    return json.loads(line)
        raise TimeoutError("Timed out waiting for ChromaDB bridge response")

    def query(self, folder: str, content: str = "", number: int = 100, full: bool = False) -> list[ChromaDocument]:
        response = self.command({
            "type": "query",
            "content": content,
            "threshold": 0.0,
            "number": number,
            "folder": folder,
            "full": full,
        })
        if response.get("status") != "success":
            raise RuntimeError(response.get("message", "ChromaDB query failed"))
        docs = []
        for result in response.get("results", []):
            docs.append(ChromaDocument(
                id=str(result.get("id", "")),
                title=str(result.get("title", "Unknown")),
                content=result.get("content") or "",
                full_content=result.get("full_content"),
            ))
        return docs


class ConfigHistoryService:
    def __init__(self, client: Optional[ChromaBridgeClient] = None):
        self.client = client or ChromaBridgeClient()

    @staticmethod
    def is_config_history_query(query: str) -> bool:
        q = query.lower()
        has_config = bool(re.search(r"\b(config|configuration)\b", q))
        has_history_time = any(word in q for word in (
            "what was", "as of", "at ", " on ", "history", "previous", "old", "past",
            "yesterday", "today", "am", "pm",
        ))
        return has_config and has_history_time

    def answer(self, query: str) -> Optional[str]:
        try:
            folder, docs = self._load_titles()
        except Exception as exc:
            logger.warning("Unable to load ChromaDB config history: %s", exc)
            return f"I could not reach the ChromaDB config history store: {exc}"

        if not docs:
            return "I could not find any configuration documents in ChromaDB."

        requested_at = self._extract_requested_datetime(query)
        selected_doc = self._select_title_deterministically(query, docs, requested_at=requested_at)
        if selected_doc is None:
            selected_title = self._select_title_with_llm(query, [doc.title for doc in docs])
            selected_doc = self._find_doc_by_title(docs, selected_title)
            if not self._is_valid_snapshot_for_request(selected_doc, requested_at):
                selected_doc = None
        if selected_doc is None:
            titles = "\n".join(f"- {doc.title}" for doc in docs[:30])
            return (
                "I found config history documents, but could not confidently map your requested date/time "
                "to one of them. Available titles:\n" + titles
            )

        full_doc = self._load_full_document(folder, selected_doc.title) or selected_doc
        content = full_doc.resolved_content.strip()
        if not content:
            content = "(The selected config document has no content.)"

        return (
            f"Config at the requested time maps to `{full_doc.title}`.\n\n"
            "```text\n"
            f"{content}\n"
            "```"
        )

    def _load_titles(self) -> tuple[str, list[ChromaDocument]]:
        last_error = None
        for folder in CONFIG_FOLDERS:
            try:
                docs = self.client.query(folder=folder, content="", number=500, full=False)
                if docs:
                    return folder, docs
            except Exception as exc:
                last_error = exc
                logger.warning("ChromaDB config title query failed for folder %s: %s", folder, exc)
        if last_error:
            raise last_error
        return CONFIG_FOLDERS[0], []

    def _load_full_document(self, folder: str, title: str) -> Optional[ChromaDocument]:
        try:
            docs = self.client.query(folder=folder, content="", number=500, full=True)
        except Exception as exc:
            logger.warning("ChromaDB full document query failed for %s: %s", title, exc)
            return None
        return self._find_doc_by_title(docs, title)

    @staticmethod
    def _find_doc_by_title(docs: list[ChromaDocument], title: Optional[str]) -> Optional[ChromaDocument]:
        if not title:
            return None
        wanted = title.strip().lower()
        for doc in docs:
            if doc.title.strip().lower() == wanted:
                return doc
        return None

    def _select_title_with_llm(self, query: str, titles: list[str]) -> Optional[str]:
        numbered_titles = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))
        prompt = f"""<|start_header_id|>system<|end_header_id|>
You select a network configuration snapshot title for a historical config query.
Rules:
- Each title represents the time that config became active.
- Choose the latest title whose timestamp is less than or equal to the user's requested date/time.
- Do not choose a later snapshot.
- Match the date too. If the user did not provide enough date/time information, return null.
- Reply only as JSON: {{"selected_title": "exact title or null", "reason": "short reason"}}<|eot_id|>
<|start_header_id|>user<|end_header_id|>
User query: {query}

Available config snapshot titles:
{numbered_titles}<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
"""
        try:
            from app.core.llm_core import get_llm
            result = get_llm().generate(prompt=prompt, max_tokens=160, temperature=0.0)
            data = self._extract_json(result.get("response", ""))
            selected = data.get("selected_title")
            if isinstance(selected, str) and selected.strip().lower() != "null":
                return selected.strip()
        except Exception as exc:
            logger.warning("LLM config title selection failed: %s", exc)
        return None

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1))
        bare = re.search(r"\{.*\}", text, re.DOTALL)
        if bare:
            return json.loads(bare.group(0))
        return {}

    def _select_title_deterministically(
        self,
        query: str,
        docs: list[ChromaDocument],
        requested_at: Optional[datetime] = None,
    ) -> Optional[ChromaDocument]:
        requested = requested_at or self._extract_requested_datetime(query)
        if requested is None:
            return None

        candidates = []
        for doc in docs:
            stamp = self._extract_datetime_from_title(doc.title)
            if stamp and stamp <= requested:
                candidates.append((stamp, doc))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def _is_valid_snapshot_for_request(
        self,
        doc: Optional[ChromaDocument],
        requested_at: Optional[datetime],
    ) -> bool:
        if doc is None:
            return False
        if requested_at is None:
            return True
        stamp = self._extract_datetime_from_title(doc.title)
        return stamp is not None and stamp <= requested_at and stamp.date() == requested_at.date()

    @staticmethod
    def _extract_requested_datetime(query: str) -> Optional[datetime]:
        text = query.lower()
        date_match = re.search(r"(\d{4})[-_/](\d{1,2})[-_/](\d{1,2})", text)
        if not date_match:
            date_match = re.search(r"(\d{1,2})[-_/](\d{1,2})[-_/](\d{4})", text)
            if not date_match:
                return None
            day, month, year = map(int, date_match.groups())
        else:
            year, month, day = map(int, date_match.groups())

        time_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text)
        if not time_match:
            time_matches = list(re.finditer(r"\b(\d{1,2})(?::(\d{2}))\s*(am|pm)?\b", text))
            time_match = time_matches[-1] if time_matches else None
        if not time_match:
            hour, minute = 23, 59
        else:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or "0")
            ampm = time_match.group(3)
            if ampm == "pm" and 1 <= hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
        try:
            return datetime(year, month, day, hour, minute)
        except ValueError:
            return None

    @staticmethod
    def _extract_datetime_from_title(title: str) -> Optional[datetime]:
        patterns = [
            r"(\d{4})[-_/](\d{1,2})[-_/](\d{1,2})[ T_-]+(\d{1,2})(?::|-)?(\d{2})?(?::\d{2})?\s*(am|pm)?",
            r"(\d{1,2})[-_/](\d{1,2})[-_/](\d{4})[ T_-]+(\d{1,2})(?::|-)?(\d{2})?(?::\d{2})?\s*(am|pm)?",
        ]
        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, title.lower())
            if not match:
                continue
            values = [int(v) if v and v.isdigit() else 0 for v in match.groups()[:-1]]
            ampm = match.groups()[-1]
            if idx == 0:
                year, month, day, hour, minute = values
            else:
                day, month, year, hour, minute = values
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            try:
                return datetime(year, month, day, hour, minute)
            except ValueError:
                return None
        return None


_config_history_service: Optional[ConfigHistoryService] = None


def get_config_history_service() -> ConfigHistoryService:
    global _config_history_service
    if _config_history_service is None:
        _config_history_service = ConfigHistoryService()
    return _config_history_service
