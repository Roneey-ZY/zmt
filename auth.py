"""Gate the entire workbench before any documents or model actions are exposed."""
import os
from time import monotonic

import streamlit as st

from core.authentication import LoginConfig, LoginLimiter, SESSION_SECONDS, valid_session


def setting(name):
    # Environment variables support Tencent CloudBase; Secrets supports Community Cloud.
    if name in os.environ:
        return os.environ[name]
    try:
        return str(st.secrets.get(name, ""))
    except FileNotFoundError:
        return ""


@st.cache_resource(show_spinner=False)
def login_limiter(revision):
    return LoginLimiter()


def require_login():
    try:
        config = LoginConfig.load(setting)
    except ValueError:
        st.title("贸审通 TradeCheck Agent")
        st.error("登录服务尚未配置完成，请联系管理员。")
        st.stop()

    session = st.session_state.get("_login", {})
    if valid_session(session, config):
        with st.sidebar:
            st.caption(f"当前账号：{config.username}")
            if st.button("退出登录", key="logout"):
                st.session_state.clear()
                st.rerun()
        return
    if session:
        st.session_state.clear()

    st.title("贸审通 TradeCheck Agent")
    st.caption("跨境贸易单证智能审核 · 请登录后进入工作台")
    with st.form("login_form", clear_on_submit=True):
        username = st.text_input("账号", max_chars=128)
        password = st.text_input("密码", type="password", max_chars=1024)
        submitted = st.form_submit_button("登录", type="primary")
    if submitted:
        accepted, wait = login_limiter(config.revision).attempt(config, username, password)
        if accepted:
            st.session_state.clear()
            st.session_state["_login"] = {
                "revision": config.revision, "until": monotonic() + SESSION_SECONDS,
            }
            st.rerun()
        elif wait:
            st.error(f"登录尝试过于频繁，请 {wait} 秒后重试。")
        else:
            st.error("账号或密码错误。连续失败 5 次后将暂停登录 60 秒。")
    st.caption("登录有效期为 8 小时。退出登录会清除当前会话中的单证与审核结果。")
    st.stop()
