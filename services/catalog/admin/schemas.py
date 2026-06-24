"""Admin-only request bodies (Appendix C) -- catalog has no customer-
facing writes, so every create/update body in this service belongs to
the admin side.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class MovieCreate(BaseModel):
    title: str
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    language: Optional[str] = None
    poster_asset_id: Optional[UUID] = None


class MovieUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    language: Optional[str] = None
    poster_asset_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class MovieReleaseCreate(BaseModel):
    city_id: UUID
    release_date: date
    planned_end_date: Optional[date] = None
    actual_end_date: Optional[date] = None


class MovieReleaseUpdate(BaseModel):
    release_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    actual_end_date: Optional[date] = None
