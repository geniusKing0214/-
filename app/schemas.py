from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class ScheduleOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    event_datetime: datetime
    recruit_limit: int
    status: str

    class Config:
        from_attributes = True


class ScheduleApplicationOut(BaseModel):
    id: int
    user_id: int
    schedule_id: int
    status: str
    applied_at: datetime

    class Config:
        from_attributes = True