import json
import logging
import smtplib

import httpx2
import pytest

from digest import deliver as channels
from digest.deliver import DeliveryError, deliver, describe, send_email, send_wechat

KEY = "SCT123456TsecretSendKeyValue"
EMAIL_ENV = {
    "SMTP_HOST": "smtp.example.org",
    "SMTP_PORT": "587",
    "SMTP_USER": "me@example.org",
    "SMTP_PASS": "app-password",
    "MAIL_TO": "me@example.org",
}


def no_sleep(seconds: float) -> None:
    pass


# --- isolation --------------------------------------------------------------------------


def test_one_failing_channel_does_not_stop_the_other():
    called = []

    def broken() -> None:
        called.append("email")
        raise RuntimeError("smtp is down")

    results = deliver({"email": broken, "wechat": lambda: called.append("wechat")})
    assert called == ["email", "wechat"]
    assert results == {"email": False, "wechat": True}


def test_order_does_not_matter_for_isolation():
    called = []

    def broken() -> None:
        raise DeliveryError("refused")

    results = deliver({"wechat": broken, "email": lambda: called.append("email")})
    assert called == ["email"]
    assert results == {"wechat": False, "email": True}


# --- which channels are enabled -----------------------------------------------------------


def test_email_needs_all_four_settings():
    assert channels.email_enabled(EMAIL_ENV)
    assert not channels.email_enabled({})
    assert not channels.email_enabled(EMAIL_ENV | {"SMTP_PASS": ""})  # unset secret
    assert channels.email_enabled({k: v for k, v in EMAIL_ENV.items() if k != "SMTP_PORT"})


def test_wechat_needs_the_send_key():
    assert channels.wechat_enabled({"SERVERCHAN_SENDKEY": KEY})
    assert not channels.wechat_enabled({"SERVERCHAN_SENDKEY": "  "})
    assert not channels.wechat_enabled({})


# --- email ------------------------------------------------------------------------------


class FakeSMTP:
    """Records what smtplib.SMTP would have been asked to do."""

    log: list = []

    def __init__(self, host, port, timeout):
        FakeSMTP.log = [("connect", host, port, timeout)]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context):
        FakeSMTP.log.append(("starttls", context is not None))

    def login(self, user, password):
        FakeSMTP.log.append(("login", user, password))

    def send_message(self, message):
        FakeSMTP.log.append(("send", message))


def test_send_email_uses_starttls_then_login_then_send(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_email(EMAIL_ENV, "Subject line", "plain text", "<p>html</p>")

    connect, starttls, login, send = FakeSMTP.log
    assert connect == ("connect", "smtp.example.org", 587, channels.SMTP_TIMEOUT_SECONDS)
    assert starttls == ("starttls", True)
    assert login == ("login", "me@example.org", "app-password")
    message = send[1]
    assert message["Subject"] == "Subject line"
    assert message["From"] == "me@example.org"
    assert message["To"] == "me@example.org"
    kinds = [part.get_content_type() for part in message.iter_parts()]
    assert kinds == ["text/plain", "text/html"]


def test_smtp_port_defaults_to_587(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_email(EMAIL_ENV | {"SMTP_PORT": ""}, "s", "t", "h")
    assert FakeSMTP.log[0][2] == 587


# --- WeChat -----------------------------------------------------------------------------


def serverchan(status: int = 200, body: dict | str | None = None, seen: list | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json={"code": 0} if body is None else body)

    return httpx2.MockTransport(handler)


def test_send_wechat_posts_title_and_body():
    seen: list = []
    send_wechat(
        {"SERVERCHAN_SENDKEY": f" {KEY} "}, "Title", "Body", transport=serverchan(seen=seen)
    )
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"https://sctapi.ftqq.com/{KEY}.send"
    assert json.loads(request.content) == {"title": "Title", "desp": "Body"}


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"code": 40001, "message": "bad pushtoken"}),
        (403, {"error": "Forbidden"}),  # no "code" field at all
        (200, "<html>not json</html>"),
        (429, "slow down"),
    ],
)
def test_send_wechat_fails_unless_code_is_zero(status, body):
    with pytest.raises(DeliveryError) as raised:
        send_wechat({"SERVERCHAN_SENDKEY": KEY}, "T", "B", transport=serverchan(status, body))
    assert KEY not in str(raised.value)


def test_app_only_key_is_refused_before_any_request():
    seen: list = []
    with pytest.raises(DeliveryError, match="not WeChat"):
        send_wechat(
            {"SERVERCHAN_SENDKEY": "sctp123tabc"}, "T", "B", transport=serverchan(seen=seen)
        )
    assert seen == []


