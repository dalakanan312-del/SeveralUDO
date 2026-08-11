from __future__ import annotations

import hashlib
import hmac
import os


def workspace_id(access_code):
    code = (access_code or "").strip()
    if len(code) < 12:
        raise ValueError("Workspace codes must be at least 12 characters long.")
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def owner_workspace_id():
    code = (os.environ.get("OWNER_ACCESS_KEY") or "").strip()
    return workspace_id(code) if code else None


def is_owner(workspace):
    owner = owner_workspace_id()
    return bool(owner and workspace and hmac.compare_digest(owner, workspace))


def render_gate(st):
    if st.session_state.get("workspace_id"):
        return st.session_state["workspace_id"]

    st.title("Decades Tracker")
    st.subheader("Open your private workspace")
    st.write("Each workspace has its own saves. Other players cannot browse or open them.")
    with st.form("workspace_access_form"):
        code = st.text_input("Private workspace code", type="password")
        submitted = st.form_submit_button("Open workspace", type="primary")
    if submitted:
        try:
            st.session_state["workspace_id"] = workspace_id(code)
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    st.caption("New player? Enter a unique code of 12+ characters. Keep it safe—you will use the same code to return to your saves.")
    return None


def sign_out(st):
    for key in ("workspace_id", "active_save_id", "sidebar_save_selector"):
        st.session_state.pop(key, None)
    st.rerun()
