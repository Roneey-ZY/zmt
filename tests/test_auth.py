from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.authentication import (
    LoginConfig, LoginLimiter, make_config, valid_session,
)

APP = Path(__file__).resolve().parents[1] / "app.py"


def configure(monkeypatch):
    config = make_config("judge", "test-password-only-2026")
    for name, value in {
        "LOGIN_USERNAME": config.username, "LOGIN_SALT": config.salt.hex(),
        "LOGIN_PASSWORD_HASH": config.digest.hex(),
    }.items():
        monkeypatch.setenv(name, value)
    return config


def test_hash_config_and_expiration():
    config = make_config("judge", "test-password-only-2026")
    assert config.verify("judge", "test-password-only-2026")
    assert not config.verify("judge", "wrong")
    assert not config.verify("other", "test-password-only-2026")
    assert not config.verify("judge", "x" * 1025)
    session = {"revision": config.revision, "until": 100}
    assert valid_session(session, config, now=99)
    assert not valid_session(session, config, now=100)
    assert not valid_session(session, make_config("judge", "new-password-only"), now=99)
    with pytest.raises(ValueError):
        LoginConfig.load(lambda _: "")


def test_throttle_shared_across_attempts():
    config = make_config("judge", "test-password-only-2026")
    limiter = LoginLimiter()
    for _ in range(5):
        assert limiter.attempt(config, "unknown", "bad", now=10) == (False, 0)
    accepted, wait = limiter.attempt(config, "judge", "test-password-only-2026", now=11)
    assert not accepted and wait > 0
    assert limiter.attempt(config, "judge", "test-password-only-2026", now=71) == (True, 0)


def test_missing_config_fails_closed(monkeypatch):
    for name in ("LOGIN_USERNAME", "LOGIN_SALT", "LOGIN_PASSWORD_HASH"):
        monkeypatch.setenv(name, "")
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    assert "尚未配置完成" in app.error[0].value
    assert not app.get("file_uploader")
    assert not app.button


def test_login_audit_logout_and_revocation(monkeypatch):
    config = configure(monkeypatch)
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    assert [button.label for button in app.button] == ["登录"]
    assert not app.get("file_uploader")
    app.text_input[0].set_value("judge")
    app.text_input[1].set_value("wrong")
    app.button[0].click().run()
    assert "账号或密码错误" in app.error[0].value
    app.text_input[0].set_value("judge")
    app.text_input[1].set_value("test-password-only-2026")
    app.button[0].click().run(timeout=30)
    assert not app.exception
    next(radio for radio in app.radio if radio.label == "执行模式").set_value("rules").run()
    next(button for button in app.button if button.label == "一键加载演示案例").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["record"][0].result.counts == {
        "difference": 3, "contradiction": 1, "review": 0,
    }
    assert app.session_state["record"][0].report
    next(button for button in app.button if button.label == "退出登录").click().run()
    assert not app.exception
    assert "record" not in app.session_state
    assert [button.label for button in app.button] == ["登录"]
    # Credential rotation revokes an existing session before showing any workbench.
    app.session_state["_login"] = {"revision": config.revision, "until": 1e20}
    configure(monkeypatch)
    app.run()
    assert [button.label for button in app.button] == ["登录"]
