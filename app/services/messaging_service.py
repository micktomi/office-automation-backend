from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class MessagingService:
    def __init__(self):
        self.settings = get_settings()
        self.whatsapp_url = self.settings.whatsapp_gateway_url
        logger.info("MessagingService: WhatsApp gateway configured at %s", self.whatsapp_url)

    async def send_whatsapp(
        self,
        to: str,
        message: str,
        client_name: str | None = None,
        policy_number: str | None = None,
    ) -> dict:
        """Sends a WhatsApp message via the local gateway (port 3400)."""
        from app.services.activity_service import log_action

        logger.info("Service: Sending WhatsApp to %s", to)
        try:
            async with httpx.AsyncClient() as client:
                payload = {"to": to, "message": message}
                response = await client.post(
                    self.whatsapp_url, json=payload, timeout=10.0
                )

                if response.status_code == 200:
                    log_action(
                        action_type="Αποστολή WhatsApp",
                        client_name=client_name or to,
                        policy_number=policy_number,
                        channel="whatsapp",
                        status="success",
                    )
                    return {"status": "sent", "id": response.json().get("id")}
                else:
                    log_action(
                        action_type="Αποστολή WhatsApp",
                        client_name=client_name or to,
                        policy_number=policy_number,
                        channel="whatsapp",
                        status="failed",
                    )
                    return {
                        "status": "failed",
                        "error": f"Gateway error: {response.status_code}",
                    }
        except Exception as e:
            logger.error("MessagingService Error: %s", e)
            log_action(
                action_type="Αποστολή WhatsApp",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="whatsapp",
                status="failed",
            )
            return {"status": "failed", "error": "Gateway unavailable"}

    async def send_sms(
        self,
        to: str,
        message: str,
        client_name: str | None = None,
        policy_number: str | None = None,
    ) -> dict:
        """Sends an SMS through the configured provider."""
        from app.services.activity_service import log_action

        provider = (self.settings.sms_provider or "").strip().lower()
        if provider != "twilio":
            logger.warning("SMS provider is not configured")
            log_action(
                action_type="Αποστολή SMS υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="sms",
                status="failed",
            )
            return {
                "status": "failed",
                "error": "SMS provider not configured. Set SMS_PROVIDER=twilio and Twilio credentials.",
            }

        account_sid = self.settings.twilio_account_sid.strip()
        auth_token = self.settings.twilio_auth_token.strip()
        messaging_service_sid = self.settings.twilio_messaging_service_sid.strip()
        from_number = self.settings.sms_from_number.strip()

        if not account_sid or not auth_token:
            log_action(
                action_type="Αποστολή SMS υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="sms",
                status="failed",
            )
            return {"status": "failed", "error": "Twilio credentials are missing"}
        if not messaging_service_sid and not from_number:
            log_action(
                action_type="Αποστολή SMS υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="sms",
                status="failed",
            )
            return {
                "status": "failed",
                "error": "SMS sender is missing. Set TWILIO_MESSAGING_SERVICE_SID or SMS_FROM_NUMBER.",
            }

        payload = {"To": to, "Body": message}
        if messaging_service_sid:
            payload["MessagingServiceSid"] = messaging_service_sid
        else:
            payload["From"] = from_number

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    data=payload,
                    auth=(account_sid, auth_token),
                    timeout=15.0,
                )

            if response.status_code not in {200, 201}:
                logger.error(
                    "Twilio SMS failed: %s %s", response.status_code, response.text
                )
                log_action(
                    action_type="Αποστολή SMS υπενθύμισης",
                    client_name=client_name or to,
                    policy_number=policy_number,
                    channel="sms",
                    status="failed",
                )
                return {
                    "status": "failed",
                    "error": f"Twilio error: {response.status_code}",
                }

            body = response.json()
            log_action(
                action_type="Αποστολή SMS υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="sms",
                status="success",
            )
            return {
                "status": "sent",
                "id": body.get("sid"),
                "provider": "twilio",
                "to": body.get("to", to),
            }
        except Exception as exc:
            logger.error("SMS send failed: %s", exc)
            log_action(
                action_type="Αποστολή SMS υπενθύμισης",
                client_name=client_name or to,
                policy_number=policy_number,
                channel="sms",
                status="failed",
            )
            return {"status": "failed", "error": f"SMS gateway unavailable: {exc}"}


messaging_service = MessagingService()
