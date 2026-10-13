"""The Entra ID token verifier, against tokens signed inside the test.

Layer: tests (unit)

Nothing here reaches Entra ID. The test makes its own key pairs, publishes their
public halves through a stand-in for the tenant's key document, and signs the
tokens it presents. That is enough to state every rule of the adapter: what it
accepts, each thing it refuses and the reason it records, and how sparingly it
fetches the keys.

Every identifier below is made up.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from energy.adapters.entra_token_verifier import EntraTokenVerifier
from energy.domain.exceptions import AuthenticationError, IdentityProviderUnavailableError
from energy.ports.token_verifier import TokenVerifier

pytestmark = pytest.mark.unit

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
OTHER_CLIENT = "44444444-4444-4444-8444-444444444444"
USER = "55555555-5555-4555-8555-555555555555"
SCOPE = "access_as_user"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"

FIVE_MINUTES = 5 * 60
ONE_DAY = 24 * 60 * 60


class SigningKey:
    """A key pair such as Entra ID signs with, and its published half."""

    def __init__(self, key_id: str) -> None:
        self.key_id = key_id
        self._private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def published(self, use: str = "sig") -> dict[str, Any]:
        document = json.loads(RSAAlgorithm.to_jwk(self._private.public_key()))
        return {**document, "kid": self.key_id, "use": use}

    def sign(self, claims: dict[str, Any], key_id: str | None = None) -> str:
        return jwt.encode(
            claims, self._private, algorithm="RS256", headers={"kid": key_id or self.key_id}
        )

    def sign_without_key_id(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._private, algorithm="RS256")


class KeyDocument:
    """Stands in for the tenant's key document, and counts how often it is read."""

    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys)
        self.reads = 0
        self.unreachable = False

    def __call__(self) -> dict[str, Any]:
        self.reads += 1
        if self.unreachable:
            raise httpx.ConnectError("the identity provider did not answer")
        return {"keys": [key.published() for key in self.keys]}


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(scope="module")
def key() -> SigningKey:
    """Return the key the tenant signs with."""
    return SigningKey("key-current")


@pytest.fixture(scope="module")
def next_key() -> SigningKey:
    """Return a key the tenant starts signing with later."""
    return SigningKey("key-next")


@pytest.fixture
def document(key: SigningKey) -> KeyDocument:
    return KeyDocument(key)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def verifier(document: KeyDocument, clock: Clock) -> EntraTokenVerifier:
    return EntraTokenVerifier(TENANT, CLIENT, SCOPE, fetch_keys=document, clock=clock)


def claims(**changes: Any) -> dict[str, Any]:
    """Return the claims of a valid token, with some replaced or (``None``) removed."""
    now = int(time.time())
    valid: dict[str, Any] = {
        "aud": CLIENT,
        "iss": ISSUER,
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 3600,
        "ver": "2.0",
        "tid": TENANT,
        "oid": USER,
        "sub": "a-pairwise-subject",
        "azp": CLIENT,
        "scp": SCOPE,
        "name": "Ada Lovelace",
        "preferred_username": "ada@example.test",
        "roles": ["operator"],
    }
    valid.update(changes)
    return {name: value for name, value in valid.items() if value is not None}


def reason_refused(verifier: EntraTokenVerifier, token: str) -> str:
    """Present a token that must be refused, and return the recorded reason."""
    with pytest.raises(AuthenticationError) as caught:
        verifier.verify(token)
    # Nothing of the token may travel with the refusal: it would end in a log.
    assert token not in str(caught.value)
    assert token not in repr(caught.value.details)
    return str(caught.value.details["reason"])


# =============================================================================
# What is accepted
# =============================================================================
def test_the_adapter_satisfies_the_port(verifier: EntraTokenVerifier) -> None:
    assert isinstance(verifier, TokenVerifier)


