"""Send the digest by email and by WeChat. The two channels are independent.

Secrets come from environment variables and are never logged. That includes exception
text: some libraries put the request URL in their error messages, and the ServerChan URL
contains the send key.
"""

import logging
import re
import smtplib
import ssl
import time
from collections.abc import Callable, Mapping
from email.message import EmailMessage

import httpx2

log = logging.getLogger(__name__)

# httpx2 logs one line per request at INFO level. Keep the log quiet. (send_wechat sets
# this again, because for that request it is a matter of secrecy, not of tidiness.)
logging.getLogger("httpx2").setLevel(logging.WARNING)

SMTP_TIMEOUT_SECONDS = 30
EMAIL_VARS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO")
SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"
SERVERCHAN_TIMEOUT = httpx2.Timeout(20.0, connect=5.0)
SERVERCHAN_RETRY_SECONDS = 10


class DeliveryError(Exception):
    """A channel failed. The message is written here, so it is safe to log."""


def email_enabled(env: Mapping[str, str]) -> bool:
    """Email is on when all four settings are present. Unset GitHub secrets arrive as
    empty strings, so empty counts as missing."""
    missing = [name for name in EMAIL_VARS if not env.get(name, "").strip()]
    if missing and len(missing) < len(EMAIL_VARS):
        log.warning("email is disabled; missing %s", ", ".join(missing))
    return not missing


def wechat_enabled(env: Mapping[str, str]) -> bool:
    return bool(env.get("SERVERCHAN_SENDKEY", "").strip())


def send_email(env: Mapping[str, str], subject: str, text: str, html: str) -> None:
    """One message with a plain-text part and an HTML part, sent over STARTTLS."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = env.get("MAIL_FROM", "").strip() or env["SMTP_USER"].strip()
    # Accept addresses separated by commas, semicolons or spaces; the header itself
    # needs commas. (With "Name <address>" forms, only commas and semicolons separate.)
    separators = "[;,]" if "<" in env["MAIL_TO"] else r"[;,\s]+"
    recipients = [part.strip() for part in re.split(separators, env["MAIL_TO"]) if part.strip()]
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    port = int(env.get("SMTP_PORT", "").strip() or 587)
    with smtplib.SMTP(env["SMTP_HOST"].strip(), port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(env["SMTP_USER"].strip(), env["SMTP_PASS"].strip())
        smtp.send_message(message)


def send_wechat(
    env: Mapping[str, str],
    title: str,
    body: str,
    *,
    transport: httpx2.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Push through ServerChan Turbo, which forwards to a WeChat service account."""
    # httpx2 logs every request URL at INFO level, and this URL contains the key. Silence
    # that logger here, at the moment of sending, so that nothing done earlier (the
    # Anthropic SDK raises its level when ANTHROPIC_LOG is set) can undo it.
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    key = env["SERVERCHAN_SENDKEY"].strip()
    if key.lower().startswith("sctp"):
        # Keys starting with "sctp" belong to a different ServerChan product, which
        # delivers to its own phone app and not to WeChat. Refuse them early.
        raise DeliveryError("the send key is for the ServerChan app, which is not WeChat")

    url = SERVERCHAN_URL.format(key=key)
    payload = {"title": title, "desp": body}
    with httpx2.Client(timeout=SERVERCHAN_TIMEOUT, transport=transport) as client:
        try:
            response = client.post(url, json=payload)
        except (httpx2.ConnectError, httpx2.ConnectTimeout):
            # Nothing was sent, so one retry cannot produce a duplicate message.
            sleep(SERVERCHAN_RETRY_SECONDS)
            try:
                response = client.post(url, json=payload)
            except httpx2.TransportError as exc:
                # "from None" drops the original exception, whose request URL holds the key.
                raise DeliveryError(f"ServerChan unreachable ({type(exc).__name__})") from None

    # Not raise_for_status(): its error message would contain the URL. Do not log the
    # reply either: on success it carries ids that open the message's detail page.
    try:
        reply = response.json()
    except ValueError:
        reply = None
    code = reply.get("code") if isinstance(reply, dict) else None
    if code != 0:
        shown = code if isinstance(code, int) else "missing"  # never echo arbitrary reply text
        raise DeliveryError(f"ServerChan refused (HTTP {response.status_code}, code {shown})")


def describe(exc: Exception) -> str:
    """What may be logged about a failure: our own messages, otherwise only the type."""
    if isinstance(exc, DeliveryError):
        return str(exc)
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"{type(exc).__name__} (SMTP code {exc.smtp_code})"
    return type(exc).__name__


def deliver(channels: dict[str, Callable[[], None]]) -> dict[str, bool]:
    """Run every channel, even if an earlier one fails. Returns {channel name: succeeded}."""
    results = {}
    for name, send in channels.items():
        try:
            send()
        except Exception as exc:  # noqa: BLE001 - one channel must not take down the other
            log.error("%s delivery failed: %s", name, describe(exc))
            results[name] = False
        else:
            log.info("%s delivery ok", name)
            results[name] = True
    return results
