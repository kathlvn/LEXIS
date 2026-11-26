r"""Checks GET /public/articles/search-text, which the site search reads.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_public_article_search_text.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main_module  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import Article, StaffUser  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main_module.engine = engine   # So nothing here can reach the real database.

failures = 0


def check(label, actual, expected):
    global failures
    ok = actual == expected
    failures += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f": got {actual!r}, expected {expected!r}"))


def override_session():
    with Session(engine) as session:
        yield session


with Session(engine) as s:
    staff = StaffUser(full_name="Secret Staff Name", email="staff@example.com", hashed_password="x", role="head_attorney")
    s.add(staff)
    s.commit()
    common = dict(author_staff_id=staff.id, updated_by_staff_id=staff.id)
    s.add(Article(title="Older", body="Plain words.", status="published",
                  published_at=datetime(2026, 10, 1), **common))
    s.add(Article(title="Newer", body="## A heading\n\nSome **bold** words.\n\n- one item\n- 2 items",
                  status="published", published_at=datetime(2026, 10, 31), **common))
    s.add(Article(title="Draft title", body="draft words", status="draft", **common))
    s.add(Article(title="Hidden title", body="hidden words", status="hidden",
                  published_at=datetime(2026, 9, 1), **common))
    s.commit()
    older_id = s.exec(__import__("sqlmodel").select(Article).where(Article.title == "Older")).first().id

app.dependency_overrides[get_session] = override_session
client = TestClient(app)

res = client.get("/public/articles/search-text")
rows = res.json()
check("answers without signing in", res.status_code, 200)
check("published only, newest first", [r["title"] for r in rows], ["Newer", "Older"])
check("the marks are removed from the text", rows[0]["text"], "A heading Some bold words. one item 2 items")
check("only the four public fields are sent", sorted(rows[0]), ["id", "published_at", "text", "title"])
check("no staff name anywhere in the answer", "Secret Staff Name" in res.text, False)

# The new path must not break the single-article path next to it.
check("one article still opens", client.get(f"/public/articles/{older_id}").json().get("title"), "Older")
check("the normal list still sends excerpts", client.get("/public/articles").json()[0].get("excerpt"),
      "A heading Some bold words. one item 2 items")

app.dependency_overrides.clear()
print("\nAll passed." if not failures else f"\n{failures} FAILED.")
sys.exit(1 if failures else 0)
