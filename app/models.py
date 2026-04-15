from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    nickname = Column(String(50), nullable=True)
    google_sub = Column(String(255), unique=True, nullable=True, index=True)
    firebase_uid = Column(String(128), unique=True, nullable=True, index=True)
    password = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    approval_status = Column(String(30), default="approved")

    applications = relationship(
        "Application",
        primaryjoin=lambda: User.id == Application.user_id,
        foreign_keys=lambda: [Application.user_id],
        back_populates="user",
        cascade="all, delete-orphan",
    )
    notifications = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
    schedule_applications = relationship(
        "ScheduleApplication", back_populates="user"
    )


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    event_date = Column(Date, nullable=False, index=True)
    location = Column(String(300), nullable=True)
    description = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    slots = relationship(
        "EventSlot", back_populates="event", cascade="all, delete-orphan"
    )
    applications = relationship(
        "Application", back_populates="event", cascade="all, delete-orphan"
    )


class EventSlot(Base):
    __tablename__ = "event_slots"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    start_time = Column(Time, nullable=False)
    capacity = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, default=True)

    event = relationship("Event", back_populates="slots")
    applications = relationship(
        "Application", back_populates="slot", cascade="all, delete-orphan"
    )


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("event_slots.id"), nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id], back_populates="applications")
    event = relationship("Event", back_populates="applications")
    slot = relationship("EventSlot", back_populates="applications")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(String(255), nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="notifications")


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    location = Column(String(200), nullable=True)
    event_datetime = Column(DateTime, nullable=False)
    recruit_limit = Column(Integer, default=0)
    status = Column(String(20), default="open")
    created_at = Column(DateTime, default=datetime.utcnow)

    applications = relationship(
        "ScheduleApplication", back_populates="schedule", cascade="all, delete-orphan"
    )


class ScheduleApplication(Base):
    __tablename__ = "schedule_applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    applied_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="pending")

    user = relationship("User", back_populates="schedule_applications")
    schedule = relationship("Schedule", back_populates="applications")
