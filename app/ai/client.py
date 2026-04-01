from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class AIClient:
    """Gemini-powered service for NLP routing and text generation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if settings.gemini_api_key:
            self._client = genai.Client(api_key=settings.gemini_api_key)
            self._model_name = settings.gemini_model
        else:
            self._client = None
            logger.warning("Gemini API key not found. AI features disabled.")

    @staticmethod
    def _clean_json(raw: str) -> str:
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return cleaned.strip()

    @staticmethod
    def _heuristic_route_intent(user_message: str, conversation_context: str | None = None) -> dict[str, Any]:
        text = " ".join(part for part in [conversation_context or "", user_message] if part).casefold()

        rules: list[tuple[str, tuple[str, ...], str, float]] = [
            ("sync_inbox", ("sync inbox", "συγχρον", "update inbox", "refresh inbox"), "Συγχρονίζω το inbox.", 0.95),
            ("scan_insurance", ("scan insurance", "σκαν", "λήξ", "ληξ", "συμβολ", "renewal"), "Ψάχνω για λήξεις συμβολαίων.", 0.95),
            ("list_insurance_alerts", ("alerts", "ειδοποι", "λήξεις ασφαλισ", "expiring"), "Ελέγχω τις λήξεις ασφαλιστηρίων.", 0.9),
            ("generate_reply", ("draft reply", "απάντη", "reply", "απάντηση"), "Ετοιμάζω draft απάντησης.", 0.85),
            ("list_needs_reply", ("needs reply", "χρειάζονται απάντηση", "χωρίς απάντηση"), "Φιλτράρω τα emails που περιμένουν απάντηση.", 0.85),
            ("list_emails", ("emails", "email", "mail"), "Ορίστε τα emails σου.", 0.8),
            ("list_calendar", ("calendar", "ημερολόγ", "ραντεβ", "meeting"), "Ελέγχω το ημερολόγιο.", 0.8),
            ("create_calendar_event", ("βάλε ραντεβού", "κλείσε ραντεβού", "schedule", "create event"), "Προσθέτω το ραντεβού.", 0.85),
            ("delete_calendar_event", ("διάγραψ", "ακύρ", "cancel event", "delete event"), "Διαγράφω το ραντεβού.", 0.85),
            ("list_tasks", ("tasks", "εργασ", "εκκρεμ"), "Ορίστε τα εκκρεμή tasks.", 0.8),
            ("create_task", ("πρόσθεσε task", "add task", "θυμίσου", "remind me"), "Προσθέτω το task.", 0.85),
            ("complete_task", ("τελείωσ", "ολοκλήρ", "done", "finished"), "Σημειώνω το task ως ολοκληρωμένο.", 0.85),
            ("batch_sms_reminders", ("batch sms", "μαζικά sms", "sms reminders"), "Στέλνω μαζικά SMS υπενθύμισης.", 0.9),
            ("send_message", ("στείλε μήνυμα", "send message", "ενημέρωσε τον πελάτη"), "Ετοιμάζω το μήνυμα προς αποστολή.", 0.9),
        ]

        for action, keywords, response, confidence in rules:
            if any(keyword in text for keyword in keywords):
                return {"action": action, "response": response, "confidence": confidence}

        if re.search(r"\b(sms|whatsapp|μήνυμα|message)\b", text):
            return {"action": "send_message", "response": "Ετοιμάζω το μήνυμα προς αποστολή.", "confidence": 0.75}

        return {"action": "chat", "response": "Πώς μπορώ να βοηθήσω;", "confidence": 0.5}

    async def _generate_content(
        self, prompt: str, *, json_mode: bool = False, temperature: float = 0.2
    ) -> str:
        if not self._client:
            raise ValueError("Gemini client not initialized")

        config = types.GenerateContentConfig(
            response_mime_type="application/json" if json_mode else "text/plain",
            temperature=temperature,
        )

        async def _call(model: str) -> str:
            def _blocking() -> str:
                response = self._client.models.generate_content(
                    model=model,
                    contents=[prompt],
                    config=config,
                )
                return response.text

            return await asyncio.to_thread(_blocking)

        try:
            return await _call(self._model_name)
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                logger.warning("Model %s not found. Falling back.", self._model_name)
                return await _call("gemini-1.5-flash")
            logger.error("[AI_CLIENT] Error: %s", e)
            raise

    async def route_intent(self, user_message: str, conversation_context: str | None = None) -> dict[str, Any]:
        """Route user message to the appropriate action via intent_router prompt."""
        fallback = self._heuristic_route_intent(user_message, conversation_context)
        if not self._client:
            logger.warning("Gemini client unavailable. Using heuristic intent router.")
            return fallback

        prompt_template = (PROMPTS_DIR / "intent_router.md").read_text(encoding="utf-8")
        prompt_input = user_message
        if conversation_context:
            prompt_input = f"{conversation_context}\n\nΤελευταίο μήνυμα χρήστη: {user_message}"
        prompt = prompt_template.replace("{user_message}", prompt_input)

        try:
            raw = await self._generate_content(prompt, json_mode=True)
            result = json.loads(self._clean_json(raw))

            if result.get("confidence", 1.0) < 0.6:
                result["action"] = "chat"

            return result
        except Exception as e:
            logger.error("[INTENT_ROUTER] Failed: %s", e)
            return fallback

    async def chat_response(self, user_message: str, action_result: str | None = None, context_data: str | None = None) -> str:
        """Fallback chat when no specific tool is called or to explain an error."""
        if not self._client:
            return "Μπορώ να βοηθήσω με emails, συμβόλαια, calendar, tasks και μηνύματα."

        prompt = (
            "Είσαι ο Geminako (Γεμινάκος), ο έξυπνος βοηθός ενός ασφαλιστικού γραφείου. "
            "Είσαι φιλικός, άμεσος και εξυπηρετικός. Χρησιμοποιείς φυσικό λόγο, όχι ρομποτικό.\n\n"
        )
        
        if context_data:
            prompt += f"--- ΔΕΔΟΜΕΝΑ ΓΡΑΦΕΙΟΥ ---\n{context_data}\n----------------------\n\n"

        prompt += f"Ο χρήστης σου είπε: '{user_message}'.\n"
        
        if action_result:
            prompt += f"Επιπλέον, το σύστημα μόλις εκτέλεσε ενέργεια στο παρασκήνιο με αποτέλεσμα: '{action_result}'.\n"
        
        prompt += "Απάντησε στο χρήστη σύντομα (1-2 προτάσεις), φυσικά και φιλικά στα ελληνικά."

        try:
            return await self._generate_content(prompt, temperature=0.7)
        except Exception:
            return "Κατανοητό. Πώς αλλιώς μπορώ να βοηθήσω?"

    async def extract_event_info(self, text: str) -> dict[str, Any]:
        """Extract event info from text using AI."""
        fallback = {"confidence": "low"}
        if not self._client:
            return fallback

        prompt_template = (PROMPTS_DIR / "event_extract.md").read_text(encoding="utf-8")
        prompt = prompt_template.replace("{text}", text)

        try:
            raw = await self._generate_content(prompt, json_mode=True)
            return json.loads(self._clean_json(raw))
        except Exception as e:
            logger.error("[EVENT_EXTRACT] Failed: %s", e)
            return fallback

    async def extract_insurance_info(self, sender: str, subject: str, body: str) -> dict[str, Any]:
        """Extract insurance renewal details from an email."""
        fallback = {
            "is_insurance": False,
            "policy_holder": None,
            "policy_number": None,
            "insurer": None,
            "expiry_date": None,
            "draft_notification_greek": None,
        }
        if not self._client:
            return fallback

        prompt_template = (PROMPTS_DIR / "insurance_extract.md").read_text(encoding="utf-8")
        prompt = (
            prompt_template.replace("{sender}", sender)
            .replace("{subject}", subject)
            .replace("{body}", body)
        )

        try:
            raw = await self._generate_content(prompt, json_mode=True, temperature=0.1)
            result = json.loads(self._clean_json(raw))
            if not isinstance(result, dict):
                return fallback
            return {**fallback, **result}
        except Exception as e:
            logger.error("[INSURANCE_EXTRACT] Failed: %s", e)
            return fallback

    async def generate_email_reply(self, sender: str, subject: str, body: str) -> str:
        """Generate an email reply using the reply_generator prompt."""
        if not self._client:
            return "Αυτόματη απάντηση (το AI δεν είναι ενεργοποιημένο)."

        prompt_template = (PROMPTS_DIR / "reply_generator.md").read_text(encoding="utf-8")
        prompt = prompt_template.replace("{sender}", sender).replace("{subject}", subject).replace("{body}", body)

        try:
            return await self._generate_content(prompt, temperature=0.1)
        except Exception as e:
            logger.error("[REPLY_GENERATOR] Failed: %s", e)
            return "Ευχαριστούμε για το μήνυμά σας. Θα επικοινωνήσουμε σύντομα."
