from collections.abc import Iterator
import logging
import os
import time

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str, *, poolclass: type[Pool] | None = None) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine_options: dict[str, object] = {"connect_args": connect_args}
    if poolclass is not None:
        engine_options["poolclass"] = poolclass
    engine = create_engine(database_url, **engine_options)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                if ":memory:" not in database_url:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    # A successful business commit includes its revision and
                    # file-operation intent. Do not trade that durability for
                    # throughput of unrelated streaming/UI writes.
                    cursor.execute("PRAGMA synchronous=FULL")
            except Exception:
                cursor.close()
                dbapi_connection.close()
                raise
            else:
                cursor.close()

    return engine


def connect_after_process_exit(engine: Engine) -> Connection:
    """Open SQLite after owned Windows writers exit; never retry business SQL.

    Windows can briefly retain WAL/shared-memory handles after process exit.
    A persistent error still propagates, and no SQLite sidecar is removed.
    """
    for attempt in range(6):
        try:
            return engine.connect()
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", 0)
            if os.name != "nt" or code & 0xFF not in (5, 6, 10) or attempt == 5:
                raise
            logging.getLogger(__name__).warning(
                "Retrying stopped-process connection after %s (attempt %d/6)",
                getattr(error.orig, "sqlite_errorname", "SQLite connection error"),
                attempt + 1,
            )
            time.sleep(0.2)
    raise AssertionError("Unreachable connection retry state")


def get_session(request: Request) -> Iterator[Session]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


SessionFactory = sessionmaker[Session]
