from __future__ import annotations

import asyncio
import json
import logging
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
            self._model_name = "gemini-3.1-flash-lite-preview"
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
        fallback = {"action": "chat", "response": "Πώς μπορώ να βοηθήσω;", "confidence": 0.5}
        if not self._client:
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

    async def chat_response(self, user_message: str, action_result: str | None = None) -> str:
        """Fallback chat when no specific tool is called or to explain an error."""
        if not self._client:
            return "Δεν υπάρχει εγκατεστημένο AI για συνομιλία."

        prompt = (
            "Είσαι ο Geminako (Γεμινάκος), ο έξυπνος βοηθός ενός ασφαλιστικού γραφείου. "
            "Είσαι φιλικός, άμεσος και εξυπηρετικός. Χρησιμοποιείς φυσικό λόγο, όχι ρομποτικό. "
            "Μπορείς να εκτελέσεις συγκεκριμένες ενέργειες: "
            "1. Να δεις τα email του χρήστη και να συγχρονίσεις το inbox.\n"
            "2. Να φτιάξεις draft απαντήσεις σε email.\n"
            "3. Να σκανάρεις τα email για να βρεις συμβόλαια που λήγουν.\n"
            "4. Να δείξεις τις ειδοποιήσεις για ασφάλειες που λήγουν σύντομα.\n"
            "5. Να στείλεις μαζικά SMS υπενθύμισης για λήξεις.\n"
            "6. Να γράψεις, να ολοκληρώσεις και να δείξεις λίστες με tasks/εργασίες.\n"
            "7. Να δεις το ημερολόγιο (Calendar) και να προσθέσεις/διαγράψεις ραντεβού.\n"
            "8. Να στείλεις μηνύματα στο κινητό μέσω WhatsApp.\n\n"
            f"Ο χρήστης σου είπε: '{user_message}'.\n"
        )
        if action_result:
            prompt += f"Επιπλέον, το σύστημα μόλις εκτέλεσε ενέργεια στο παρασκήνιο με αποτέλεσμα: '{action_result}'.\n"
        prompt += "Απάντησε στο χρήστη σύντομα (1-2 προτάσεις), φυσικά και φιλικά στα ελληνικά. Αν ζητάει κάτι άσχετο με τις δυνατότητές σου, καθοδήγησέ τον σε αυτές που έχεις."

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
