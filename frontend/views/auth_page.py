"""Sign-in and account creation screen."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from components import api_client as api
from components.auth import active_theme, complete_login

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def _login(email: str, password: str, remember: bool) -> bool:
    try:
        data = api.post("/auth/login", json={"email": email, "password": password, "remember": remember})
    except api.ApiError as exc:
        st.error(exc.message, icon=":material/lock:")
        return False
    complete_login(data["token"], data["user"], remember)
    return True


def render() -> None:
    _, center, _ = st.columns([1, 1.25, 1])
    with center:
        st.image(str(ASSETS / f"logo-{active_theme()}.svg"), width=260)
        st.markdown("#### Your private, local AI research assistant")
        st.caption("Papers, data and drafts stay on this computer. AI runs locally with Ollama.")

        mode = st.segmented_control("Mode", ["Log in", "Create account"], default="Log in",
                                    label_visibility="collapsed", key="auth_mode", width="stretch")

        if mode == "Create account":
            with st.form("signup", border=True):
                name = st.text_input("Full name", placeholder="Dr. Jane Researcher")
                email = st.text_input("Email", placeholder="jane@university.edu")
                password = st.text_input("Password", type="password",
                                         help="At least 8 characters, including a letter and a number.")
                confirm = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create account", type="primary", width="stretch")
            if submitted:
                if password != confirm:
                    st.error("The passwords do not match.")
                else:
                    try:
                        api.post("/auth/signup", json={"email": email, "full_name": name, "password": password})
                    except api.ApiError as exc:
                        st.error(exc.message)
                    else:
                        if _login(email, password, remember=False):
                            st.rerun()
        else:
            with st.form("login", border=True):
                email = st.text_input("Email", placeholder="jane@university.edu")
                password = st.text_input("Password", type="password")
                remember = st.checkbox("Keep me signed in for 30 days")
                submitted = st.form_submit_button("Log in", type="primary", width="stretch")
            if submitted:
                if not email or not password:
                    st.error("Enter your email and password.")
                elif _login(email, password, remember):
                    st.rerun()
            st.caption("New here? Choose **Create account** above. Accounts are stored only on this computer.")
