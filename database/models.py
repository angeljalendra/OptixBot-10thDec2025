from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, JSON, Enum, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime

Base = declarative_base()


class Trade(Base):
    __tablename__ = 'trades'

    id = Column(String, primary_key=True)
    order_id = Column(String, unique=True, index=True)
    symbol = Column(String, index=True)
    strike = Column(Float)
    option_type = Column(String)
    expiry = Column(String)
    entry_time = Column(DateTime, default=datetime.utcnow)
    entry_price = Column(Float)
    quantity = Column(Integer)
    lot_size = Column(Integer)
    exit_time = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    exit_reason = Column(String, nullable=True)
    premium_paid = Column(Float)
    premium_received = Column(Float, nullable=True)
    pnl = Column(Float, default=0)
    pnl_pct = Column(Float, default=0)
    max_profit = Column(Float, default=0)
    max_loss = Column(Float, default=0)
    entry_delta = Column(Float, nullable=True)
    entry_theta = Column(Float, nullable=True)
    entry_vega = Column(Float, nullable=True)
    current_delta = Column(Float, nullable=True)
    strategy = Column(String, index=True)
    strategy_confidence = Column(Float)
    status = Column(String, default='OPEN', index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailyMetric(Base):
    __tablename__ = 'daily_metrics'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, unique=True, index=True)
    daily_pnl = Column(Float)
    daily_pnl_pct = Column(Float)
    trades_executed = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    win_rate = Column(Float)
    strategy_breakdown = Column(JSON)
    max_intraday_loss = Column(Float, default=0)
    max_intraday_gain = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


def get_db_engine(connection_string: str):
    return create_engine(connection_string, echo=False)


def get_session(engine):
    Session = sessionmaker(bind=engine)
    return Session()