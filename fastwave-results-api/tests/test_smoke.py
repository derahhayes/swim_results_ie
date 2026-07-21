from app.models import Base

EXPECTED_TABLES = {
    "users",
    "swimmer_claims",
    "coach_affiliations",
    "clubs",
    "swimmers",
    "meets",
    "meet_events",
    "results",
    "result_splits",
    "relay_legs",
    "uploads",
    "match_reviews",
    "audit_log",
    "app_settings",
}


def test_all_models_import():
    import app.models  # noqa: F401


def test_expected_tables_present():
    assert EXPECTED_TABLES == set(Base.metadata.tables.keys())


def test_result_unique_constraints_exist():
    results_table = Base.metadata.tables["results"]

    index_names = {ix.name for ix in results_table.indexes}
    assert "ux_result_individual" in index_names
    assert "ux_result_relay" in index_names

    constraint_names = {c.name for c in results_table.constraints}
    assert "ck_result_relay_shape" in constraint_names
