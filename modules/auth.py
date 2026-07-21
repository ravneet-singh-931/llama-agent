"""
Authentication Module - JWT Tokens & API Keys

Features:
- User registration and login
- JWT token generation and validation
- API key authentication
- Password hashing with bcrypt
- Role-based access control
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from functools import wraps

# JWT for token-based auth
from jose import JWTError, jwt
import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader

# Database
from modules.database import (
    get_db_manager,
    User,
    ApiKey,
    hash_password,
    verify_password,
    generate_api_key,
)

# ============ CONFIGURATION ============

# JWT settings (from environment or defaults)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))

# Security schemes for FastAPI
bearer_scheme = HTTPBearer()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ============ PASSWORD UTILITIES ============


class PasswordManager:
    """Secure password hashing and verification using bcrypt directly"""

    def hash(self, password: str) -> str:
        """Hash a password"""
        salt = _bcrypt.gensalt()
        return _bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password"""
        return _bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )

    def check_strength(self, password: str) -> Dict[str, Any]:
        """Check password strength"""
        checks = {
            "length": len(password) >= 8,
            "uppercase": any(c.isupper() for c in password),
            "lowercase": any(c.islower() for c in password),
            "digits": any(c.isdigit() for c in password),
            "special": any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password),
        }
        score = sum(checks.values())
        return {
            "valid": score >= 4,  # At least 4/5 checks
            "score": score,
            "checks": checks,
            "message": "Strong" if score >= 4 else "Weak - add more character types"
        }


password_manager = PasswordManager()

# ============ JWT TOKEN UTILITIES ============


class JWTManager:
    """JWT token creation and validation"""

    def __init__(
        self,
        secret_key: str = SECRET_KEY,
        algorithm: str = ALGORITHM,
        access_expire_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_expire_days: int = REFRESH_TOKEN_EXPIRE_DAYS,
    ):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_expire_minutes = access_expire_minutes
        self.refresh_expire_days = refresh_expire_days

    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """
        Create access token
        
        Args:
            data: Data to encode in token (e.g., {"sub": user_id})
            expires_delta: Custom expiration time
        
        Returns:
            JWT token string
        """
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.access_expire_minutes)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        })
        
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, data: Dict[str, Any]) -> str:
        """Create refresh token (longer expiration)"""
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=self.refresh_expire_days)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh",
        })
        
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify and decode a token
        
        Returns:
            Token payload if valid, None otherwise
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError:
            return None

    def get_user_id_from_token(self, token: str) -> Optional[str]:
        """Extract user ID from token"""
        payload = self.verify_token(token)
        if payload:
            return payload.get("sub")
        return None


jwt_manager = JWTManager()

# ============ USER MANAGEMENT ============


