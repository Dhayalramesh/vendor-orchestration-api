"""
db.py

Database models for the compliance case-management system: a Case
represents one customer being onboarded/verified, and each Case has
multiple VendorCheck records -- one per vendor call (identity, sanctions,
document) with its normalized result.

Uses SQLAlchemy so the same models run against SQLite locally (for the
portfolio demo, no external dependency) or PostgreSQL in production by
just changing the connection string -- the actual pattern used in real
services, not a toy substitute.
"""
import datetime
import enum
import os

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./orchestration.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class CaseStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    passed = "passed"
    failed = "failed"
    review = "review"


class CheckStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    timeout = "timeout"


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, index=True)
    customer_country = Column(String)
    status = Column(Enum(CaseStatus), default=CaseStatus.pending)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                         onupdate=datetime.datetime.utcnow)

    checks = relationship("VendorCheck", back_populates="case", cascade="all, delete-orphan")


class VendorCheck(Base):
    __tablename__ = "vendor_checks"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    vendor_name = Column(String)
    check_type = Column(String)  # identity, sanctions, document
    status = Column(Enum(CheckStatus), default=CheckStatus.pending)
    raw_response = Column(Text, nullable=True)
    normalized_result = Column(String, nullable=True)  # pass / fail / manual_review
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("Case", back_populates="checks")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()