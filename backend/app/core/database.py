from sqlmodel import SQLModel, Session, create_engine
from app.core.config import settings

# The log never shows the values in a query (client names, emails, message
# text), not even on an error. Printing the SQL itself is off unless
# SQL_ECHO=true is set while debugging.
#
# Connections (PERF-02): the server answers up to 40 calls at a time (the
# worker threads), but the default pool has only 15 connections, so at 100
# users calls queued for 30-60 s and failed. 20 + 20 = 40 matches the
# threads; PostgreSQL allows 100. SQLite (the test scripts) keeps its own pool.
_pool = {} if settings.database_url.startswith("sqlite") else {
    "pool_size": 20, "max_overflow": 20, "pool_timeout": 30, "pool_pre_ping": True,
}
engine = create_engine(settings.database_url, echo=settings.sql_echo, hide_parameters=True, **_pool)


def create_db_and_tables():
    """
    Dev-only convenience: creates tables directly from the SQLModel classes.
    For production, use Alembic migrations instead (see alembic/ — this repo
    ships with the folder started but migrations aren't written yet; that's
    the next thing to add once the schema settles).
    """
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session