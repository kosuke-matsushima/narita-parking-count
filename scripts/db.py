"""SQLAlchemy + GeoAlchemy2 モデル定義"""
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from scripts.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"
    id = Column(Integer, primary_key=True)
    captured_at = Column(DateTime(timezone=True))
    analyzed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    image_source = Column(Text)
    image_path = Column(Text)
    bbox = Column(Geometry("POLYGON", srid=4326))
    resolution_m = Column(Float)
    model_name = Column(Text)
    model_version = Column(Text)
    total_detected = Column(Integer, default=0)
    notes = Column(Text)
    vehicles = relationship("DetectedVehicle", back_populates="session", cascade="all, delete-orphan")


class DetectedVehicle(Base):
    __tablename__ = "detected_vehicles"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False)
    location = Column(Geometry("POINT", srid=4326), nullable=False)
    bbox_pixel = Column(JSONB)
    confidence = Column(Float, nullable=False)
    class_label = Column(String, default="car")
    is_correct = Column(Boolean)
    verified_at = Column(DateTime(timezone=True))
    verified_by = Column(Text)
    notes = Column(Text)
    session = relationship("AnalysisSession", back_populates="vehicles")


class ParkingArea(Base):
    __tablename__ = "parking_areas"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    area = Column(Geometry("POLYGON", srid=4326), nullable=False)
    capacity = Column(Integer)
    notes = Column(Text)


engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Session:
    return SessionLocal()
