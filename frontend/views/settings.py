"""Module F — Settings, assistants (system prompts) and system status."""

from __future__ import annotations

import streamlit as st

from components import api_client as api
from components import auth
from components.ui import call, fmt_size

st.title("Settings")
st.markdown("<div class='sch-subtitle'>Your account, the local AI engine and application preferences.</div>",
            unsafe_allow_html=True)

settings = call(api.get, "/settings")
if settings is None:
    st.stop()
info = api.safe_get("/settings/info", default={}) or {}
models_data = api.safe_get("/models")
installed = [m["name"] for m in (models_data or {}).get("models", [])]
assistants = api.safe_get("/assistants", default=[]) or []

tab_account, tab_ai, tab_app, tab_assistants, tab_status = st.tabs(
    ["Account", "AI model", "Application", "Assistants", "System status"])

# --- Account ------------------------------------------------------------------------
with tab_account:
    user = auth.current_user() or {}
    with st.form("profile"):
        st.markdown("**Profile**")
        full_name = st.text_input("Full name", value=user.get("full_name", ""))
        st.text_input("Email", value=user.get("email", ""), disabled=True, help="Your sign-in email cannot be changed.")
        theme_choice = st.radio("Appearance", ["light", "dark"], index=0 if auth.active_theme() == "light" else 1,
                                format_func=lambda t: "Light" if t == "light" else "Dark", horizontal=True)
        if st.form_submit_button("Save profile", type="primary"):
            updated = call(api.patch, "/auth/me", json={"full_name": full_name.strip() or user.get("full_name"),
                                                        "theme": theme_choice})
            if updated:
                st.session_state["auth_user"] = updated
                st.toast("Profile saved", icon=":material/check:")
                st.rerun()
    with st.form("password", clear_on_submit=True):
        st.markdown("**Change password**")
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password", help="At least 8 characters, including a letter and a number.")
        confirm = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Change password"):
            if new != confirm:
                st.error("The new passwords do not match.")
            elif call(api.post, "/auth/change-password", json={"current_password": current, "new_password": new}):
                st.success("Password changed.")
    st.caption(f"Member since {user.get('created_at', '')[:10]}. Accounts and data are stored only on this computer.")

# --- AI settings ----------------------------------------------------------------
with tab_ai:
    if not installed:
        st.warning("No installed models found (Ollama not running or no models pulled). You can still type a "
                   "model name; install it with `ollama pull <model>`.")
    with st.form("ai_settings"):
        ollama_url = st.text_input("Ollama server URL", settings["ollama_url"],
                                   help="Only a local server (127.0.0.1 / localhost) is accepted.")
        if installed:
            default_model = st.selectbox("Default model", installed,
                                         index=installed.index(settings["default_model"]) if settings["default_model"] in installed else 0)
            fallback_model = st.selectbox("Fallback model (used if the default is not installed)", installed,
                                          index=installed.index(settings["fallback_model"]) if settings["fallback_model"] in installed else 0)
        else:
            default_model = st.text_input("Default model", settings["default_model"])
            fallback_model = st.text_input("Fallback model", settings["fallback_model"])
        c1, c2 = st.columns(2)
        temperature = c1.slider("Temperature", 0.0, 1.5, float(settings["temperature"]), 0.05)
        context_length = c2.select_slider("Context length (tokens)", [2048, 3072, 4096, 6144, 8192],
                                          value=settings["context_length"] if settings["context_length"] in [2048, 3072, 4096, 6144, 8192] else 4096,
                                          help="4096 is recommended for 8 GB unified memory.")
        c3, c4 = st.columns(2)
        max_tokens = c3.select_slider("Maximum response tokens", [256, 512, 768, 1024, 1536, 2048],
                                      value=settings["max_tokens"] if settings["max_tokens"] in [256, 512, 768, 1024, 1536, 2048] else 1024)
        keep_alive = c4.selectbox("Model keep-alive", ["30s", "1m", "2m", "5m", "10m", "0"],
                                  index=["30s", "1m", "2m", "5m", "10m", "0"].index(settings["keep_alive"]) if settings["keep_alive"] in ["30s", "1m", "2m", "5m", "10m", "0"] else 2,
                                  help="How long the model stays in memory after a request. Shorter frees RAM sooner.")
        c5, c6 = st.columns(2)
        history = c5.slider("Conversation messages sent as history", 2, 40, int(settings["history_max_messages"]))
        top_k = c6.slider("Passages retrieved per document question", 1, 12, int(settings["retrieval_top_k"]))
        if context_length > 4096:
            st.caption(":orange[Contexts above 4096 tokens increase memory use and may fail on 8 GB machines.]")
        if st.form_submit_button("Save AI settings", type="primary"):
            if call(api.put, "/settings", json={
                    "ollama_url": ollama_url, "default_model": default_model, "fallback_model": fallback_model,
                    "temperature": temperature, "context_length": context_length, "max_tokens": max_tokens,
                    "keep_alive": keep_alive, "history_max_messages": history, "retrieval_top_k": top_k}):
                st.cache_data.clear()
                st.success("AI settings saved.")
    if st.button("Test Ollama connection", icon=":material/network_check:"):
        result = call(api.post, "/system/test-ollama")
        if result and result.get("connected"):
            st.success(f"Connected to Ollama {result.get('version')} · {result.get('installed_models')} model(s) installed · "
                       f"default model {'available' if result.get('default_model_available') else 'NOT installed'}.")
        elif result:
            st.error(result.get("error", "Not connected"))

