"""Scholaria — Streamlit entrypoint: authentication, navigation, theme and sidebar.

Run (from the project root):
    streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from components import api_client as api
from components import auth
from components.ui import apply_base_style
from views import auth_page

ASSETS = Path(__file__).parent / "assets"
APP_NAME = "Scholaria"

st.set_page_config(page_title=APP_NAME, page_icon=str(ASSETS / "icon.svg"), layout="wide",
                   initial_sidebar_state="expanded")
apply_base_style()
ss = st.session_state

# --- Authentication ---------------------------------------------------------------
auth.restore_session()
if auth.current_user():
    auth.apply_theme()  # may rerun; must happen before the one-time cookie script is rendered
auth.flush_cookie_ops()

PAGE_FILES = ["research_chat", "literature", "data_analysis", "writing_assistant", "workspace", "settings"]

if not auth.current_user():
    # Every page URL shows the sign-in form, so deep links open the requested page after login.
    login_pages = [st.Page(auth_page.render, title="Sign in", icon=":material/login:", default=True)]
    login_pages += [st.Page(auth_page.render, title="Sign in", url_path=name) for name in PAGE_FILES]
    st.navigation(login_pages, position="hidden").run()
    st.stop()

theme = auth.active_theme()
# Deep link: /research_chat?chat=<id> opens that chat (the API enforces ownership).
if "chat" in st.query_params:
    try:
        ss["conversation_id"] = int(st.query_params["chat"])
    except ValueError:
        pass
    del st.query_params["chat"]
st.logo(str(ASSETS / f"logo-{theme}.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))

PAGES = {
    "dashboard": st.Page("views/dashboard.py", title="Home", icon=":material/home:", default=True),
    "chat": st.Page("views/research_chat.py", title="Research chat", icon=":material/forum:"),
    "literature": st.Page("views/literature.py", title="Literature", icon=":material/menu_book:"),
    "data": st.Page("views/data_analysis.py", title="Data analysis", icon=":material/monitoring:"),
    "writing": st.Page("views/writing_assistant.py", title="Academic writing", icon=":material/edit_note:"),
    "workspace": st.Page("views/workspace.py", title="Projects", icon=":material/folder_open:"),
    "settings": st.Page("views/settings.py", title="Settings", icon=":material/settings:"),
}
ss["_pages"] = PAGES

navigation = st.navigation({
    "": [PAGES["dashboard"]],
    "Workspace": [PAGES["chat"], PAGES["literature"], PAGES["data"], PAGES["writing"], PAGES["workspace"]],
    "Account": [PAGES["settings"]],
})


# --- Sidebar: chats with rename / delete -------------------------------------------
@st.dialog("Delete chat?")
def confirm_delete(conv: dict[str, Any]) -> None:
    st.write(f"**{conv['title']}** and all of its messages will be permanently deleted.")
    c1, c2 = st.columns(2)
    if c1.button("Cancel", width="stretch"):
        st.rerun()
    if c2.button("Delete", type="primary", width="stretch", icon=":material/delete:"):
        try:
            api.delete(f"/conversations/{conv['id']}")
        except api.ApiError as exc:
            st.error(exc.message)
            return
        if ss.get("conversation_id") == conv["id"]:
            ss["conversation_id"] = None
        st.toast("Chat deleted", icon=":material/delete:")
        st.rerun()


def rename_chat(conv_id: int, key: str) -> None:
    title = (ss.get(key) or "").strip()
    if not title:
        st.toast("A chat title cannot be empty.", icon=":material/warning:")
        return
    try:
        api.patch(f"/conversations/{conv_id}", json={"title": title[:300]})
        st.toast("Chat renamed", icon=":material/edit:")
    except api.ApiError as exc:
        st.toast(exc.message, icon=":material/error:")


def sidebar() -> None:
    with st.sidebar:
        if st.button("New chat", icon=":material/add_comment:", width="stretch", type="primary"):
            ss["conversation_id"] = None
            st.switch_page(PAGES["chat"])

        conversations = api.safe_get("/conversations", default=[], params={"limit": 50}) or []
        if conversations:
            query = st.text_input("Search chats", placeholder="Search chats…", label_visibility="collapsed",
                                  icon=":material/search:", key="chat_search")
            shown = [c for c in conversations if query.lower() in c["title"].lower()] if query else conversations
            st.caption("Recent chats")
            current = ss.get("conversation_id")
            for conv in shown[:30]:
                c1, c2 = st.columns([5, 1], gap="small", vertical_alignment="center")
                label = conv["title"] if len(conv["title"]) <= 30 else conv["title"][:29] + "…"
                if c1.button(label, key=f"conv_{conv['id']}", width="stretch",
                             type="tertiary" if conv["id"] != current else "secondary", help=conv["title"]):
                    ss["conversation_id"] = conv["id"]
                    st.switch_page(PAGES["chat"])
                with c2.popover("", icon=":material/more_horiz:", help="Rename or delete", type="tertiary"):
                    key = f"rename_{conv['id']}"
                    st.text_input("Rename chat", value=conv["title"], key=key, max_chars=300)
                    st.button("Save name", key=f"save_{conv['id']}", icon=":material/check:", width="stretch",
                              on_click=rename_chat, args=(conv["id"], key))
                    if st.button("Delete chat", key=f"del_{conv['id']}", icon=":material/delete:", width="stretch"):
                        confirm_delete(conv)
            if not shown:
                st.caption("No chats match your search.")
        else:
            st.caption("Your chats will appear here.")

        st.divider()
        user = auth.current_user() or {}
        initials = "".join(part[0] for part in user.get("full_name", "?").split()[:2]).upper() or "?"
        st.markdown(f"<div class='sch-user'><span class='sch-avatar'>{initials}</span>"
                    f"<span><b>{user.get('full_name', '')}</b><br><small>{user.get('email', '')}</small></span></div>",
                    unsafe_allow_html=True)
        dark = st.toggle("Dark mode", value=theme == "dark", key="dark_toggle")
        if dark != (theme == "dark"):
            auth.set_theme("dark" if dark else "light")
            st.rerun()
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            auth.logout()
            st.rerun()


try:
    sidebar()
    navigation.run()
except api.ApiError as exc:
    if exc.code == "unauthorized":
        auth.end_session()
        st.rerun()
    raise