class UserManager:
    """User registration, login, and management"""

    def __init__(self):
        self.db = get_db_manager()

    async def register(
        self,
        username: str,
        email: str,
        password: str,
        full_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a new user
        
        Returns:
            User data and tokens
        """
        # Validate password strength
        strength = password_manager.check_strength(password)
        if not strength["valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Password too weak: {strength['message']}"
            )

        async with self.db.session() as session:
            # Check if username exists
            from sqlalchemy import select
            existing = await session.execute(
                select(User).where(
                    (User.username == username) | (User.email == email)
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username or email already registered"
                )

            # Create user
            user = User(
                username=username,
                email=email,
                hashed_password=password_manager.hash(password),
                full_name=full_name,
            )
            session.add(user)
            await session.flush()  # Get user.id

            # Generate tokens
            access_token = jwt_manager.create_access_token(data={"sub": str(user.id)})
            refresh_token = jwt_manager.create_refresh_token(data={"sub": str(user.id)})

            return {
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "full_name": user.full_name,
                },
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
            }

    async def login(self, username: str, password: str) -> Dict[str, Any]:
        """
        Login with username/email and password
        
        Returns:
            User data and tokens
        """
        from sqlalchemy import select

        async with self.db.session() as session:
            # Find user by username or email
            result = await session.execute(
                select(User).where(
                    (User.username == username) | (User.email == username)
                )
            )
            user = result.scalar_one_or_none()

            if not user or not user.verify_password(password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid credentials"
                )

            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account is disabled"
                )

            # Generate tokens
            access_token = jwt_manager.create_access_token(data={"sub": str(user.id)})
            refresh_token = jwt_manager.create_refresh_token(data={"sub": str(user.id)})

            return {
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "full_name": user.full_name,
                },
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
            }

    async def refresh_token(self, refresh_token: str) -> Dict[str, str]:
        """Get new access token using refresh token"""
        payload = jwt_manager.verify_token(refresh_token)
        
        if not payload or payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )

        user_id = payload.get("sub")
        new_access_token = jwt_manager.create_access_token(data={"sub": user_id})

        return {
            "access_token": new_access_token,
            "token_type": "bearer",
        }

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        from sqlalchemy import select
        from modules.database import get_db_manager

        db = get_db_manager()
        async with db.session() as session:
            result = await session.execute(
                select(User).where(User.id == uuid.UUID(user_id))
            )
            user = result.scalar_one_or_none()
            
            if user:
                return {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "full_name": user.full_name,
                    "is_active": user.is_active,
                    "is_admin": user.is_admin,
                    "created_at": user.created_at.isoformat(),
                }
            return None

    async def create_api_key(
        self,
        user_id: str,
        name: str,
        expires_days: int = 90,
    ) -> Dict[str, Any]:
        """Create an API key for a user"""
        from modules.database import get_db_manager

        db = get_db_manager()
        async with db.session() as session:
            api_key = ApiKey(
                key=generate_api_key(),
                name=name,
                user_id=uuid.UUID(user_id),
                expires_at=datetime.utcnow() + timedelta(days=expires_days),
            )
            session.add(api_key)
            await session.flush()

            return {
                "key": api_key.key,
                "name": api_key.name,
                "expires_at": api_key.expires_at.isoformat(),
            }

    async def validate_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """Validate an API key and return user info"""
        from sqlalchemy import select
        from modules.database import get_db_manager

        db = get_db_manager()
        async with db.session() as session:
            result = await session.execute(
                select(ApiKey).where(
                    ApiKey.key == api_key,
                    ApiKey.is_active == True,
                )
            )
            key_record = result.scalar_one_or_none()

            if not key_record:
                return None

            # Check expiration
            if key_record.expires_at and key_record.expires_at < datetime.utcnow():
                return None

            # Update last used
            key_record.last_used = datetime.utcnow()

            # Get user
            user_result = await session.execute(
                select(User).where(User.id == key_record.user_id)
            )
            user = user_result.scalar_one_or_none()

            if user and user.is_active:
                return {
                    "user_id": str(user.id),
                    "username": user.username,
                    "api_key_name": key_record.name,
                }
            return None


user_manager = UserManager()

# ============ FASTAPI DEPENDENCIES ============


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """
    FastAPI dependency to get current authenticated user
    
    Usage:
        @app.get("/protected")
        async def protected_route(user = Depends(get_current_user)):
            return {"user": user}
    """
    token = credentials.credentials
    payload = jwt_manager.verify_token(token)
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return user


async def get_current_user_from_api_key(
    api_key: Optional[str] = Depends(api_key_header),
) -> Optional[Dict[str, Any]]:
    """
    FastAPI dependency for API key authentication
    
    Usage:
        @app.get("/api-key-auth")
        async def api_key_route(user = Depends(get_current_user_from_api_key)):
            return {"user": user}
    """
    if api_key:
        user = await user_manager.validate_api_key(api_key)
        if user:
            return user
    return None


async def require_admin(user = Depends(get_current_user)) -> Dict[str, Any]:
    """Require admin role"""
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user


# ============ DUAL AUTH (JWT OR API KEY) ============


async def authenticate_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key: Optional[str] = Depends(api_key_header),
) -> Dict[str, Any]:
    """
    Try JWT first, then API key
    Allows clients to use either authentication method
    """
    # Try JWT token
    if credentials:
        payload = jwt_manager.verify_token(credentials.credentials)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                user = await user_manager.get_user(user_id)
                if user:
                    return user

    # Try API key
    if api_key:
        user = await user_manager.validate_api_key(api_key)
        if user:
            return user

    # Neither worked
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials - provide JWT token or API key"
    )


async def optional_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key: Optional[str] = Depends(api_key_header),
) -> Optional[Dict[str, Any]]:
    """
    Optional authentication - returns None if not authenticated
    Use this for endpoints that work with or without auth
    """
    # Try JWT token
    if credentials:
        payload = jwt_manager.verify_token(credentials.credentials)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                user = await user_manager.get_user(user_id)
                if user:
                    return user

    # Try API key
    if api_key:
        user = await user_manager.validate_api_key(api_key)
        if user:
            return user

    # Not authenticated - that's OK
    return None
