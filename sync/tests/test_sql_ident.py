"""Identifier validation for SQL built by the sync engine (IMPROVEMENT_PLAN.md 4.6)."""
import pytest

from sync.sql_ident import UnsafeIdentifier, identifier, qualified, split_qualified


def test_plain_names_are_quoted():
    assert identifier("CalSoft_calibrationsession") == '"CalSoft_calibrationsession"'
    assert qualified("public", "jobcard_jobcard") == '"public"."jobcard_jobcard"'
    assert qualified(None, "t") == '"public"."t"'


def test_already_quoted_names_are_accepted():
    assert identifier('"Inventory_equipment"') == '"Inventory_equipment"'


@pytest.mark.parametrize("bad", [
    'x"; DROP TABLE users; --', "a b", "a.b", "1table", "", "tbl;", "t'", "t)--",
])
def test_anything_but_a_plain_identifier_is_refused(bad):
    with pytest.raises(UnsafeIdentifier):
        identifier(bad)


def test_split_qualified():
    assert split_qualified("public.jobcard_jobcard") == ("public", "jobcard_jobcard")
    assert split_qualified("jobcard_jobcard") == ("public", "jobcard_jobcard")
    with pytest.raises(UnsafeIdentifier):
        split_qualified('public.x"--')
