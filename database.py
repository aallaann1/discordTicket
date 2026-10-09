import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, BigInteger
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost/ticket_db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class Panel(Base):
    """Configuration d'un panneau de ticket (Ex: Support Technique)"""
    __tablename__ = 'panels'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    button_text = Column(String, default="Ouvrir un ticket")
    active_category_id = Column(BigInteger, nullable=True)
    archive_category_id = Column(BigInteger, nullable=True)
    ping_role_id = Column(BigInteger, nullable=True)
    intro_message = Column(Text, default="Bonjour, décrivez votre problème :")
    inactivity_days = Column(Integer, default=3) # Fermeture auto après X jours d'inactivité

class FormField(Base):
    """Les questions posées dans le formulaire (Modal)"""
    __tablename__ = 'form_fields'
    id = Column(Integer, primary_key=True, index=True)
    panel_id = Column(Integer, ForeignKey('panels.id'))
    label = Column(String)
    is_paragraph = Column(Boolean, default=False)
    required = Column(Boolean, default=True)
    order = Column(Integer, default=0)

class Ticket(Base):
    """Un ticket ouvert ou fermé"""
    __tablename__ = 'tickets'
    id = Column(Integer, primary_key=True, index=True)
    panel_id = Column(Integer, ForeignKey('panels.id'))
    channel_id = Column(BigInteger, unique=True, index=True)
    creator_id = Column(BigInteger)
    status = Column(String, default="open") # open, closed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TicketArchive(Base):
    """L'archive d'un ticket (HTML généré + JSON)"""
    __tablename__ = 'ticket_archives'
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id', ondelete="CASCADE"), unique=True)
    html_content = Column(Text, nullable=True) # Transcript visuel
