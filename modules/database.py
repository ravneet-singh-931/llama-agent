"""
Database Module - PostgreSQL Integration
Replaces JSON file storage with proper database

Tables:
- users: User accounts and authentication
- conversations: Chat history
- facts: Key-value fact storage
- documents: Uploaded document metadata
- api_keys: API key management
"""

import os
import uuid
from datetime import datetime
from typing import Optional, Dict
from contextlib import asynccontextmanager

# SQLAlchemy for database ORM
from sqlalchemy import (
    String,
    Text,
    DateTime,
    Boolean,
    Integer,
    ForeignKey,
    JSON,
    Index,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.dialects.postgresql import UUID

# For password hashing (bcrypt directly — passlib incompatible with bcrypt 5.x)
import bcrypt as _bcrypt


# ============ BASE MODEL ============


class Base(DeclarativeBase):
    """Base class for all database models"""


# ============ USER MODEL ============


class User(Base):
    """User account model"""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")

    def verify_password(self, password: str) -> bool:
        """Verify password against hash"""
        return _bcrypt.checkpw(
            password.encode("utf-8"),
            self.hashed_password.encode("utf-8"),
        )

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password"""
        salt = _bcrypt.gensalt()
        return _bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# ============ API KEY MODEL ============


class ApiKey(Base):
    """API key model for programmatic access"""
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Rate limiting
    requests_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    requests_today: Mapped[int] = mapped_column(Integer, default=0)

    # Relationship
    user = relationship("User", back_populates="api_keys")


# ============ CONVERSATION MODEL ============


class Conversation(Base):
    """Conversation/chat history model"""
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

    # Index for faster queries
    __table_args__ = (
        Index("idx_conversation_user", "user_id"),
        Index("idx_conversation_created", "created_at"),
    )


# ============ MESSAGE MODEL ============


class Message(Base):
    """Individual message in a conversation"""
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user", "assistant", "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True)  # Tools used, response time, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationship
    conversation = relationship("Conversation", back_populates="messages")

    # Indexes for faster queries
    __table_args__ = (
        Index("idx_message_conversation", "conversation_id"),
        Index("idx_message_created", "created_at"),
    )


# ============ FACT MODEL ============


class Fact(Base):
    """Key-value fact storage"""
    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Composite unique constraint - one fact per key per user
    __table_args__ = (
        Index("idx_fact_user_key", "user_id", "key", unique=True),
    )


# ============ DOCUMENT MODEL ============


class Document(Base):
    """Uploaded document metadata"""
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Index for user's documents
    __table_args__ = (
        Index("idx_document_user", "user_id"),
    )


# ============ DATABASE MANAGER ============


class DatabaseManager:
    """
    Async database manager using SQLAlchemy
    
    Usage:
        db = DatabaseManager()
        async with db.session() as session:
            # Use session for database operations
            result = await session.execute(select(User))
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database manager
        
        Args:
            database_url: PostgreSQL connection URL
                         Format: postgresql+asyncpg://user:pass@host:port/dbname
                         If not provided, reads from DATABASE_URL env var
        """
        if database_url is None:
            from modules.secrets import resolve_database_url
            database_url = resolve_database_url()

        self.database_url = database_url
        self.engine = create_async_engine(
            database_url,
            echo=os.getenv("DB_ECHO", "false").lower() == "true",  # SQL logging
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,  # Auto-reconnect on connection loss
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def session(self):
        """Get a database session"""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def init_db(self):
        """Create all tables (for development)"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables created!")

    async def close(self):
        """Close database connections"""
        await self.engine.dispose()


# ============ HELPER FUNCTIONS ============


def generate_api_key() -> str:
    """Generate a secure API key"""
    return f"sk-{uuid.uuid4().hex}"


def hash_password(password: str) -> str:
    """Hash a password"""
    salt = _bcrypt.gensalt()
    return _bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return _bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# ============ SINGLETON INSTANCE ============

# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """Get or create the database manager singleton"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
