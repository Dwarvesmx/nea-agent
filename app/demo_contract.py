"""Frontera de ámbito: un sobre demo jamás se interpreta como comercial."""
from __future__ import annotations
import re
from typing import Any


def has_demo_scope(payload: dict[str, Any]) -> bool:
    conversation = payload.get("conversation")
    return ("demo" in payload or "channelContext" in payload or "scope" in payload or "scope_kind" in payload
            or "scopeKind" in payload
            or isinstance(conversation, dict) and any(k in conversation for k in ("demo", "scope", "scopeKind", "scope_kind", "demoSessionId")))


def validate_demo_envelope(payload: dict[str, Any]) -> None:
    demo = payload.get("demo")
    if not isinstance(demo, dict) or set(demo) != {"contractVersion", "scope"}:
        raise ValueError("invalid_demo_contract")
    if type(demo["contractVersion"]) is not int or demo["contractVersion"] != 1:
        raise ValueError("invalid_demo_version")
    scope = demo["scope"]
    fields = {"kind", "organizationId", "spaceId", "sessionId", "conversationId", "authorizationId", "routeGeneration", "profileVersionId"}
    if not isinstance(scope, dict) or set(scope) != fields or scope.get("kind") != "demo":
        raise ValueError("invalid_demo_scope")
    for key in fields - {"kind", "routeGeneration"}:
        if not isinstance(scope[key], str) or not 0 < len(scope[key]) <= 128:
            raise ValueError("invalid_demo_scope")
    if type(scope["routeGeneration"]) is not int or not 0 < scope["routeGeneration"] <= 9007199254740991:
        raise ValueError("invalid_demo_generation")
    org, conversation = payload.get("organization"), payload.get("conversation")
    if not isinstance(org, dict) or org.get("id") != scope["organizationId"]:
        raise ValueError("demo_org_mismatch")
    if not isinstance(conversation, dict) or conversation.get("id") != scope["conversationId"]:
        raise ValueError("demo_conversation_mismatch")
    channel = payload.get("channelContext")
    if not isinstance(channel, dict) or set(channel) != {"accountRef", "identity"}:
        raise ValueError("invalid_demo_channel")
    account = channel["accountRef"]
    identity = channel["identity"]
    if not isinstance(account, str) or not 0 < len(account) <= 128 or account.strip() != account:
        raise ValueError("invalid_demo_account")
    if not isinstance(identity, dict) or set(identity) != {"kind", "value"}:
        raise ValueError("invalid_demo_identity")
    kind, value = identity["kind"], identity["value"]
    if kind not in ("phone", "bsuid") or not isinstance(value, str) or not 0 < len(value) <= 128 or value.strip() != value:
        raise ValueError("invalid_demo_identity")
    if kind == "phone" and not re.fullmatch(r"[1-9][0-9]{6,14}", value):
        raise ValueError("invalid_demo_phone")
    contact = payload.get("contact")
    canonical = value if kind == "phone" else "bsuid:" + value
    if not isinstance(contact, dict) or contact.get("identity") != canonical:
        raise ValueError("demo_identity_mismatch")
