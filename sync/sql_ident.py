"""Safe SQL identifiers for the sync engine (IMPROVEMENT_PLAN.md 4.6).

Values always go to ``cursor.execute`` as parameters. Table and schema names
cannot be parameters, so they are formatted into the SQL, and everything that
formats one goes through here. A name is accepted only if it is a plain
identifier (letters, digits, underscore; not starting with a digit). A table
name from config, the catalog or HQ that does not match raises before any SQL
is built, so quoting can never be escaped.
"""
import re

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeIdentifier(ValueError):
    pass


def identifier(name):
    """Return ``name`` double-quoted, after checking it is a plain identifier."""
    name = str(name).strip().strip('"')
    if not _IDENTIFIER.match(name):
        raise UnsafeIdentifier(f"refusing to use {name!r} as an SQL identifier")
    return f'"{name}"'


def qualified(schema, table):
    """``"schema"."table"``, both parts validated."""
    return f"{identifier(schema or 'public')}.{identifier(table)}"


def split_qualified(name, default_schema="public"):
    """``'schema.table'`` or ``'table'`` -> (schema, table), unquoted and validated."""
    schema, _, table = str(name).rpartition(".")
    schema, table = (schema or default_schema).strip('"'), table.strip('"')
    identifier(schema)
    identifier(table)
    return schema, table
