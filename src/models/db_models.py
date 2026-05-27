from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, String, Text

from src.config.database import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True, index=True)
    symbol = Column(String, index=True)
    type = Column(String)
    side = Column(String)
    quantity = Column(Float)
    price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    status = Column(String, default="pending")
    filled_quantity = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Trade(Base):
    __tablename__ = "trades"

    id = Column(String, primary_key=True, index=True)
    order_id = Column(String, index=True, nullable=True)
    symbol = Column(String, index=True)
    side = Column(String)
    quantity = Column(Float)
    price = Column(Float)
    realized_pnl = Column(Float, default=0.0)
    executed_at = Column(DateTime, default=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"

    id = Column(String, primary_key=True, index=True)
    symbol = Column(String, index=True)
    side = Column(String)
    quantity = Column(Float)
    entry_price = Column(Float)
    current_price = Column(Float)
    unrealized_pnl = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    is_open = Column(Boolean, default=True)


class AgentOutput(Base):
    __tablename__ = "agent_outputs"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, index=True)
    agent_name = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    reasoning = Column(Text)
    decision = Column(String)
    confidence = Column(Float)
    input_summary = Column(Text, nullable=True)
