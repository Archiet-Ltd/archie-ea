"""The text pass of the one scrub: a secret written inside ordinary words, a URL or a form is redacted, and the
words around it, and ordinary architecture text, come through unchanged.

Known limits (not decoded, so not asserted): base64 or hex encodings of a key, a key split across two fields,
a URL-encoded JSON blob, and a bare ``?key=`` query value.
"""

import base64
import json
import random

import pytest

from app.modules.ai_chat.services.llm_service_impl import _luhn_ok, _scrub_prompt, scrub_chat_messages

try:
    from app.modules.ai_chat.services.llm_service_impl import scrub_for_model
except ImportError:  # the earlier scrub had one function, without the personal-data switch
    scrub_for_model = None

KEY = "sk-proj-AbCdEf0123456789GhIjKlMn"
PLAIN = "plainvalue12345"


def reviewer_path(text):
    """What the reviewer is sent: secrets masked, names, numbers and dates kept."""
    return scrub_for_model(text, mask_pii=False) if scrub_for_model else _scrub_prompt(text)


# (name, text, the secret that must not survive)
MUST_BE_REDACTED = [
    ("url query api_key", "GET https://api.x.io/v1/a?api_key=%s&x=1" % PLAIN, PLAIN),
    ("url query apikey", "https://api.x.io/v1/a?apikey=%s" % PLAIN, PLAIN),
    ("url query access_token", "https://a.io/cb?access_token=%s&state=9" % PLAIN, PLAIN),
    ("url query token", "https://a.io/cb?token=%s" % PLAIN, PLAIN),
    ("url query key AIza", "https://g.io/v1?key=AIzaSyA1234567890abcdefghijklmnopqrstuv", "AIzaSyA1234567890"),
    ("url query client_secret", "https://a.io/t?client_id=1&client_secret=%s" % PLAIN, PLAIN),
    ("url query sig", "https://x.blob.core.windows.net/c?sv=2024&sig=AbCdEf0123456789GhIj%3D", "AbCdEf0123456789GhIj"),
    ("url query amz signature", "https://s3.io/a?X-Amz-Signature=abcdef0123456789abcdef0123456789",
     "abcdef0123456789abcdef"),
    ("url query signature", "https://s3.io/a?signature=abcdef0123456789abcdef0123456789", "abcdef0123456789abcdef"),
    ("url query pwd", "https://a.io/login?pwd=%s" % PLAIN, PLAIN),
    ("url user and password", "postgres://admin:%s@db.internal/app" % PLAIN, PLAIN),
    ("secret key inside a note", "note: password=Hunter2!", "Hunter2!"),
    ("secrets inside a rationale",
     "Connect with password=Hunter3Hunter3 and api_key=%s via https://x.io/a?token=urltoken99887766" % PLAIN,
     "Hunter3Hunter3"),
    ("secret in the url of a rationale",
     "Connect with password=Hunter3Hunter3 and api_key=%s via https://x.io/a?token=urltoken99887766" % PLAIN,
     "urltoken99887766"),
    ("name value pair", json.dumps([{"name": "API_KEY", "value": PLAIN}]), PLAIN),
    ("key value pair", json.dumps({"key": "password", "value": PLAIN}), PLAIN),
    ("header pair", json.dumps({"header": "Authorization", "value": "Bearer abcDEF123456"}), "abcDEF123456"),
    ("plural api_keys", json.dumps({"api_keys": [PLAIN]}), PLAIN),
    ("plural passwords", json.dumps({"passwords": [PLAIN]}), PLAIN),
    ("plural secrets", json.dumps({"secrets": {"a": PLAIN}}), PLAIN),
    ("compact accesstoken", json.dumps({"accesstoken": PLAIN}), PLAIN),
    ("compact clientsecret", json.dumps({"clientsecret": PLAIN}), PLAIN),
    ("compact authtoken", "authtoken=%s" % PLAIN, PLAIN),
    ("PGPASSWORD env", "PGPASSWORD=%s" % PLAIN, PLAIN),
    ("mysql pwd", "mysql_pwd=%s" % PLAIN, PLAIN),
    ("pwd", "pwd=%s" % PLAIN, PLAIN),
    ("pass", "pass=%s" % PLAIN, PLAIN),
    ("password then space", "password %s" % PLAIN, PLAIN),
    ("token then space", "token %s" % PLAIN, PLAIN),
    ("api key words with colon", "API key: %s" % PLAIN, PLAIN),
    ("secret key words with equals", "secret key = %s" % PLAIN, PLAIN),
    ("api_key then space", "api_key %s" % PLAIN, PLAIN),
    ("secret as a json key", json.dumps({KEY: 1}), KEY),
    ("secret as a key in nested json text", json.dumps({"a": json.dumps({KEY: 1})}), KEY),
    ("shape next to a key in json", json.dumps({"note": "use %s now" % KEY}), KEY),
    ("cookie header", "Cookie: session=abcDEF1234567890xyz", "abcDEF1234567890xyz"),
    ("set-cookie header", "Set-Cookie: sid=abcDEF1234567890xyz; Path=/", "abcDEF1234567890xyz"),
    ("x-api-key header", "X-Api-Key: %s" % PLAIN, PLAIN),
    ("api_key then a line break", "api_key:\n  %s" % PLAIN, PLAIN),
    ("api_key then a line break and quotes", 'api_key:\n  "%s"' % PLAIN, PLAIN),
    ("curl -u", "curl -u admin:%s https://a.io" % PLAIN, PLAIN),
    ("curl -H", "curl -H 'Authorization: Bearer abcDEF123456' https://a.io", "abcDEF123456"),
    ("short sk", "sk-abcDEF123456", "sk-abcDEF123456"),
    ("short project key", "sk-proj-AbCdEf01", "AbCdEf01"),
    ("bytes repr", repr({"api_key": PLAIN.encode()}), PLAIN),
    ("escaped quotes", 'cfg {\\"password\\": \\"%s\\"}' % PLAIN, PLAIN),
    ("key broken by a line break", "sk-proj-AbCdEf0123\n456789GhIjKlMnOpQr", "456789GhIjKlMnOpQr"),
    ("key broken inside json text", '{"r": "sk-proj-AbCdEf0123\\n456789GhIjKlMnOpQr"}', "456789GhIjKlMnOpQr"),
    ("key with a zero width space", "sk-proj-AbCd​Ef0123456789GhIjKlMn", "Ef0123456789GhIjKlMn"),
    ("key with a line break after its head", "sk-proj-AbCd\nEf0123456789GhIjKlMn", "Ef0123456789GhIjKlMn"),
    ("card in grouped digits", "pay with 4111 1111 1111 1111 now", "4111 1111 1111 1111"),
    ("card dashed", "card 5500-0000-0000-0004", "5500-0000-0000-0004"),
    ("amex 4-6-5", "card 3782 822463 10005", "3782 822463 10005"),
    ("card ungrouped", "card 4111111111111111", "4111111111111111"),
    ("pwd with four letters", "pwd: abcd", "abcd"),
    ("pwd with three letters", "pwd=xyz", "xyz"),
    ("password with one character", "password: q", ": q"),
    ("token with a short word", "token: abc rest", "abc"),
    ("quoted two character password", 'password: "ab"', "ab"),
    ("secret then a date under an equals sign", "secret=2026-11-10", "2026-11-10"),
]


