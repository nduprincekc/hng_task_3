from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Index, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    gender = Column(String(50), nullable=False)
    gender_probability = Column(Float, default=0.0)
    age = Column(Integer, nullable=False)
    age_group = Column(String(50), nullable=True)
    country_id = Column(String(10), nullable=True)
    country_name = Column(String(100), nullable=True)
    country_probability = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_profiles_country_id_gender", "country_id", "gender"),
        Index("ix_profiles_country_id_gender_age", "country_id", "gender", "age"),
        Index("ix_profiles_country_name_gender", "country_name", "gender"),
        Index("ix_profiles_gender_age", "gender", "age"),
        Index("ix_profiles_age_group", "age_group"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    github_id = Column(String(100), unique=True, nullable=False)
    username = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    avatar_url = Column(Text, nullable=True)
    role = Column(String(50), default="analyst")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), nullable=False, index=True)
    token_hash = Column(String(255), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())