def test_send_wechat_retries_once_when_the_connection_fails():
    attempts = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx2.ConnectError("no route", request=request)
        return httpx2.Response(200, json={"code": 0})

    pauses: list = []
    send_wechat(
        {"SERVERCHAN_SENDKEY": KEY},
        "T",
        "B",
        transport=httpx2.MockTransport(handler),
        sleep=pauses.append,
    )
    assert len(attempts) == 2
    assert pauses == [channels.SERVERCHAN_RETRY_SECONDS]


def test_send_wechat_gives_up_after_the_retry_without_exposing_the_key():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(DeliveryError) as raised:
        send_wechat(
            {"SERVERCHAN_SENDKEY": KEY},
            "T",
            "B",
            transport=httpx2.MockTransport(handler),
            sleep=no_sleep,
        )
    assert "unreachable" in str(raised.value)
    assert KEY not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


# --- secrets stay out of the log --------------------------------------------------------------


def test_describe_never_repeats_foreign_exception_text():
    assert (
        describe(RuntimeError(f"failed for url https://sctapi.ftqq.com/{KEY}.send"))
        == "RuntimeError"
    )
    assert describe(DeliveryError("ServerChan refused (HTTP 400, code 40001)")).startswith(
        "ServerChan"
    )
    assert describe(smtplib.SMTPAuthenticationError(535, b"secret detail")) == (
        "SMTPAuthenticationError (SMTP code 535)"
    )


@pytest.mark.parametrize("outcome", ["ok", "refused", "timeout"])
def test_send_key_never_reaches_the_log(caplog, outcome):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if outcome == "timeout":
            raise httpx2.ReadTimeout(f"timed out reading {request.url}", request=request)
        return httpx2.Response(200, json={"code": 0 if outcome == "ok" else 40001, "pushid": "p1"})

    env = {"SERVERCHAN_SENDKEY": KEY}
    transport = httpx2.MockTransport(handler)
    # httpx2 logs the full request URL at INFO. Turn that on, as the Anthropic SDK does
    # when ANTHROPIC_LOG is set; send_wechat has to turn it off again by itself.
    caplog.set_level(logging.INFO, logger="httpx2")
    with caplog.at_level(logging.DEBUG):
        results = deliver({"wechat": lambda: send_wechat(env, "T", "B", transport=transport)})

    assert results == {"wechat": outcome == "ok"}
    assert caplog.records  # something was logged
    assert KEY not in caplog.text
    assert "p1" not in caplog.text  # the reply is not logged either


def test_httpx2_really_would_log_the_url():
    """Guards the test above: if httpx2 stops logging URLs, that test proves nothing."""
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("httpx2")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        transport = httpx2.MockTransport(lambda request: httpx2.Response(200))
        with httpx2.Client(transport=transport) as client:
            client.post("https://example.org/SECRET.send")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    assert "SECRET" in stream.getvalue()


def test_mail_to_accepts_commas_and_semicolons(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_email(
        EMAIL_ENV | {"MAIL_TO": "a@example.org; b@example.org ,c@example.org"}, "s", "t", "h"
    )
    assert FakeSMTP.log[-1][1]["To"] == "a@example.org, b@example.org, c@example.org"


def test_password_is_stripped_like_the_other_settings(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_email(EMAIL_ENV | {"SMTP_PASS": "app-password\n"}, "s", "t", "h")
    assert FakeSMTP.log[2] == ("login", "me@example.org", "app-password")


def test_reply_text_is_never_copied_into_the_error():
    body = {"code": "<script>arbitrary reply text</script>"}
    with pytest.raises(DeliveryError) as raised:
        send_wechat({"SERVERCHAN_SENDKEY": KEY}, "T", "B", transport=serverchan(200, body))
    assert "arbitrary" not in str(raised.value)
    assert "code missing" in str(raised.value)


def test_mail_to_accepts_spaces_and_newlines_too(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_email(EMAIL_ENV | {"MAIL_TO": "a@example.org b@example.org\nc@example.org"}, "s", "t", "h")
    assert FakeSMTP.log[-1][1]["To"] == "a@example.org, b@example.org, c@example.org"

    named = '"Doe, Jane" <jane@example.org>; Bob <bob@example.org>'
    send_email(EMAIL_ENV | {"MAIL_TO": named}, "s", "t", "h")
    assert "jane@example.org" in FakeSMTP.log[-1][1]["To"]
    assert "bob@example.org" in FakeSMTP.log[-1][1]["To"]