@pytest.mark.parametrize("name,text,secret", MUST_BE_REDACTED, ids=[c[0] for c in MUST_BE_REDACTED])
def test_a_secret_in_any_of_these_forms_is_redacted(name, text, secret):
    assert secret not in reviewer_path(text)
    assert secret not in _scrub_prompt(text)


def test_the_words_around_a_secret_reach_the_model():
    text = "Connect with password=Hunter3Hunter3 and api_key=%s via https://x.io/a?token=urltoken99887766 today" % PLAIN
    cleaned = reviewer_path(text)
    for word in ("Connect with password=", "and api_key=", "via https://x.io/a?token=", "today"):
        assert word in cleaned
    assert "note: " in reviewer_path("note: password=Hunter2!")


def test_a_secret_is_redacted_in_a_nested_tool_result_and_a_tool_call():
    messages = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": [
            {"type": "tool_result", "content": [{"type": "text", "text": json.dumps({"deep": {"api_key": PLAIN, "k": KEY}})}]}
        ]}]},
        {"role": "user", "content": [{"type": "tool_use", "id": "1", "name": "x",
                                      "input": {"a": {"b": [{"api_key": PLAIN, "n": KEY}]}}}]},
        {"role": "tool", "content": json.dumps({"result": {"headers": {"Authorization": "Bearer abcDEF123456"}}})},
    ]
    sent = json.dumps(scrub_chat_messages(messages))
    for secret in (PLAIN, KEY, "abcDEF123456"):
        assert secret not in sent


