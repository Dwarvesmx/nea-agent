"""Frontera de ámbito: un sobre demo jamás se interpreta como comercial."""
from __future__ import annotations
from typing import Any


def has_demo_scope(payload: dict[str, Any]) -> bool:
    conversation = payload.get("conversation")
    return ("demo" in payload or "scope" in payload or "scope_kind" in payload
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
