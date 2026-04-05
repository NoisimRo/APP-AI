"""Document Comments API — Inline comments for collaborative review.

Supports text anchoring (character offsets), resolution tracking,
and per-document comment threads.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_optional_user
from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import DocumentComment, DocumentGenerat, User

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

class CommentCreate(BaseModel):
    text: str = Field(..., min_length=1)
    anchor_start: int | None = None
    anchor_end: int | None = None
    anchor_text: str | None = None


class CommentUpdate(BaseModel):
    text: str | None = Field(None, min_length=1)


class CommentResponse(BaseModel):
    id: str
    document_id: str
    user_id: str | None
    user_name: str | None
    anchor_start: int | None
    anchor_end: int | None
    anchor_text: str | None
    text: str
    resolved: bool
    resolved_by: str | None
    resolved_at: str | None
    created_at: str
    updated_at: str


class CommentStats(BaseModel):
    total: int
    resolved: int
    unresolved: int


def _comment_to_response(c: DocumentComment, user_name: str | None = None) -> CommentResponse:
    return CommentResponse(
        id=c.id,
        document_id=c.document_id,
        user_id=c.user_id,
        user_name=user_name,
        anchor_start=c.anchor_start,
        anchor_end=c.anchor_end,
        anchor_text=c.anchor_text,
        text=c.text,
        resolved=c.resolved,
        resolved_by=c.resolved_by,
        resolved_at=c.resolved_at.isoformat() if c.resolved_at else None,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/{document_id}/comments", response_model=CommentResponse)
async def create_comment(
    document_id: str,
    request: CommentCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Add a comment to a document."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Verify document exists
    doc_result = await session.execute(
        select(DocumentGenerat).where(DocumentGenerat.id == document_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document negăsit")

    comment = DocumentComment(
        document_id=document_id,
        user_id=user.id,
        text=request.text,
        anchor_start=request.anchor_start,
        anchor_end=request.anchor_end,
        anchor_text=request.anchor_text,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)

    logger.info("comment_created", comment_id=comment.id, document_id=document_id)
    return _comment_to_response(comment, user_name=user.nume or user.email)


@router.get("/{document_id}/comments", response_model=list[CommentResponse])
async def list_comments(
    document_id: str,
    resolved: bool | None = Query(None, description="Filter by resolved status"),
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """List comments on a document."""
    if not is_db_available():
        return []

    # Verify document exists
    doc_result = await session.execute(
        select(DocumentGenerat).where(DocumentGenerat.id == document_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document negăsit")

    query = (
        select(DocumentComment)
        .where(DocumentComment.document_id == document_id)
        .order_by(DocumentComment.anchor_start.asc().nullslast(), DocumentComment.created_at.asc())
    )
    if resolved is not None:
        query = query.where(DocumentComment.resolved == resolved)

    result = await session.execute(query)
    comments = result.scalars().all()

    # Fetch user names in batch
    user_ids = {c.user_id for c in comments if c.user_id}
    user_names = {}
    if user_ids:
        users_result = await session.execute(
            select(User).where(User.id.in_(user_ids))
        )
        for u in users_result.scalars():
            user_names[u.id] = u.nume or u.email

    return [_comment_to_response(c, user_name=user_names.get(c.user_id)) for c in comments]


@router.get("/{document_id}/comments/stats", response_model=CommentStats)
async def comment_stats(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
):
    """Get comment statistics for a document."""
    if not is_db_available():
        return CommentStats(total=0, resolved=0, unresolved=0)

    total_result = await session.execute(
        select(func.count()).where(DocumentComment.document_id == document_id)
    )
    total = total_result.scalar() or 0

    resolved_result = await session.execute(
        select(func.count()).where(
            DocumentComment.document_id == document_id,
            DocumentComment.resolved == True,
        )
    )
    resolved = resolved_result.scalar() or 0

    return CommentStats(total=total, resolved=resolved, unresolved=total - resolved)


@router.put("/{document_id}/comments/{comment_id}", response_model=CommentResponse)
async def update_comment(
    document_id: str,
    comment_id: str,
    request: CommentUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Update a comment's text."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(DocumentComment).where(
            DocumentComment.id == comment_id,
            DocumentComment.document_id == document_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(404, "Comentariu negăsit")

    if comment.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest comentariu")

    if request.text is not None:
        comment.text = request.text

    await session.commit()
    await session.refresh(comment)

    logger.info("comment_updated", comment_id=comment_id)
    return _comment_to_response(comment, user_name=user.nume or user.email)


@router.post("/{document_id}/comments/{comment_id}/resolve", response_model=CommentResponse)
async def resolve_comment(
    document_id: str,
    comment_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Mark a comment as resolved."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(DocumentComment).where(
            DocumentComment.id == comment_id,
            DocumentComment.document_id == document_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(404, "Comentariu negăsit")

    comment.resolved = True
    comment.resolved_by = user.id
    comment.resolved_at = datetime.utcnow()

    await session.commit()
    await session.refresh(comment)

    logger.info("comment_resolved", comment_id=comment_id)
    return _comment_to_response(comment, user_name=user.nume or user.email)


@router.post("/{document_id}/comments/{comment_id}/unresolve", response_model=CommentResponse)
async def unresolve_comment(
    document_id: str,
    comment_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Mark a comment as unresolved."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(DocumentComment).where(
            DocumentComment.id == comment_id,
            DocumentComment.document_id == document_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(404, "Comentariu negăsit")

    comment.resolved = False
    comment.resolved_by = None
    comment.resolved_at = None

    await session.commit()
    await session.refresh(comment)

    logger.info("comment_unresolved", comment_id=comment_id)
    return _comment_to_response(comment, user_name=user.nume or user.email)


@router.delete("/{document_id}/comments/{comment_id}")
async def delete_comment(
    document_id: str,
    comment_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Delete a comment."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(DocumentComment).where(
            DocumentComment.id == comment_id,
            DocumentComment.document_id == document_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(404, "Comentariu negăsit")

    if comment.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la acest comentariu")

    await session.delete(comment)
    await session.commit()

    logger.info("comment_deleted", comment_id=comment_id)
    return {"status": "deleted"}
