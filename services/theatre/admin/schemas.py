"""Admin-only request bodies (Appendix C) -- theatre service has no
customer-facing writes, so every create/update/patch body in this
service belongs to the admin side.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class TheatreCreate(BaseModel):
    city_id: UUID
    name: str
    address: Optional[str] = None


class TheatreUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None


class ScreenCreate(BaseModel):
    name: str


class ScreenUpdate(BaseModel):
    name: Optional[str] = None


class SeatCreate(BaseModel):
    id: UUID
    label: str
    x: float
    y: float
    seat_type: str
    price_multiplier: float


class SeatLayoutDraftCreate(BaseModel):
    screen_id: UUID
    name: str
    seats: list[SeatCreate]


class SeatPatch(BaseModel):
    label: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    seat_type: Optional[str] = None
    price_multiplier: Optional[float] = None
    is_active: Optional[bool] = None


class BulkSeatPatch(SeatPatch):
    seat_ids: list[UUID]


class CloneRequest(BaseModel):
    target_screen_id: UUID


class ShowtimeCreate(BaseModel):
    movie_id: UUID
    movie_title: str
    screen_id: UUID
    start_time: datetime
    is_high_demand: bool = False
    base_price: float


class ShowtimeUpdate(BaseModel):
    movie_id: Optional[UUID] = None
    movie_title: Optional[str] = None
    start_time: Optional[datetime] = None
    is_high_demand: Optional[bool] = None
    base_price: Optional[float] = None
