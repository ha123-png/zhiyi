"""Keep multi-query readers on one SQLite snapshot, including legacy sqlite3 mode."""
from sqlalchemy.orm import Session


def begin_read_snapshot(session: Session) -> None:
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # sqlite3 legacy transaction control doesn't BEGIN for SELECT. Without
        # this, a file header and its paged details could come from different edits.
        connection.exec_driver_sql("BEGIN")


def begin_write_snapshot(session: Session) -> None:
    """Serialize schema/range checks with writes instead of checking an old scope."""
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
