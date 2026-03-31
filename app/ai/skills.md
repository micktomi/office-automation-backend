# AI Available Skills

The AI Orchestrator can trigger the following deterministic skills, exported by the `app.routers.agent.execute_action` function.

| Intent Name (from LLM) | Output Skill ID | Required Payload | Description |
|-------------------------|-----------------|------------------|-------------|
| `sync_inbox`            | `email.sync`    | None | Scans Gmail for new important emails and analyzes them. |
| `list_emails`           | `email.list`    | None | Returns a list of the latest emails from the database. |
| `generate_reply`        | `email.reply`   | `email_id` | Drafts a friendly response for a specific email. |
| `list_insurance_alerts` | `insurance.alerts` | `status` (optional), `days` (optional) | Gets upcoming insurance policy renewals within the shared window. |
| `scan_insurance`        | `insurance.scan`| None | Processes recently synced emails to find missing insurance renewals. |
| `list_tasks`            | `tasks.list`    | None | Lists outstanding todo tasks. |
| `create_task`           | `tasks.create`  | `title` | Creates a new task. |
| `complete_task`         | `tasks.complete`| `task_id` | Marks a task as complete. |

If the Orchestrator does not have a confidence level >0.6, it will fallback to the `chat` "skill", which does not execute any backend logic but simply uses Gemini to reply and ask for clarification.