# --- Application settings ----------------------------------------------------------
with tab_app:
    with st.form("app_settings"):
        c1, c2 = st.columns(2)
        max_upload = c1.number_input("Max document upload (MB)", 1, 500, int(settings["max_upload_mb"]))
        max_dataset = c2.number_input("Max dataset upload (MB)", 1, 500, int(settings["max_dataset_mb"]))
        names = {a["name"]: a["id"] for a in assistants}
        current = next((a["name"] for a in assistants if a["id"] == settings.get("default_assistant_id")),
                       next((a["name"] for a in assistants if a["is_default"]), None))
        default_assistant = st.selectbox("Default assistant", list(names),
                                         index=list(names).index(current) if current in names else 0) if names else None
        if st.form_submit_button("Save application settings", type="primary"):
            if call(api.put, "/settings", json={
                    "max_upload_mb": int(max_upload), "max_dataset_mb": int(max_dataset),
                    "default_assistant_id": names.get(default_assistant) if default_assistant else None}):
                st.success("Application settings saved.")
    st.markdown("**Application information**")
    st.markdown(
        f"- Version: **{info.get('version', '—')}**\n"
        f"- Database: **{info.get('database_engine', 'SQLite')}** (`{info.get('database_file', '')}`)\n"
        f"- Storage folder: `{info.get('storage_folder', 'storage/')}` (inside the project folder)\n"
        f"- Concurrent model requests: **{info.get('max_concurrent_requests', 1)}**\n"
        "- Network: API and UI bound to **127.0.0.1** only; no telemetry."
    )

# --- Assistants ------------------------------------------------------------------------
with tab_assistants:
    st.caption("Assistants are reusable system prompts selectable in Research chat.")
    for a in assistants:
        with st.expander(f"{a['name']}{' · default' if a['is_default'] else ''}"):
            with st.form(f"assistant_{a['id']}"):
                name = st.text_input("Name", a["name"])
                description = st.text_input("Description", a.get("description") or "")
                prompt = st.text_area("System prompt", a["system_prompt"], height=180)
                c1, c2, c3 = st.columns(3)
                save = c1.form_submit_button("Save")
                make_default = c2.form_submit_button("Make default", disabled=a["is_default"])
                remove = c3.form_submit_button("Delete", disabled=a["is_default"])
            if save:
                call(api.patch, f"/assistants/{a['id']}", json={"name": name, "description": description,
                                                                "system_prompt": prompt})
                st.cache_data.clear()
                st.rerun()
            if make_default:
                call(api.patch, f"/assistants/{a['id']}", json={"is_default": True})
                st.cache_data.clear()
                st.rerun()
            if remove:
                call(api.delete, f"/assistants/{a['id']}")
                st.cache_data.clear()
                st.rerun()
    with st.expander("Create assistant", icon=":material/add:"):
        with st.form("new_assistant", clear_on_submit=True):
            name = st.text_input("Name")
            description = st.text_input("Description")
            prompt = st.text_area("System prompt", height=150,
                                  value="You are an academic research assistant. Never invent references, data or "
                                        "page numbers; say when evidence is missing.")
            if st.form_submit_button("Create", type="primary") and name.strip():
                call(api.post, "/assistants", json={"name": name.strip(), "description": description or None,
                                                    "system_prompt": prompt})
                st.cache_data.clear()
                st.rerun()

# --- Status -----------------------------------------------------------------------------
with tab_status:
    if st.button("Refresh", icon=":material/refresh:"):
        st.rerun()
    status = call(api.get, "/system/status")
    if status:
        def row(label: str, ok: bool, detail: str = "") -> None:
            st.markdown(f"{':green[●]' if ok else ':red[●]'} **{label}** {detail}")

        o = status["ollama"]
        row("FastAPI backend", True, f"— v{status['app']['version']}")
        row("Ollama connection", o.get("connected", False),
            f"— version {o.get('version')}" if o.get("connected") else f"— {o.get('error', '')}")
        row("Default model available", bool(o.get("default_model_available")), f"— `{o['default_model']}`")
        row("Fallback model available", bool(o.get("fallback_model_available")), f"— `{o['fallback_model']}`")
        loaded = o.get("loaded_models") or []
        st.caption("Models currently loaded in memory: " +
                   (", ".join(f"{m['name']} ({fmt_size(m.get('size_bytes'))})" for m in loaded) or "none"))
        db = status["database"]
        row("Database", db.get("ok", False), f"— SQLite, {fmt_size(db.get('size_bytes'))}, "
                                             f"full-text search {'on' if db.get('fts5') else 'off (fallback)'}")
        stg = status["storage"]
        row("Storage", stg.get("ok", False), f"— {stg.get('free_gb', '?')} GB free")
