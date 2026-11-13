# Scholaria REST API

Base URL: `http://127.0.0.1:8000/api`. Interactive Swagger UI: <http://127.0.0.1:8000/docs>.

## Authentication

Every endpoint except `GET /health`, `POST /auth/signup` and `POST /auth/login` requires a bearer token:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"YourPassw0rd","remember":false}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/projects
```

Data is scoped to the logged-in account: projects, chats, documents, datasets, notes, citations and
analyses of other accounts are reported as `404 not_found`. Assistants (system prompts) and AI
settings are shared by all accounts on the machine.

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | `{email, full_name, password}`. Password: 8+ characters with a letter and a number |
| POST | `/auth/login` | `{email, password, remember}` → `{token, expires_at, user}` (12 h, or 30 days with `remember`) |
| POST | `/auth/logout` | Revoke the current token |
| GET / PATCH | `/auth/me` | Current profile / update `full_name`, `theme` (`light` or `dark`) |
| POST | `/auth/change-password` | `{current_password, new_password}` |

All errors use one structure:

```json
{"error": {"code": "ollama_unavailable", "message": "Cannot connect to Ollama...", "details": null}}
```

| Status | Code | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing, invalid or expired login token |
| 429 | `too_many_attempts` | Five failed logins for one email; wait 60 seconds |
| 404 | `not_found` / `model_not_found` | Missing record, or model not installed in Ollama |
| 409 | `conflict` | Duplicate upload (`details.document_id` gives the existing one) |
| 413 | `payload_too_large` | Upload exceeds the configured limit |
| 422 | `validation_failed` | Invalid input |
| 502 | `ollama_error` | Ollama returned an error |
| 503 | `ollama_unavailable` | Ollama not running |
| 507 | `insufficient_memory` | The model could not load or run because of memory |

## Health and system

| Method | Path | Description |
|---|---|---|
| GET | `/health` | API + DB health |
| GET | `/system/status` | App, database, storage, Ollama and model availability |
| POST | `/system/test-ollama` | Test the Ollama connection |
| GET / PUT | `/settings` | Read / update runtime settings (partial update) |
| GET | `/settings/info` | Non-sensitive app information |
| GET | `/activity?limit=10` | Recent conversations, documents, datasets and notes |

## Models and assistants

| Method | Path | Description |
|---|---|---|
| GET | `/models` | Installed Ollama models (discovered at runtime) |
| GET | `/models/status` | Connection, default/fallback availability, loaded models |
| POST | `/models/select` | `{"model": "qwen3:1.7b"}`: sets the default model (must be installed) |
| GET / POST | `/assistants` | List / create assistants (system prompts) |
| PATCH / DELETE | `/assistants/{id}` | Update (incl. `is_default`) / delete a non-default assistant |

## Conversations and chat

| Method | Path | Description |
|---|---|---|
| POST | `/conversations` | Create `{title?, project_id?, assistant_type?, assistant_id?, model_name?}` |
| GET | `/conversations?project_id=&assistant_type=&limit=&offset=` | List (most recent first) |
| GET | `/conversations/{id}` | Conversation with all messages (also opened in the UI via `/research_chat?chat=<id>`) |
| PATCH | `/conversations/{id}` | Rename or link to a project |
| DELETE | `/conversations/{id}` | Delete with its messages |
| DELETE | `/conversations/{id}/messages` | Clear the messages |
| POST | `/chat` | Non-streaming chat; returns the user and assistant messages |
| POST | `/chat/stream` | Streaming chat (SSE) |
| POST | `/chat/{id}/regenerate` | Regenerate the last reply (SSE) |
| POST | `/chat/{id}/stop` | Stop an in-flight generation |

Chat request body:

```json
{"message": "…", "conversation_id": null, "project_id": null, "assistant_id": null,
 "model": null, "temperature": 0.6, "context_length": 4096, "max_tokens": 1024,
 "document_ids": [1, 2]}
```

SSE events: `meta` (conversation_id, model, history stats) → `token` (`{"content": "…"}`)* →
`done` (`message_id`, `status`, `sources`, token counts) or `error` (`code`, `message`,
`partial_message_id`). The reply is saved even when the client disconnects (`interrupted`), is
stopped (`stopped`) or fails mid-stream (`error`).

## Documents

| Method | Path | Description |
|---|---|---|
| POST | `/documents/upload` | multipart `file` (+ `project_id`, `process`); PDF/DOCX/TXT |
| GET | `/documents?project_id=` | List |
| GET | `/documents/tasks` | Available extraction tasks |
| GET / PATCH / DELETE | `/documents/{id}` | Get / update (title, project) / delete (file + chunks) |
| POST | `/documents/{id}/process` | (Re-)extract, chunk and index |
| GET | `/documents/{id}/chunks?page=&limit=&offset=` | Extracted chunks |
| POST | `/documents/{id}/ask` | `{question, top_k?, model?, additional_document_ids?}` → grounded answer + sources |
| POST | `/documents/{id}/summarize` | `{task, full_scan?, model?}`; task ∈ summary, objectives, research_questions, theoretical_framework, methodology, sample, variables, findings, limitations, future_research |

## Research workspace

| Method | Path | Description |
|---|---|---|
| POST / GET | `/projects` | Create / list (with item counts) |
| GET / PATCH / DELETE | `/projects/{id}` | Get / update / delete (notes and citations deleted; other items unlinked) |
| GET / POST | `/projects/{id}/notes` | List / create notes |
| GET / PATCH / DELETE | `/notes/{id}` | Note CRUD |
| GET / POST | `/projects/{id}/citations` | List / create bibliography entries (DOI format validated) |
| GET / PATCH / DELETE | `/citations/{id}` | Citation CRUD |
| GET | `/projects/{id}/citations/export` | BibTeX export |

## Datasets

| Method | Path | Description |
|---|---|---|
| POST | `/datasets/upload` | multipart `file` (+ `project_id`); CSV/XLSX |
| GET | `/datasets?project_id=` | List |
| GET / PATCH / DELETE | `/datasets/{id}` | Get / link to a project / delete |
| GET | `/datasets/{id}/preview?rows=50` | Shape, column types, missing, unique, duplicates, first rows |
| POST | `/datasets/{id}/analyze` | Run a predefined analysis (below) |
| GET | `/datasets/{id}/analyses` | Saved runs |
| DELETE | `/datasets/{id}/analyses/{run_id}` | Delete a run |
| POST | `/datasets/{id}/analyses/{run_id}/interpret` | LLM explanation of stored results (no new numbers) |
| GET | `/datasets/{id}/export?format=xlsx\|csv\|json&analysis_id=` | Export results |

`analysis_type` ∈ `overview, descriptive, missing, duplicates, frequencies, correlation,
regression, histogram, scatter`, with `columns`, `dependent`, `independents`,
`method` (pearson/spearman/kendall), `bins`, `top_n`, `x` and `y` as needed. Every saved result
records the dataset name, analysis type, variables, n, missing-data handling, assumptions and limitations.

## Writing

| Method | Path | Description |
|---|---|---|
| GET | `/writing/tasks` | Available writing workflows |
| POST | `/writing/generate` | SSE stream. `{task, text, context?, project_id?, include_project_context?, include_bibliography?, output_format: markdown\|latex\|plain, model?, temperature?, max_tokens?}` |