def test_a_valid_token_yields_the_person_and_the_role_values(
    verifier: EntraTokenVerifier, key: SigningKey
) -> None:
    token = claims()
    verified = verifier.verify(key.sign(token))

    assert verified.subject == USER
    assert verified.tenant_id == TENANT
    assert verified.name == "Ada Lovelace"
    assert verified.username == "ada@example.test"
    assert verified.roles == ("operator",)
    assert verified.expires_at.timestamp() == token["exp"]
    assert verified.expires_at.tzinfo is not None


def test_the_scope_may_come_with_others(verifier: EntraTokenVerifier, key: SigningKey) -> None:
    """Entra ID writes every granted scope in one claim, separated by spaces."""
    verified = verifier.verify(key.sign(claims(scp=f"another_scope {SCOPE}")))

    assert verified.subject == USER


def test_the_audience_may_be_a_list(verifier: EntraTokenVerifier, key: SigningKey) -> None:
    verified = verifier.verify(key.sign(claims(aud=[OTHER_CLIENT, CLIENT])))

    assert verified.subject == USER


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (None, ()),
        ([], ()),
        (["viewer", "operator"], ("viewer", "operator")),
        # Values this application does not define are passed on untouched: what
        # a role means is not this module's decision.
        (["something-else"], ("something-else",)),
        # A claim of the wrong shape carries no role at all.
        ("operator", ()),
        (["operator", 7, None], ("operator",)),
    ],
)
def test_role_values_are_passed_on_as_found(
    verifier: EntraTokenVerifier, key: SigningKey, roles: Any, expected: tuple[str, ...]
) -> None:
    assert verifier.verify(key.sign(claims(roles=roles))).roles == expected


def test_a_token_without_a_name_is_still_a_person(
    verifier: EntraTokenVerifier, key: SigningKey
) -> None:
    verified = verifier.verify(key.sign(claims(name=None, preferred_username=None)))

    assert verified.subject == USER
    assert verified.name is None
    assert verified.username is None


def test_a_minute_of_clock_difference_is_tolerated(
    verifier: EntraTokenVerifier, key: SigningKey
) -> None:
    now = int(time.time())

    assert verifier.verify(key.sign(claims(exp=now - 30))).subject == USER
    assert verifier.verify(key.sign(claims(nbf=now + 30, iat=now + 30))).subject == USER
    assert reason_refused(verifier, key.sign(claims(exp=now - 120))) == "expired"


# =============================================================================
# What is refused, and the reason recorded
# =============================================================================
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # Issued for another application.
        ({"aud": OTHER_CLIENT}, "wrong_audience"),
        # The audience a version 1 token would carry.
        ({"aud": f"api://{CLIENT}"}, "wrong_audience"),
        ({"aud": [OTHER_CLIENT]}, "wrong_audience"),
        # Issued by another tenant, or claiming to be.
        ({"iss": f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"}, "wrong_issuer"),
        ({"iss": "https://example.test/"}, "wrong_issuer"),
        ({"tid": OTHER_TENANT}, "wrong_tenant"),
        ({"tid": None}, "wrong_tenant"),
        # A version 1 token: the registration's manifest was not set to 2.
        ({"ver": "1.0", "iss": f"https://sts.windows.net/{TENANT}/"}, "wrong_version"),
        ({"ver": None}, "wrong_version"),
        # No person: a token an application obtained for itself has no scope.
        ({"scp": None}, "missing_scope"),
        ({"scp": "another_scope"}, "missing_scope"),
        # The scope is a whole word, not the beginning of one.
        ({"scp": f"{SCOPE}_and_more"}, "missing_scope"),
        ({"scp": [SCOPE]}, "missing_scope"),
        ({"oid": None}, "no_subject"),
        ({"oid": ""}, "no_subject"),
        # A token must state its own validity period, issuer and audience.
        ({"exp": None}, "invalid"),
        ({"nbf": None}, "invalid"),
        ({"iat": None}, "invalid"),
        ({"iss": None}, "invalid"),
        ({"aud": None}, "invalid"),
    ],
)
def test_a_token_that_is_not_ours_is_refused(
    verifier: EntraTokenVerifier, key: SigningKey, changes: dict[str, Any], reason: str
) -> None:
    assert reason_refused(verifier, key.sign(claims(**changes))) == reason


