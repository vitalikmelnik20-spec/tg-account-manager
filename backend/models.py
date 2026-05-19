from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Boolean, Text
from datetime import datetime, timezone
from backend.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    session_string = Column(Text, nullable=False)
    api_id = Column(String, nullable=False)
    api_hash = Column(String, nullable=False)
    phone = Column(String)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    bio = Column(Text)
    pyrogram_session = Column(Text, nullable=True)
    twofa_password = Column(String, nullable=True)
    is_connected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class OTPCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, nullable=False)
    code = Column(String)
    code_type = Column(String, default="login")  # login | 2fa
    message_text = Column(Text)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReactChannel(Base):
    __tablename__ = "react_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(BigInteger, nullable=False)
    access_hash = Column(BigInteger, nullable=False)
    username = Column(String, nullable=True)
    title = Column(String, nullable=False)
    reaction = Column(String, default="👍", nullable=False)
    last_msg_id = Column(Integer, default=0, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MonitoredChannel(Base):
    __tablename__ = "monitored_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    access_hash = Column(BigInteger, nullable=False)
    username = Column(String, nullable=True)
    title = Column(String, nullable=False)
    last_msg_id = Column(Integer, default=0, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CommentReactChannel(Base):
    __tablename__ = "comment_react_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(BigInteger, nullable=False)
    access_hash = Column(BigInteger, nullable=False)
    discussion_id = Column(BigInteger, nullable=True)
    discussion_hash = Column(BigInteger, nullable=True)
    username = Column(String, nullable=True)
    title = Column(String, nullable=False)
    reaction = Column(String, default="👍", nullable=False)
    last_comment_id = Column(Integer, default=0, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
