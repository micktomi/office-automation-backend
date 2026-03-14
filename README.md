# office-automation-backend

Deterministic FastAPI backend for insurance office automation.

## Features
- Policy normalization and validation
- Renewal and reminder cycle engine
- Email reminder endpoints
- Insurance alerts and Excel/CSV import
- Task management endpoints
- Reports and Google Calendar integration

## Run
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 3001
```

## Frontend API base URL
Set frontend `NEXT_PUBLIC_API_URL` to:

```text
http://localhost:3001
```
# office-agent-backend
