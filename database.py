import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, BigInteger, JSON
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
    
    # Customisation de l'Embed (Le message du panel)
    embed_title = Column(String, default="Système de Ticket")
    embed_description = Column(Text, default="Cliquez sur le bouton ci-dessous pour ouvrir un ticket.")
    embed_color = Column(String, default="#2ecc71") # Couleur Hexadécimale (Vert par défaut)
    
    # Customisation du Bouton
    button_text = Column(String, default="Ouvrir un ticket")
    button_color = Column(String, default="success") # primary (bleu), secondary (gris), success (vert), danger (rouge)
    button_emoji = Column(String, default="🎫")
    
    # Fonctionnalités
    active_category_id = Column(BigInteger, nullable=True)
    archive_category_id = Column(BigInteger, nullable=True)
    ping_role_id = Column(BigInteger, nullable=True)
    ping_creator = Column(Boolean, default=True)
    max_tickets_per_user = Column(Integer, default=1) # Max tickets ouverts par utilisateur
    intro_message = Column(Text, default="Veuillez patienter, un membre du staff va vous répondre.")
    inactivity_days = Column(Integer, default=3) # Fermeture auto après X jours
    
    # Suivi du déploiement
    deployed_channel_id = Column(BigInteger, nullable=True)
    deployed_message_id = Column(BigInteger, nullable=True)

    # Boutons d'action dans le ticket
    close_button_text = Column(String, default="Fermer le ticket")
    close_button_color = Column(String, default="danger")
    close_button_emoji = Column(String, default="🔒")

    reopen_button_text = Column(String, default="Ré-ouvrir")
    reopen_button_color = Column(String, default="success")
    reopen_button_emoji = Column(String, default="🔓")

    delete_button_text = Column(String, default="Supprimer")
    delete_button_color = Column(String, default="danger")
    delete_button_emoji = Column(String, default="🗑️")

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
    channel_id = Column(BigInteger, unique=True, index=True, nullable=True)
    creator_id = Column(BigInteger)
    status = Column(String, default="open") # open, closed
    
    # 🆕 Sauvegarde des réponses du formulaire en base de données JSON
    form_answers = Column(JSON, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TicketArchive(Base):
    """L'archive d'un ticket (HTML généré)"""
    __tablename__ = 'ticket_archives'
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id', ondelete="CASCADE"), unique=True)
    html_content = Column(Text, nullable=True)
