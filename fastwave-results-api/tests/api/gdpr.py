"""Reusable GDPR-leak scanner: walks a JSON-like structure recursively and
asserts none of the forbidden field names/terms appear anywhere, as a key
or inside a string value. Run against every public endpoint's response,
every test session - see test_gdpr_scan.py.

Step 5's private endpoints (swimmer-results, coach-view) are a deliberate,
narrow exception - a claimed swimmer's own DOB/registrationNo, or a coach's
approved-affiliation club roster, is meant to carry those fields. `allow`
lets those tests scan for everything else (email, address, citizenship,
and each other's un-allowed terms) without blanket-permitting all PII.
"""

FORBIDDEN_TERMS = ["dateOfBirth", "registrationNo", "citizenship", "email", "address"]


def assert_no_pii(data: object, _path: str = "$", allow: tuple[str, ...] = ()) -> None:
    allowed = {term.lower() for term in allow}
    terms = [t for t in FORBIDDEN_TERMS if t.lower() not in allowed]

    if isinstance(data, dict):
        for key, value in data.items():
            for term in terms:
                assert term.lower() not in key.lower(), f"forbidden key {key!r} found at {_path}"
            assert_no_pii(value, f"{_path}.{key}", allow=allow)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            assert_no_pii(item, f"{_path}[{i}]", allow=allow)
    elif isinstance(data, str):
        for term in terms:
            assert term.lower() not in data.lower(), f"forbidden term {term!r} found in value {data!r} at {_path}"