def test_an_expired_token_is_refused(verifier: EntraTokenVerifier, key: SigningKey) -> None:
    token = key.sign(claims(exp=int(time.time()) - 3600))

    assert reason_refused(verifier, token) == "expired"


def test_a_token_from_the_future_is_refused(verifier: EntraTokenVerifier, key: SigningKey) -> None:
    token = key.sign(claims(nbf=int(time.time()) + 3600))

    assert reason_refused(verifier, token) == "not_yet_valid"


def test_a_token_that_names_no_algorithm_is_refused(
    verifier: EntraTokenVerifier, key: SigningKey, document: KeyDocument
) -> None:
    """The oldest attack on these tokens: declare that there is no signature."""
    unsigned = jwt.encode(claims(), None, algorithm="none", headers={"kid": key.key_id})

    assert reason_refused(verifier, unsigned) == "algorithm"
    # Refused on its header alone: no key was looked for.
    assert document.reads == 0


def test_a_token_signed_with_a_shared_secret_is_refused(
    verifier: EntraTokenVerifier, key: SigningKey, document: KeyDocument
) -> None:
    """The second oldest: sign with a secret and hope the public key is used as one."""
    forged = jwt.encode(claims(), "s" * 64, algorithm="HS256", headers={"kid": key.key_id})

    assert reason_refused(verifier, forged) == "algorithm"
    assert document.reads == 0


def test_a_token_signed_by_someone_else_is_refused(
    verifier: EntraTokenVerifier, key: SigningKey, next_key: SigningKey
) -> None:
    """A stranger's signature under the name of a key the tenant does publish."""
    forged = next_key.sign(claims(), key_id=key.key_id)

    assert reason_refused(verifier, forged) == "bad_signature"


def test_a_token_whose_content_was_changed_is_refused(
    verifier: EntraTokenVerifier, key: SigningKey
) -> None:
    header, _, signature = key.sign(claims(roles=["viewer"])).split(".")
    _, promoted, _ = key.sign(claims(roles=["admin"])).split(".")

    assert reason_refused(verifier, f"{header}.{promoted}.{signature}") == "bad_signature"


def test_a_token_without_a_key_identifier_is_refused(
    verifier: EntraTokenVerifier, key: SigningKey
) -> None:
    assert reason_refused(verifier, key.sign_without_key_id(claims())) == "no_key_id"


@pytest.mark.parametrize("text", ["", "not-a-token", "a.b.c", "Bearer abc"])
def test_text_that_is_not_a_token_is_refused(verifier: EntraTokenVerifier, text: str) -> None:
    with pytest.raises(AuthenticationError) as caught:
        verifier.verify(text)

    assert caught.value.details["reason"] == "malformed"


# =============================================================================
# The signing keys
# =============================================================================
def test_the_keys_are_fetched_once_and_then_reused(
    verifier: EntraTokenVerifier, key: SigningKey, document: KeyDocument, clock: Clock
) -> None:
    for _ in range(5):
        verifier.verify(key.sign(claims()))
        clock.advance(60)

    assert document.reads == 1


def test_the_keys_are_fetched_again_after_a_day(
    verifier: EntraTokenVerifier, key: SigningKey, document: KeyDocument, clock: Clock
) -> None:
    verifier.verify(key.sign(claims()))
    clock.advance(ONE_DAY - 1)
    verifier.verify(key.sign(claims()))
    assert document.reads == 1

    clock.advance(2)
    verifier.verify(key.sign(claims()))
    assert document.reads == 2


def test_a_new_key_is_found_when_a_token_names_it(
    verifier: EntraTokenVerifier,
    key: SigningKey,
    next_key: SigningKey,
    document: KeyDocument,
    clock: Clock,
) -> None:
    """Entra ID changes keys without notice; the held ones must not lock users out."""
    verifier.verify(key.sign(claims()))
    document.keys.append(next_key)
    clock.advance(FIVE_MINUTES)

    assert verifier.verify(next_key.sign(claims())).subject == USER
    assert document.reads == 2