# Ordinary text the scrub must leave exactly as it is (the reviewer path).
ORDINARY = [
    "The customer onboarding capability is served by the CRM application and the identity platform.",
    "Migrate the order management system to the target cloud platform by the end of the third quarter.",
    "The API gateway validates bearer tokens before forwarding requests to the payments service.",
    "Retire the legacy billing application once the invoicing work package is complete.",
    "The data warehouse receives nightly extracts from the core banking application.",
    "Application owners must confirm the lifecycle status of each system every year.",
    "The integration layer publishes order events to the Kafka topic for downstream consumers.",
    "Our target architecture separates the customer channel layer from the product processing layer.",
    "The security architect requires mutual TLS between the ingestion service and the data lake.",
    "The value stream for claims handling starts with first notification of loss.",
    "Move the HR self-service portal from development to production after user acceptance testing.",
    "The capability map shows supply chain planning at maturity level three.",
    "The ARB approved the waiver for the mainframe batch scheduler until 2027-06-30.",
    "Contract CT-2041 with the hosting vendor renews on 2027-01-15.",
    "The annual run cost of the ERP platform is 1250000 euros across all regions.",
    "Reduce the recovery time objective of the trading platform to fifteen minutes.",
    "The risk of vendor lock-in is rated high for the proprietary integration suite.",
    "The sk-learn-pipeline-v2 model service scores churn risk every night.",
    "Element sk-capability-map-0042 belongs to the capability map.",
    "Bearer authentication is used on every public API in the partner channel.",
    "The identity provider issues short-lived access tokens to the mobile application.",
    "Secrets are stored in the vault and rotated every ninety days by the platform team.",
    "Purchase order 4532015112830367 covers the storage expansion for the analytics cluster.",
    "The solution uses the event-driven pattern recommended by the integration architect.",
    "Application APP-00412 depends on the message broker and the reference data service.",
    "The roadmap plateau for 2028 removes three duplicate document management systems.",
    "The compliance gap in data retention is closed by the archive work package.",
    "Business capability 3.2.1 Payment Execution is realised by the payments hub.",
    "Version 12.4.1 of the database engine reaches end of support next spring.",
    "The workload runs on 48 virtual machines in two availability zones.",
    "Service level target is 99.95 percent availability measured monthly.",
    "Grouped network range 10.20.30.0/24 hosts the shared integration services.",
    "The server rack serial 12345678 is listed in the asset register.",
    "The business case assumes a payback period of thirty months.",
    "Vendor assessment scores the supplier at 4.2 out of 5 for financial stability.",
    "Owner of the application is the head of retail lending operations.",
    "The data architect has named the party master as the system of record for customers.",
    "Three interfaces still send customer records over unencrypted file transfer.",
    "The token service is shared by the web channel and the contact centre.",
    "Project code ALPHA-7 funds the decommissioning of the warehouse scanner system.",
    "Telephone support is available on the central service desk during office hours.",
    "We keep sk-learn pipelines\nand others",
    "password: required",
    "authorization: required",
    "Password: optional",
    "token: N/A",
    "secret: <hidden>",
    "api_key: ***",
    json.dumps({"authorization": "Delegated to the CFO"}),
    json.dumps({"credentials": "ISO 27001 certified"}),
    "Credential: certified practitioner",
    "Max token: 4096",
    "Token: 8000 per call",
    json.dumps({"max_tokens": "4000"}),
    json.dumps({"token_service": "Okta"}),
    "token_count = 123456789",
    "max_token=123456789012",
    "Order total 1250000.00 EUR",
    "Order total 1 250 000.50",
    "2027-03-31",
    "2026-11-10 14:30:00",
    "2026-11-10 2026-12-15",
    "ISBN 978-3-16-148410-0",
    "IBAN DE89 3704 0044 0532 0130 00",
    "10.2.4.1000.2026.11",
    "+44 20 7946 0958",
    "jane@example.com",
    "10.0.0.1 10.0.0.2 10.0.0.3",
    "the password reset flow is owned by the identity team",
    "We pass the token to the gateway and the secret stays in the vault.",
    "Rotate the secret 2026-11-10 quarterly",
    "Rotate the password 10/11/2026 and review it",
    "Review the token 2026-12-31",
    "Due 2026-11-10",
]


