# Troubleshooting Scholaria

Logs: `storage/logs/backend.log` (app), `api.log` (uvicorn), `frontend.log` (Streamlit), `ollama.log`.

| Symptom | Cause | Fix |
|---|---|---|
| "Ollama not running" in the sidebar | Ollama server is not started | `ollama serve` or open the Ollama app; `start.sh` starts it if the CLI is installed |
| "Model … is not installed" | Model not pulled | `ollama pull qwen3:4b-instruct-2507-q4_K_M` (or pick an installed model in Settings) |
| "not enough free memory" / error 507 | Model cannot fit in RAM | Quit memory-heavy apps (browsers with many tabs, IDEs), lower context length to 2048–4096, set keep-alive shorter, or switch to `qwen3:1.7b` |
| Responses are slow | 4B model on CPU/GPU-shared memory | Use `qwen3:1.7b`, lower max response tokens, and use targeted extraction instead of whole-document processing |
| Chat answers ignore earlier messages | History budget exceeded | Older messages are dropped to fit the context; raise context length (more memory) or start a new conversation |
| "Port 8000/8501 is already in use" | Another program is using the port | Stop that program, or set `RA_API_PORT` / `RA_FRONTEND_PORT` in `.env` |
| "Backend API offline" | API not running | `./scripts/start.sh`; check `storage/logs/api.log` |
| PDF status `failed`: "No extractable text found" | Scanned (image-only) PDF | OCR is not supported in v1; use a text-based PDF |
| PDF is password-protected | Encrypted file | Remove the password before uploading |
| Upload rejected with 413 | File exceeds the limit | Raise the limit in Settings → Application |
| Upload rejected with 409 | Same file already uploaded | Use the existing document (its ID is in the message) |
| Summary says "partial coverage" | Targeted extraction uses retrieved passages | Turn on "Process the whole document" |
| "(page reference removed: not verifiable)" | The model cited a page not in the text | Expected safety behaviour; check the cited pages in Sources |
| Dataset columns not offered for correlation/regression | Column was read as text | Clean the column (e.g. remove "N/A" strings or thousands separators) and re-upload |
| `setup.sh`: Python 3.11 not found | No 3.11 interpreter | `brew install python@3.11` or `brew install uv` |
| Stale processes after a crash | Leftover PID files | `./scripts/stop.sh` cleans stale PID files safely |
| Reset all data | | `./scripts/stop.sh`, then delete `storage/research.db*` and the contents of `storage/uploads` and `storage/datasets`, then `make migrate` |
| Forgot your password | Accounts are local; there is no email reset | `.venv/bin/python scripts/manage.py reset-password you@example.com` |
| "Too many failed attempts" | Five wrong passwords in a row | Wait 60 seconds, then try again |
| Logged out after closing the browser | "Keep me signed in" was not ticked | Tick it at login to stay signed in for 30 days |
| Someone else's chats are missing | Each account only sees its own data | Log in with the account that created them |
| Data from before accounts existed | It belongs to the first account created | Create your account first on an existing installation |