def test_an_unknown_key_does_not_cause_a_fetch_within_five_minutes(
    verifier: EntraTokenVerifier,
    key: SigningKey,
    next_key: SigningKey,
    document: KeyDocument,
    clock: Clock,
) -> None:
    """A flood of forged tokens must not turn into a flood of calls to Entra ID."""
    verifier.verify(key.sign(claims()))

    for attempt in range(50):
        clock.advance(1)
        forged = next_key.sign(claims(), key_id=f"made-up-{attempt}")
        assert reason_refused(verifier, forged) == "unknown_key"

    assert document.reads == 1

    clock.advance(FIVE_MINUTES)
    assert reason_refused(verifier, next_key.sign(claims())) == "unknown_key"
    assert document.reads == 2


def test_a_published_key_that_is_not_for_signing_is_not_used(
    key: SigningKey, next_key: SigningKey, clock: Clock
) -> None:
    def keys() -> dict[str, Any]:
        return {"keys": [key.published(), next_key.published(use="enc")]}

    verifier = EntraTokenVerifier(TENANT, CLIENT, SCOPE, fetch_keys=keys, clock=clock)

    assert verifier.verify(key.sign(claims())).subject == USER
    assert reason_refused(verifier, next_key.sign(claims())) == "unknown_key"


def test_held_keys_keep_working_when_the_provider_does_not_answer(
    verifier: EntraTokenVerifier, key: SigningKey, document: KeyDocument, clock: Clock
) -> None:
    verifier.verify(key.sign(claims()))
    document.unreachable = True
    clock.advance(ONE_DAY + 1)

    assert verifier.verify(key.sign(claims())).subject == USER
    assert document.reads == 2

    # The failed attempt is not repeated on the very next request ...
    assert verifier.verify(key.sign(claims())).subject == USER
    assert document.reads == 2

    # ... but half a minute later it is.
    clock.advance(31)
    assert verifier.verify(key.sign(claims())).subject == USER
    assert document.reads == 3


def test_with_no_keys_and_no_answer_the_token_cannot_be_checked(
    verifier: EntraTokenVerifier, key: SigningKey, document: KeyDocument, clock: Clock
) -> None:
    """Not a refusal: the token may be perfectly good. The failure is on this side."""
    document.unreachable = True

    with pytest.raises(IdentityProviderUnavailableError):
        verifier.verify(key.sign(claims()))
    with pytest.raises(IdentityProviderUnavailableError):
        verifier.verify(key.sign(claims()))
    assert document.reads == 1

    document.unreachable = False
    clock.advance(31)
    assert verifier.verify(key.sign(claims())).subject == USER


@pytest.mark.parametrize("answer", [{}, {"keys": []}, {"keys": "none"}, {"keys": [{"kty": "?"}]}])
def test_a_key_document_with_no_usable_key_counts_as_no_answer(
    key: SigningKey, clock: Clock, answer: dict[str, Any]
) -> None:
    verifier = EntraTokenVerifier(TENANT, CLIENT, SCOPE, fetch_keys=lambda: answer, clock=clock)

    with pytest.raises(IdentityProviderUnavailableError):
        verifier.verify(key.sign(claims()))


def test_the_keys_are_read_from_the_tenants_own_address(
    key: SigningKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a stand-in, the adapter asks Entra ID for this tenant's keys, with a time limit."""
    asked: list[tuple[str, float]] = []

    def answer(url: str, *, timeout: float) -> httpx.Response:
        asked.append((url, timeout))
        return httpx.Response(
            200, json={"keys": [key.published()]}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx, "get", answer)
    verifier = EntraTokenVerifier(TENANT, CLIENT, SCOPE)

    assert verifier.verify(key.sign(claims())).subject == USER
    assert asked == [(f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys", 5.0)]


def test_an_error_answer_from_the_provider_counts_as_no_answer(
    key: SigningKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(url: str, *, timeout: float) -> httpx.Response:
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", answer)
    verifier = EntraTokenVerifier(TENANT, CLIENT, SCOPE)

    with pytest.raises(IdentityProviderUnavailableError):
        verifier.verify(key.sign(claims()))
