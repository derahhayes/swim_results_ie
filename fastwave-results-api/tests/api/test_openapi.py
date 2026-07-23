"""Structural validation of /openapi.json.

No external validator library here: openapi-spec-validator's dependency
range conflicts with hytek-parser's pinned attrs<22. This checks the
things that actually matter for Lovable's codegen - every response has a
named, resolvable schema, and there are no dangling $refs - by walking the
document directly.
"""

import re

REF_RE = re.compile(r"^#/components/schemas/(.+)$")


def _walk_refs(node, refs: set[str]) -> None:
    if isinstance(node, dict):
        if "$ref" in node:
            refs.add(node["$ref"])
        for value in node.values():
            _walk_refs(value, refs)
    elif isinstance(node, list):
        for item in node:
            _walk_refs(item, refs)


async def test_openapi_has_expected_top_level_shape(api_client):
    resp = await api_client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()

    assert schema["openapi"].startswith("3.")
    assert "paths" in schema and schema["paths"]
    assert "components" in schema and "schemas" in schema["components"]


async def test_openapi_every_ref_resolves(api_client):
    resp = await api_client.get("/openapi.json")
    schema = resp.json()

    refs: set[str] = set()
    _walk_refs(schema["paths"], refs)
    _walk_refs(schema["components"]["schemas"], refs)
    assert refs  # sanity: we do use $ref somewhere

    schema_names = set(schema["components"]["schemas"].keys())
    for ref in refs:
        match = REF_RE.match(ref)
        assert match, f"unexpected $ref format: {ref}"
        assert match.group(1) in schema_names, f"dangling $ref: {ref}"


async def test_all_v1_endpoints_have_named_response_schemas(api_client):
    resp = await api_client.get("/openapi.json")
    schema = resp.json()

    for path, methods in schema["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, operation in methods.items():
            responses = operation.get("responses", {})
            ok = responses.get("200") or responses.get("201")
            if ok is None:
                continue

            ok_content = ok.get("content", {})
            if "application/json" not in ok_content:
                # A genuinely non-JSON response (e.g. the coach-view CSV
                # export) - nothing here for Lovable's JSON codegen to see.
                continue

            content = ok_content["application/json"]
            assert "schema" in content, f"{method.upper()} {path} has no JSON schema"
            response_schema = content["schema"]

            # A bare list[Model] response is `{type: array, items: {$ref: ...}}`
            # - a named item schema referenced by array, not an anonymous
            # inline object, so that's fine too.
            is_named_ref = "$ref" in response_schema
            is_array_of_named_ref = response_schema.get("type") == "array" and "$ref" in response_schema.get(
                "items", {}
            )
            assert is_named_ref or is_array_of_named_ref, (
                f"{method.upper()} {path} returns an anonymous inline schema"
            )
