"""WebAuthn registration and authentication ceremonies."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from modules.models import User, WebAuthnCredential
from modules.utils import get_current_time
from services.passkeys.challenges import (
    challenge_b64_from_credential,
    consume_challenge,
    store_challenge,
)
from services.passkeys.rp import resolve_relying_party
from services.passkeys.types import (
    GENERIC_LOGIN_FAILURE,
    MAX_PASSKEYS_PER_USER,
    PasskeyError,
    RelyingParty,
)

_AUTHENTICATOR_SELECTION = AuthenticatorSelectionCriteria(
    resident_key=ResidentKeyRequirement.REQUIRED,
    require_resident_key=True,
    user_verification=UserVerificationRequirement.REQUIRED,
)


def user_handle_for(user_id: int) -> bytes:
    return user_id.to_bytes(8, "big")


def _transports_from_row(raw: list[str] | None) -> list[AuthenticatorTransport] | None:
    if not raw:
        return None
    allowed = {item.value: item for item in AuthenticatorTransport}
    parsed = [allowed[item] for item in raw if item in allowed]
    return parsed or None


def _transports_from_credential(credential: dict[str, Any]) -> list[str]:
    response = credential.get("response")
    raw = response.get("transports") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)][:8]


def _descriptors(rows: list[WebAuthnCredential]) -> list[PublicKeyCredentialDescriptor]:
    descriptors: list[PublicKeyCredentialDescriptor] = []
    for row in rows:
        try:
            credential_id = base64url_to_bytes(row.credential_id)
        except ValueError, TypeError:
            continue
        descriptors.append(
            PublicKeyCredentialDescriptor(
                id=credential_id,
                transports=_transports_from_row(row.transports),
            )
        )
    return descriptors


def _normalize_nickname(nickname: str | None) -> str:
    return (nickname or "").strip()[:100]


def _challenge_bytes(record: dict[str, Any]) -> bytes:
    try:
        return base64url_to_bytes(str(record["challenge"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401) from exc


def _credential_id_b64(credential: dict[str, Any]) -> str:
    raw = credential.get("id") or credential.get("rawId")
    if not isinstance(raw, str) or not raw:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401)
    try:
        return bytes_to_base64url(base64url_to_bytes(raw))
    except (ValueError, TypeError) as exc:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401) from exc


def _require_matching_challenge(record: dict[str, Any], rp: RelyingParty, ceremony: str) -> None:
    if record.get("type") != ceremony:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401)
    if record.get("origin") != rp.origin or record.get("rp_id") != rp.rp_id:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401)


def _cloned_sign_count(stored: int, incoming: int) -> bool:
    return incoming > 0 and stored > 0 and incoming <= stored


async def start_registration(
    *,
    origin_header: str | None,
    user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    if user.id is None:
        raise PasskeyError("passkey_verification_failed")
    rp = resolve_relying_party(origin_header=origin_header)
    existing = await WebAuthnCredential.list_for_user(db, user.id)
    if len(existing) >= MAX_PASSKEYS_PER_USER:
        raise PasskeyError("passkey_limit_reached", 409)
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=rp.rp_name,
        user_id=user_handle_for(user.id),
        user_name=user.username,
        user_display_name=user.username,
        authenticator_selection=_AUTHENTICATOR_SELECTION,
        exclude_credentials=_descriptors(existing),
    )
    await store_challenge(
        bytes_to_base64url(options.challenge),
        ceremony="register",
        origin=rp.origin,
        rp_id=rp.rp_id,
        user_id=user.id,
    )
    return options_to_json_dict(options)


async def complete_registration(
    *,
    origin_header: str | None,
    user: User,
    db: AsyncSession,
    credential: dict[str, Any],
    nickname: str | None,
) -> WebAuthnCredential:
    if user.id is None:
        raise PasskeyError("passkey_verification_failed")
    rp = resolve_relying_party(origin_header=origin_header)
    record = await consume_challenge(challenge_b64_from_credential(credential))
    _require_matching_challenge(record, rp, "register")
    if record.get("user_id") != user.id:
        raise PasskeyError("passkey_verification_failed")
    if await WebAuthnCredential.count_for_user(db, user.id) >= MAX_PASSKEYS_PER_USER:
        raise PasskeyError("passkey_limit_reached", 409)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=_challenge_bytes(record),
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, ValueError, TypeError, KeyError) as exc:
        raise PasskeyError("passkey_verification_failed") from exc
    credential_id = bytes_to_base64url(verified.credential_id)
    if await WebAuthnCredential.get_by_credential_id(db, credential_id) is not None:
        raise PasskeyError("passkey_duplicate", 409)
    backed_up = bool(verified.credential_backed_up)
    row = WebAuthnCredential(
        user_id=user.id,
        credential_id=credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=_transports_from_credential(credential),
        aaguid=verified.aaguid or None,
        backup_eligible=verified.credential_device_type is CredentialDeviceType.MULTI_DEVICE,
        backup_state=backed_up,
        nickname=_normalize_nickname(nickname),
        created_at=get_current_time(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def start_authentication(
    *,
    origin_header: str | None,
    db: AsyncSession,
    username: str | None,
) -> dict[str, Any]:
    rp = resolve_relying_party(origin_header=origin_header)
    user_id, allow = await _login_allow_list(db, username)
    options = generate_authentication_options(
        rp_id=rp.rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    await store_challenge(
        bytes_to_base64url(options.challenge),
        ceremony="login",
        origin=rp.origin,
        rp_id=rp.rp_id,
        user_id=user_id,
    )
    return options_to_json_dict(options)


async def _login_allow_list(
    db: AsyncSession, username: str | None
) -> tuple[int | None, list[PublicKeyCredentialDescriptor] | None]:
    trimmed = (username or "").strip()
    if not trimmed:
        return None, None
    user = await User.get_by_username(db, trimmed)
    if user is None or not user.is_active or user.id is None:
        return None, []
    return user.id, _descriptors(await WebAuthnCredential.list_for_user(db, user.id))


async def complete_authentication(
    *,
    origin_header: str | None,
    db: AsyncSession,
    credential: dict[str, Any],
) -> User:
    rp = resolve_relying_party(origin_header=origin_header)
    record = await consume_challenge(challenge_b64_from_credential(credential))
    _require_matching_challenge(record, rp, "login")
    row = await WebAuthnCredential.get_by_credential_id(db, _credential_id_b64(credential))
    if row is None:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401)
    bound_user_id = record.get("user_id")
    if bound_user_id is not None and bound_user_id != row.user_id:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=_challenge_bytes(record),
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            credential_public_key=row.public_key,
            credential_current_sign_count=int(row.sign_count),
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, ValueError, TypeError, KeyError) as exc:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401) from exc
    if _cloned_sign_count(int(row.sign_count), verified.new_sign_count):
        raise PasskeyError("passkey_cloned", 401)
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise PasskeyError(GENERIC_LOGIN_FAILURE, 401)
    row.sign_count = verified.new_sign_count
    row.backup_state = verified.credential_backed_up
    row.last_used_at = get_current_time()
    db.add(row)
    await db.commit()
    return user


async def list_passkeys(db: AsyncSession, user_id: int) -> list[WebAuthnCredential]:
    return await WebAuthnCredential.list_for_user(db, user_id)


async def rename_credential(
    db: AsyncSession, user_id: int, credential_pk: int, nickname: str
) -> WebAuthnCredential:
    row = await WebAuthnCredential.get_for_user(db, credential_pk, user_id)
    if row is None:
        raise PasskeyError("passkey_not_found", 404)
    row.nickname = _normalize_nickname(nickname)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_credential(db: AsyncSession, user_id: int, credential_pk: int) -> None:
    row = await WebAuthnCredential.get_for_user(db, credential_pk, user_id)
    if row is None:
        raise PasskeyError("passkey_not_found", 404)
    await db.delete(row)
    await db.commit()
