# AI Orchestration Architecture

This document describes how the AI capabilities are integrated into the deterministic core of the **Office Automation Backend**.

## The Separation of Concerns
The backend is strictly divided into two spaces:
1. **Deterministic Core (`app/engine`, `app/adapters`, `app/routers`)**: Does exactly what it is told without "guessing". It contains specific logic for tasks like extracting emails, scanning for policies, and returning reports.
2. **AI Orchestrator (`app/ai`)**: Acts as a natural language interface that sits on top of the deterministic core. It understands user intent and safely triggers the deterministic functions.

## Components of the AI Orchestrator

- **`client.py`**: A wrapper around the `google-genai` SDK. It converts system prompts into structured JSON responses using the `gemini-3.1-flash-lite-preview` model.
- **`orchestrator.py`**: The bridge. It receives user chat messages, sends them to `client.py` for intent mapping, maps the returned intent to a specific deterministic action name (e.g., `insurance.scan`), and then executes the action via `app.routers.agent.execute_action`.
- **`prompts/`**: Contains the instructions for the Gemini model. `intent_router.md` is the primary prompt that outlines available skills and how to respond with confidence scores.

## How it works

1. User sends a message via UI chat widget: `"Μπορείς να τσεκάρεις για λήξεις;"`
2. The UI sends a POST to `/assistant/chat`.
3. The `chat` router hands the text to `app.ai.orchestrator.handle_chat_message()`.
4. The Orchestrator forwards the text to Gemini via `client.route_intent()`.
5. Gemini processes the prompt (`intents_router.md`) and returns JSON:
   ```json
   {"action": "scan_insurance", "response": "Βεβαίως, κάνω αμέσως σάρωση για νέα ασφαλιστήρια...", "confidence": 0.98}
   ```
6. The Orchestrator translates `scan_insurance` to the deterministic skill identifier `insurance.scan`.
7. The Orchestrator calls the core logic `execute_action('insurance.scan', {}, db)`.
8. The result and the natural response ("Βεβαίως, κάνω αμέσως σάρωση...") are returned to the UI.
