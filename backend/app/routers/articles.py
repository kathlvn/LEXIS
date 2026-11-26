"""
Articles: short write-ups the Legal Office posts
on the public Home page. `router` is the staff side; `public_router` (below)
serves the public list and single-article page.

- Every staff member sees every article, drafts included
  (a small office, and it avoids two people writing the same
  topic).
- Any staff member writes a draft and edits their own drafts. The Head
  Attorney may edit any draft.
- Only the Head Attorney publishes, hides, publishes again, or edits an
  article that is published or hidden (what the public sees is the
  office's word).
- draft -> published -> hidden -> published again. Never deleted.
- Publishing, hiding, and edits to a published or hidden article go to
  the Security Log (AuditLogEntry needs a request, so it can't be used).
  Drafts are not logged: they never reach the public.
- public_router: published articles only, no sign-in, no staff
  names (the public byline is "The Legal Office").
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_current_staff_user, require_head_attorney
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.models.models import ARTICLE_STATUSES, Article, StaffUser
from app.schemas.schemas import (
    ArticleCreate, ArticleListItem, ArticleOut, ArticleUpdate, PublicArticleListItem, PublicArticleOut,
    PublicArticleSearchItem,
)

router = APIRouter(prefix="/articles", tags=["articles"])
public_router = APIRouter(prefix="/public/articles", tags=["articles"])

EXCERPT_LENGTH = 220


def _is_head(staff: StaffUser) -> bool:
    return staff.role == "head_attorney"


def _can_edit(article: Article, staff: StaffUser) -> bool:
    if _is_head(staff):
        return True
    return article.status == "draft" and article.author_staff_id == staff.id


def _names(session: Session) -> dict[int, str]:
    return {s.id: s.full_name for s in session.exec(select(StaffUser)).all()}


def _fields(article: Article, staff: StaffUser, names: dict[int, str]) -> dict:
    return dict(
        id=article.id, title=article.title, status=article.status,
        author_name=names.get(article.author_staff_id, "Unknown"),
        created_at=article.created_at, updated_at=article.updated_at,
        updated_by_name=names.get(article.updated_by_staff_id),
        published_at=article.published_at,
        published_by_name=names.get(article.published_by_staff_id),
        can_edit=_can_edit(article, staff),
        can_publish=_is_head(staff) and article.status in ("draft", "hidden"),
        can_hide=_is_head(staff) and article.status == "published",
    )


def _out(session: Session, article: Article, staff: StaffUser) -> ArticleOut:
    return ArticleOut(**_fields(article, staff, _names(session)), body=article.body)


def _get(session: Session, article_id: int) -> Article:
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found. It may have been removed from the list; reload the page.")
    return article


def _log(session: Session, request: Request, event_type: str, article: Article, staff: StaffUser) -> None:
    log_security_event(session, event_type, f"\"{article.title}\" (article #{article.id}, by {staff.full_name})",
                       get_client_ip(request))


@router.get("", response_model=list[ArticleListItem])
@limiter.limit("60/minute")
def list_articles(request: Request, status: str | None = Query(None, max_length=10),
                  session: Session = Depends(get_session),
                  staff: StaffUser = Depends(get_current_staff_user)):
    """Every article, most recently edited first. Optional status filter."""
    stmt = select(Article).order_by(Article.updated_at.desc())
    if status:
        if status not in ARTICLE_STATUSES:
            raise HTTPException(status_code=400, detail="Unknown status. Use draft, published, or hidden.")
        stmt = stmt.where(Article.status == status)
    names = _names(session)
    return [ArticleListItem(**_fields(a, staff, names)) for a in session.exec(stmt).all()]


@router.get("/{article_id}", response_model=ArticleOut)
@limiter.limit("60/minute")
def get_article(request: Request, article_id: int, session: Session = Depends(get_session),
                staff: StaffUser = Depends(get_current_staff_user)):
    return _out(session, _get(session, article_id), staff)


@router.post("", response_model=ArticleOut, status_code=201)
@limiter.limit("30/minute")
def create_article(request: Request, payload: ArticleCreate, session: Session = Depends(get_session),
                   staff: StaffUser = Depends(get_current_staff_user)):
    """A new article always starts as a draft."""
    now = datetime.utcnow()
    article = Article(title=payload.title, body=payload.body, status="draft",
                      author_staff_id=staff.id, created_at=now,
                      updated_by_staff_id=staff.id, updated_at=now)
    session.add(article)
    session.commit()
    session.refresh(article)
    return _out(session, article, staff)


@router.patch("/{article_id}", response_model=ArticleOut)
@limiter.limit("30/minute")
def update_article(request: Request, article_id: int, payload: ArticleUpdate,
                   session: Session = Depends(get_session),
                   staff: StaffUser = Depends(get_current_staff_user)):
    article = _get(session, article_id)
    if not _can_edit(article, staff):
        if article.status != "draft":
            detail = "Only the Head Attorney can edit an article that has been published or hidden."
        else:
            detail = "You can edit only your own drafts."
        raise HTTPException(status_code=403, detail=detail)

    changed = False
    if payload.title is not None and payload.title != article.title:
        article.title = payload.title
        changed = True
    if payload.body is not None and payload.body != article.body:
        article.body = payload.body
        changed = True
    if not changed:
        return _out(session, article, staff)

    article.updated_by_staff_id = staff.id
    article.updated_at = datetime.utcnow()
    session.add(article)
    session.commit()
    session.refresh(article)
    if article.status != "draft":
        _log(session, request, "article_edited", article, staff)
    return _out(session, article, staff)


@router.post("/{article_id}/publish", response_model=ArticleOut)
@limiter.limit("30/minute")
def publish_article(request: Request, article_id: int, session: Session = Depends(get_session),
                    staff: StaffUser = Depends(require_head_attorney)):
    """Draft or hidden -> published. The first publish date is kept."""
    article = _get(session, article_id)
    if article.status == "published":
        raise HTTPException(status_code=409, detail="This article is already published.")
    article.status = "published"
    if article.published_at is None:
        article.published_at = datetime.utcnow()
        article.published_by_staff_id = staff.id
    session.add(article)
    session.commit()
    session.refresh(article)
    _log(session, request, "article_published", article, staff)
    return _out(session, article, staff)


@router.post("/{article_id}/hide", response_model=ArticleOut)
@limiter.limit("30/minute")
def hide_article(request: Request, article_id: int, session: Session = Depends(get_session),
                 staff: StaffUser = Depends(require_head_attorney)):
    """Published -> hidden (off the public site; can be published again)."""
    article = _get(session, article_id)
    if article.status != "published":
        raise HTTPException(status_code=409, detail="Only a published article can be hidden.")
    article.status = "hidden"
    session.add(article)
    session.commit()
    session.refresh(article)
    _log(session, request, "article_hidden", article, staff)
    return _out(session, article, staff)


# ---------------------------------------------------------------------------
# Public side: what visitors see on Home, articles.html and
# article.html. Drafts and hidden articles are never sent.
# ---------------------------------------------------------------------------

def _plain_text(body: str) -> str:
    """The article as plain text: the marks (## , - , **) removed, lines joined."""
    lines = []
    for line in body.split("\n"):
        line = re.sub(r"^\s*(## |- )", "", line).replace("**", "").strip()
        if line:
            lines.append(line)
    return " ".join(lines)


def _excerpt(body: str) -> str:
    """First words of the article, cut at a word near EXCERPT_LENGTH."""
    text = _plain_text(body)
    if len(text) <= EXCERPT_LENGTH:
        return text
    cut = text[:EXCERPT_LENGTH].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return cut + "…"


@public_router.get("", response_model=list[PublicArticleListItem])
@limiter.limit("60/minute")
def list_published_articles(request: Request, limit: int = Query(100, ge=1, le=100),
                            session: Session = Depends(get_session)):
    """Published articles, newest first (by first publish date)."""
    rows = session.exec(
        select(Article).where(Article.status == "published")
        .order_by(Article.published_at.desc(), Article.id.desc()).limit(limit)
    ).all()
    return [PublicArticleListItem(id=a.id, title=a.title, published_at=a.published_at, excerpt=_excerpt(a.body))
            for a in rows]


@public_router.get("/search-text", response_model=list[PublicArticleSearchItem])
@limiter.limit("60/minute")
def published_articles_search_text(request: Request, session: Session = Depends(get_session)):
    """The text of the published articles, for the site search (js/site-search.js).
    The search runs in the visitor's browser, so what they type is not sent to
    this API. Takes no input; the 100 newest, like the public list."""
    rows = session.exec(
        select(Article).where(Article.status == "published")
        .order_by(Article.published_at.desc(), Article.id.desc()).limit(100)
    ).all()
    return [PublicArticleSearchItem(id=a.id, title=a.title, published_at=a.published_at, text=_plain_text(a.body))
            for a in rows]


@public_router.get("/{article_id}", response_model=PublicArticleOut)
@limiter.limit("60/minute")
def get_published_article(request: Request, article_id: int, session: Session = Depends(get_session)):
    article = session.get(Article, article_id)
    if not article or article.status != "published":
        # Same answer for "never existed", draft and hidden: nothing to guess from.
        raise HTTPException(status_code=404, detail="This article is not available. It may have been taken down.")
    return PublicArticleOut(id=article.id, title=article.title, published_at=article.published_at, body=article.body)
