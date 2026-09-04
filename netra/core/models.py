"""Data model for the NETRA platform.

Four concerns, kept separate:
  Camera     — Model 1 registry: identity, geography, capability profile
  Detection  — one observed vehicle at one camera at one instant
  Watchlist  — entities of interest
  Alert      — a Detection matched against a Watchlist entry, with reasons
"""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        JSON, String, Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from netra.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Camera(Base):
    """A camera in the registry (Model 1 foundation)."""
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))

    # geography
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    city: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(64), default="Home Department")

    # technical profile, discovered by probing (the grid supplies none of this)
    codec: Mapped[str | None] = mapped_column(String(16))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    declared_fps: Mapped[str | None] = mapped_column(String(16))
    measured_fps: Mapped[float | None] = mapped_column(Float)

    # endpoints
    rtsp_url: Mapped[str | None] = mapped_column(String(256))
    whep_url: Mapped[str | None] = mapped_column(String(256))
    hls_url: Mapped[str | None] = mapped_column(String(256))

    # capability profile — what this camera can actually deliver
    # one of: anpr | vehicle | person | degraded | unknown
    capability: Mapped[str] = mapped_column(String(16), default="unknown")
    health: Mapped[str] = mapped_column(String(16), default="unknown")  # ok|degraded|down
    mean_luma: Mapped[float | None] = mapped_column(Float)
    plate_px_estimate: Mapped[float | None] = mapped_column(Float)
    capability_note: Mapped[str | None] = mapped_column(Text)

    # live state
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    reconnects: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    detections: Mapped[list["Detection"]] = relationship(back_populates="camera")


class Detection(Base):
    """One vehicle observed at one camera at one instant."""
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)

    # Timing. pts_ms is the stream's own clock and is authoritative for any
    # elapsed-time maths; wall_time is only for display and correlation.
    pts_ms: Mapped[float] = mapped_column(Float)
    wall_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    # timestamp burned into the video by the source camera, when parsed
    scene_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle_class: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    bbox: Mapped[list] = mapped_column(JSON)  # [x1, y1, x2, y2]

    colour: Mapped[str | None] = mapped_column(String(16))
    embedding: Mapped[list | None] = mapped_column(JSON)  # appearance vector

    plate_text: Mapped[str | None] = mapped_column(String(32), index=True)
    plate_conf: Mapped[float | None] = mapped_column(Float)
    plate_chars: Mapped[int | None] = mapped_column(Integer)  # chars actually recovered
    plate_bbox: Mapped[list | None] = mapped_column(JSON)

    evidence_path: Mapped[str | None] = mapped_column(String(256))
    track_id: Mapped[int | None] = mapped_column(Integer, index=True)

    camera: Mapped["Camera"] = relationship(back_populates="detections")


Index("ix_det_cam_time", Detection.camera_id, Detection.wall_time)


class WatchlistEntry(Base):
    """An entity of interest. Schema mirrors VAHAN / eGujCop record shape."""
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(32))  # stolen|wanted|blacklist|suspect|missing
    severity: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high|critical

    owner_name: Mapped[str | None] = mapped_column(String(128))
    vehicle_make: Mapped[str | None] = mapped_column(String(64))
    vehicle_colour: Mapped[str | None] = mapped_column(String(32))
    vehicle_class: Mapped[str | None] = mapped_column(String(16))

    case_ref: Mapped[str | None] = mapped_column(String(64))
    source_db: Mapped[str] = mapped_column(String(32), default="VAHAN")
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Alert(Base):
    """A detection matched to a watchlist entry, with the reasoning preserved."""
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(ForeignKey("detections.id"), index=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlist.id"), index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)

    score: Mapped[float] = mapped_column(Float)
    match_type: Mapped[str] = mapped_column(String(24))  # exact|partial|appearance|fused
    # why the system believes this: {signal: {score, detail}}
    reasons: Mapped[dict] = mapped_column(JSON)

    severity: Mapped[str] = mapped_column(String(16), default="medium")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class AuditLog(Base):
    """Every operator query against the system is recorded."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(256))
    detail: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
