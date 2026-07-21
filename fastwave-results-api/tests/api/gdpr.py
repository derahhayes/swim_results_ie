"""Reusable GDPR-leak scanner: walks a JSON-like structure recursively and
asserts none of the forbidden field names/terms appear anywhere, as a key
or inside a string value. Run against every public endpoint's response,
every test session - see test_gdpr_scan.py.
"""

FORBIDDEN_TERMS = ["dateOfBirth", "registrationNo", "citizenship", "email", "address"]


def assert_no_pii(data: object, _path: str = "$") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            for term in FORBIDDEN_TERMS:
                assert term.lower() not in key.lower(), f"forbidden key {key!r} found at {_path}"
            assert_no_pii(value, f"{_path}.{key}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            assert_no_pii(item, f"{_path}[{i}]")
    elif isinstance(data, str):
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in data.lower(), f"forbidden term {term!r} found in value {data!r} at {_path}"
