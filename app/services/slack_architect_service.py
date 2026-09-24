"""Slack AI Architect integration.

Phase 1 — channel intelligence:
  - Inbound Events API: @archie mentions → AI response (ChiefArchitectService + LLM)
  - Passive portfolio monitoring: messages mentioning known app names → context card
  - Outbound: Web API chat.postMessage with Block Kit

Config stored in APISettings (provider='slack_bot', key_label='default'):
    api_key             — Bot Token (xoxb-...)
    jira_url            — Signing Secret (for HMAC verification)
    custom_endpoint_url — Comma-separated monitored channel IDs (blank = all)
    custom_headers      — JSON: {"portfolio_scan": bool, "mention_response": bool}
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"
_PROVIDER = "slack_bot"
_LABEL = "default"


class SlackArchitectService:

    # ------------------------------------------------------------------ #
    # Config                                                               #
    # ------------------------------------------------------------------ #

    @classmethod
    def _settings_row(cls):
        try:
            from app.models.models import APISettings
            return APISettings.query.filter_by(provider=_PROVIDER, key_label=_LABEL).first()
        except Exception as exc:
            logger.debug("slack: settings load error: %s", exc)
            return None

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        row = cls._settings_row()
        if not row:
            return {"configured": False, "portfolio_scan": True, "mention_response": True,
                    "bot_token": "", "signing_secret": "", "monitored_channels": ""}
        extras = {}
        try:
            extras = json.loads(row.custom_headers or "{}")
        except Exception:
            pass
        return {
            "configured": bool(row.api_key),
            "bot_token": row.api_key or "",
            "signing_secret": row.jira_url or "",
            "monitored_channels": row.custom_endpoint_url or "",
            "portfolio_scan": extras.get("portfolio_scan", True),
            "mention_response": extras.get("mention_response", True),
        }

    @classmethod
    def save_config(cls, bot_token: str, signing_secret: str,
                    monitored_channels: str, portfolio_scan: bool,
                    mention_response: bool) -> None:
        from app.models.models import APISettings
        from app import db
        row = APISettings.query.filter_by(provider=_PROVIDER, key_label=_LABEL).first()
        extras = json.dumps({"portfolio_scan": portfolio_scan,
                             "mention_response": mention_response})
        if row:
            row.api_key = bot_token or row.api_key
            row.jira_url = signing_secret or row.jira_url
            row.custom_endpoint_url = monitored_channels
            row.custom_headers = extras
        else:
            row = APISettings(
                provider=_PROVIDER,
                key_label=_LABEL,
                api_key=bot_token,
                jira_url=signing_secret,
                custom_endpoint_url=monitored_channels,
                custom_headers=extras,
                enabled=True,
            )
            db.session.add(row)
        db.session.commit()

    # ------------------------------------------------------------------ #
    # Signature verification (Slack v0 scheme)                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def verify_signature(body: bytes, timestamp: str, signature: str,
                         signing_secret: str) -> bool:
        """Return True when Slack's v0 HMAC-SHA256 signature is valid."""
        try:
            age = abs(time.time() - float(timestamp))
            if age > 300:  # replay protection: reject requests older than 5 min
                return False
            basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
            expected = "v0=" + hmac.new(
                signing_secret.encode(), basestring, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as exc:
            logger.warning("slack: signature verification error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Event routing                                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def handle_event(cls, payload: Dict[str, Any]) -> Optional[Dict]:
        """Route an incoming Events API payload. Returns reply dict or None."""
        cfg = cls.get_config()
        if not cfg.get("configured"):
            return None

        event = payload.get("event", {})
        etype = event.get("type", "")
        bot_token = cfg["bot_token"]
        channel = event.get("channel", "")

        # Skip bot's own messages to avoid infinite loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return None

        allowed = [c.strip() for c in (cfg["monitored_channels"] or "").split(",") if c.strip()]
        if allowed and channel not in allowed:
            return None

        if etype == "app_mention" and cfg.get("mention_response"):
            return cls._handle_mention(event, bot_token)

        if etype == "message" and cfg.get("portfolio_scan"):
            text = event.get("text", "")
            if text:
                cls._handle_portfolio_scan(event, bot_token, text)
        return None

    @classmethod
    def _handle_mention(cls, event: Dict, bot_token: str) -> Dict:
        channel = event.get("channel", "")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        # strip <@BOTID> from text
        question = text
        while "<@" in question and ">" in question:
            start = question.find("<@")
            end = question.find(">", start)
            question = (question[:start] + question[end + 1:]).strip()

        if not question:
            question = "What is the current state of the architecture portfolio?"

        logger.info("slack: mention in %s: %.80s", channel, question)
        answer, blocks = cls._ai_response(question)
        return cls.post_message(channel, blocks, answer, bot_token, thread_ts=thread_ts)

    @classmethod
    def _handle_portfolio_scan(cls, event: Dict, bot_token: str, text: str) -> None:
        mentions = cls._portfolio_mentions(text)
        if not mentions:
            return
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        blocks = cls._portfolio_mention_blocks(mentions)
        cls.post_message(channel, blocks,
                         "Entelim detected portfolio applications in this message.",
                         bot_token, thread_ts=thread_ts)

    # ------------------------------------------------------------------ #
    # AI response (ChiefArchitectService + LLM)                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def _ai_response(cls, question: str) -> Tuple[str, List[Dict]]:
        answer = "I couldn't generate a response right now. Check the Entelim dashboard."
        try:
            from app.modules.solutions_strategic.v2.services.chief_architect_service import (
                ChiefArchitectService,
            )
            from app.modules.ai_chat.services.llm_service_impl import LLMService

            portfolio = ChiefArchitectService.portfolio_synthesis()
            avg = portfolio.get("avg_conformance", "?")
            flagged = portfolio.get("flagged_total", 0)
            worst = portfolio.get("worst", [])
            worst_text = (
                ", ".join(f"{w['name']} ({w['flagged']} issues)" for w in worst[:3])
                or "none"
            )

            context = (
                f"{portfolio.get('solutions_reviewed', 0)} solutions reviewed. "
                f"Avg conformance: {avg}%. Total flagged: {flagged}. "
                f"Highest priority: {worst_text}."
            )
            prompt = (
                "You are Entelim, an enterprise architecture AI advisor embedded in Slack. "
                "Answer in 2-4 plain sentences. No markdown, no bullet points, no headers. "
                f"Live portfolio context: {context}\n\nQuestion: {question}"
            )
            llm = LLMService()
            raw = llm._call_llm(prompt, model=None, provider=None, user_id=None, max_tokens=250)
            answer = (raw or "").strip() or answer
        except Exception as exc:
            logger.warning("slack: AI response error: %s", exc)

        base_url = "http://127.0.0.1"

        # Build a Composer diagram for any portfolio apps mentioned in the question
        composer_url = None
        try:
            from app.services.archimate_composer_service import (
                element_ids_for_apps, create_diagram, full_composer_url,
            )
            # Detect app names in question text using the same portfolio scan
            mentioned_apps = [m["name"] for m in cls._portfolio_mentions(question)]
            if mentioned_apps:
                el_ids = element_ids_for_apps(mentioned_apps)
                if el_ids:
                    rel = create_diagram(
                        el_ids,
                        name=f"Slack Q: {question[:60]}",
                    )
                    if rel:
                        composer_url = full_composer_url(rel)
        except Exception as exc:
            logger.debug("slack: composer diagram skipped: %s", exc)

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":building_construction: *Entelim Architecture Advisor*\n{answer}",
                },
            },
        ]

        if composer_url:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":pencil: View in ArchiMate Composer"},
                        "url": composer_url,
                        "style": "primary",
                    }
                ],
            })

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"<{base_url}/dashboard/overview|Dashboard>  ·  "
                        f"<{base_url}/solutions/|Solutions>  ·  "
                        f"<{base_url}/archimate/composer|ArchiMate Composer>"
                    ),
                }
            ],
        })
        return answer, blocks

    # ------------------------------------------------------------------ #
    # Portfolio mention detection                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def _portfolio_mentions(cls, text: str) -> List[Dict]:
        """Return portfolio apps whose names appear (case-insensitive) in text.

        Includes the solution_id of the first solution using each app so that
        responses can link directly into the solution context.
        """
        try:
            from app.models.application_portfolio import ApplicationComponent
            apps = ApplicationComponent.query.with_entities(
                ApplicationComponent.id,
                ApplicationComponent.name,
                ApplicationComponent.lifecycle_status,
                ApplicationComponent.archimate_element_id,
            ).limit(600).all()
            text_lower = text.lower()
            hits = []
            for app in apps:
                name = app.name or ""
                if len(name) >= 5 and name.lower() in text_lower:
                    hits.append({
                        "id": app.id,
                        "name": name,
                        "lifecycle": app.lifecycle_status or "unknown",
                        "archimate_element_id": app.archimate_element_id,
                        "solution_id": cls._solution_for_app(app.id),
                    })
            return hits[:5]
        except Exception as exc:
            logger.debug("slack: portfolio scan error: %s", exc)
            return []

    @staticmethod
    def _solution_for_app(app_id: int) -> Optional[int]:
        """Return the first solution_id linked to this application, or None."""
        try:
            from app.models.solution_models import SolutionApplication
            row = SolutionApplication.query.filter_by(
                application_id=app_id
            ).first()
            return row.solution_id if row else None
        except Exception:
            return None

    @staticmethod
    def _portfolio_mention_blocks(mentions: List[Dict]) -> List[Dict]:
        base_url = "http://127.0.0.1"
        lines = []
        for m in mentions:
            status_ok = m["lifecycle"] in ("operational", "strategic", "active")
            emoji = ":white_check_mark:" if status_ok else ":warning:"
            lines.append(
                f"{emoji} *{m['name']}* — {m['lifecycle']} "
                f"(<{base_url}/applications/{m['id']}|View in portfolio>)"
            )
        # Add solution deep-links where available
        sol_links = []
        for m in mentions:
            if m.get("solution_id"):
                sol_links.append(
                    f"<{base_url}/solutions/{m['solution_id']}|{m['name']} solution>"
                )
        if sol_links:
            lines.append("Related solutions: " + "  ·  ".join(sol_links))

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":mag: *Entelim detected portfolio apps in this message:*\n"
                            + "\n".join(lines),
                },
            }
        ]

        # If any app has an ArchiMate element, generate a Composer diagram
        element_ids = [m["archimate_element_id"] for m in mentions
                       if m.get("archimate_element_id")]
        if element_ids:
            try:
                from app.services.archimate_composer_service import (
                    create_diagram, full_composer_url,
                )
                app_names = ", ".join(m["name"] for m in mentions[:3])
                rel = create_diagram(element_ids, name=f"Portfolio scan: {app_names[:60]}")
                if rel:
                    blocks.append({
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text",
                                         "text": ":pencil: View in ArchiMate Composer"},
                                "url": full_composer_url(rel),
                                "style": "primary",
                            }
                        ],
                    })
            except Exception as exc:
                logger.debug("slack: portfolio mention composer skipped: %s", exc)

        return blocks

    # ------------------------------------------------------------------ #
    # Slack Web API                                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def post_message(cls, channel: str, blocks: List[Dict], text: str,
                     bot_token: str, thread_ts: str = None) -> Dict:
        payload = {"channel": channel, "text": text, "blocks": blocks}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            resp = requests.post(
                f"{_SLACK_API}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("slack: postMessage error: %s", data.get("error"))
            return data
        except Exception as exc:
            logger.error("slack: postMessage failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    @classmethod
    def test_connection(cls, bot_token: str) -> Dict[str, Any]:
        try:
            resp = requests.post(
                f"{_SLACK_API}/auth.test",
                headers={"Authorization": f"Bearer {bot_token}"},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                return {"status": "ok", "team": data.get("team"), "bot_name": data.get("user"),
                        "bot_id": data.get("user_id")}
            return {"status": "error", "error": data.get("error", "unknown error from Slack")}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