@pytest.mark.parametrize("text", ORDINARY, ids=[str(i) for i in range(len(ORDINARY))])
def test_ordinary_text_comes_through_unchanged(text):
    assert reviewer_path(text) == text


def test_a_short_value_after_a_secret_word_is_redacted_and_the_word_stays():
    assert reviewer_path("pwd: abcd") == "pwd: " + "[CREDENTIAL_REDACTED]"
    assert reviewer_path("Set password: q now") == "Set password: [CREDENTIAL_REDACTED] now"


def test_a_date_is_kept_on_the_chat_path_and_a_phone_number_is_still_masked():
    chat = lambda text: scrub_for_model(text, mask_pii=True)  # noqa: E731 - the chat path masks personal data
    assert chat("Due 2026-11-10") == "Due 2026-11-10"
    assert chat("Due 2026-11-10 and 2026-12-01 for review") == "Due 2026-11-10 and 2026-12-01 for review"
    assert chat("Review on 10/11/2026") == "Review on 10/11/2026"
    assert "[PHONE_REDACTED]" in chat("Call +44 20 7946 0958 about it")
    assert "[PHONE_REDACTED]" in chat("Due 2026-11-10, call 020 7946 0958")
    assert "2026-11-10" in chat("Due 2026-11-10, call 020 7946 0958")
    assert "[EMAIL_REDACTED]" in chat("Due 2026-11-10 to jane@example.com")


def test_two_iso_dates_and_money_figures_are_never_taken_for_a_card():
    random.seed(7)

    def date():
        return "20%02d-%02d-%02d" % (random.randint(24, 30), random.randint(1, 12), random.randint(1, 28))

    for _ in range(3000):
        for text in (date() + " " + date(), date() + "-" + date(), " ".join(str(random.randint(100000, 999999)) for _ in range(3))):
            assert "[CARD_REDACTED]" not in reviewer_path(text), text


def test_a_card_is_redacted_only_when_it_passes_the_check():
    assert "[CARD_REDACTED]" in reviewer_path("pay 4111111111111111")
    assert "[CARD_REDACTED]" not in reviewer_path("order 4111111111111112")
    assert _luhn_ok("4111111111111111")


def test_every_secret_name_in_the_closed_list_is_a_secret_key():
    from app.modules.ai_chat.services.llm_service_impl import is_secret_key

    for name in ("api_key", "api_keys", "passwords", "secrets", "accesstoken", "clientsecret", "PGPASSWORD", "pwd",
                 "mysql_pwd", "pass", "Cookie", "Set-Cookie", "accessToken", "Client-Secret", "authtoken"):
        assert is_secret_key(name), name
    for name in ("max_tokens", "max_token", "token_count", "num_token", "token_limit", "token_service", "passenger",
                 "bypass", "compass", "key", "name"):
        assert not is_secret_key(name), name
