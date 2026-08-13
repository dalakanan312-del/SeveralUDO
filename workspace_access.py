from __future__ import annotations

import hashlib
import hmac
import os
import secrets

import storage
import cloud_schema

COOKIE_NAME = "trusted_workspace"
SESSION_DAYS = 90
_AUTH_SETUP = False


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


def _token_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _cookie_manager(st):
    from streamlit_cookies_manager import EncryptedCookieManager
    password=(os.environ.get("AUTH_COOKIE_PASSWORD") or os.environ.get("OWNER_ACCESS_KEY") or "").strip()
    if not password:
        # DATABASE_URL is already a private Railway secret and is stable across
        # restarts. Hash it before giving it to the cookie component.
        password=hashlib.sha256((storage.load_config().get("pooled_url") or "decades-local").encode()).hexdigest()
    cookies=EncryptedCookieManager(prefix="severaludo/v1/",password=password)
    if not cookies.ready():
        st.stop()
    return cookies


def _create_session(workspace):
    token=secrets.token_urlsafe(32)
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.decades_sessions WHERE expires_at<=now()")
            cursor.execute(
                "INSERT INTO public.decades_sessions(token_hash,workspace_hash,expires_at) VALUES(%s,%s,now()+(%s * interval '1 day'))",
                (_token_hash(token),workspace,SESSION_DAYS),
            )
        connection.commit()
    return token


def _workspace_for_session(token):
    if not token:
        return None
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT workspace_hash FROM public.decades_sessions WHERE token_hash=%s AND expires_at>now()",
                (_token_hash(token),),
            )
            row=cursor.fetchone()
            if row:
                cursor.execute(
                    "UPDATE public.decades_sessions SET last_seen_at=now() WHERE token_hash=%s AND last_seen_at<now()-interval '1 day'",
                    (_token_hash(token),),
                )
        connection.commit()
    return row[0] if row else None


def link_email(workspace,email):
    normalized=(email or "").strip().casefold()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("Enter a valid email address.")
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT workspace_hash FROM public.decades_identities WHERE email=%s",(normalized,))
            existing=cursor.fetchone()
            if existing and not hmac.compare_digest(existing[0],workspace):
                raise ValueError("That email is already connected to another workspace.")
            cursor.execute(
                "INSERT INTO public.decades_identities(email,workspace_hash) VALUES(%s,%s) "
                "ON CONFLICT(email) DO UPDATE SET workspace_hash=excluded.workspace_hash",
                (normalized,workspace),
            )
        connection.commit()
    return normalized


def linked_email(workspace):
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT email FROM public.decades_identities WHERE workspace_hash=%s ORDER BY created_at LIMIT 1",(workspace,))
            row=cursor.fetchone()
    return row[0] if row else None


def render_gate(st):
    # Authentication tables live in the public registry and must exist before
    # a workspace has been selected.
    global _AUTH_SETUP
    if not _AUTH_SETUP:
        with storage.raw_connect(use_direct=True) as connection:
            cloud_schema.create_registry(connection)
        _AUTH_SETUP=True
    cookies=_cookie_manager(st)
    if st.session_state.get("workspace_id"):
        return st.session_state["workspace_id"]

    restored=_workspace_for_session(cookies.get(COOKIE_NAME))
    if restored:
        st.session_state["workspace_id"]=restored
        return restored

    st.title("Decades Tracker")
    st.subheader("Open your private workspace")
    st.write("Each workspace has its own saves. Other players cannot browse or open them.")
    with st.form("workspace_access_form"):
        code=st.text_input("Private workspace code",type="password")
        remember=st.checkbox(f"Keep me signed in on this device for {SESSION_DAYS} days",value=True)
        submitted=st.form_submit_button("Open workspace",type="primary")
    if submitted:
        try:
            workspace=workspace_id(code)
            st.session_state["workspace_id"]=workspace
            if remember:
                cookies[COOKIE_NAME]=_create_session(workspace)
                cookies.save()
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    st.caption("Your code remains a recovery key. Once inside, connect an email to prepare this workspace for Google sign-in.")
    return None


def render_account_settings(st,workspace):
    with st.expander("Account & sign-in"):
        cache_key=f"linked_email_{workspace}"
        if cache_key not in st.session_state:
            st.session_state[cache_key]=linked_email(workspace)
        email=st.session_state[cache_key]
        if email:
            st.success(f"Connected email: {email}")
            if os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"):
                st.caption("Google sign-in is configured for this deployment.")
            else:
                st.caption("This workspace is ready for Google sign-in once the app owner adds Google OAuth credentials.")
        else:
            st.caption("Connect an email now. Your private workspace code remains available as a recovery method.")
            with st.form("link_workspace_email"):
                new_email=st.text_input("Email address")
                submit=st.form_submit_button("Connect email")
            if submit:
                try:
                    linked=link_email(workspace,new_email)
                    st.session_state[cache_key]=linked
                    st.success(f"Connected {linked}.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))


def sign_out(st):
    cookies=_cookie_manager(st)
    token=cookies.get(COOKIE_NAME)
    if token:
        with storage.raw_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.decades_sessions WHERE token_hash=%s",(_token_hash(token),))
            connection.commit()
        del cookies[COOKIE_NAME]
        cookies.save()
    for key in ("workspace_id","active_save_id","sidebar_save_selector"):
        st.session_state.pop(key,None)
    st.rerun()
