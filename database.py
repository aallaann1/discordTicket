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

    close_reason_button_text = Column(String, default="Fermer avec raison")
    close_reason_button_color = Column(String, default="secondary")
    close_reason_button_emoji = Column(String, default="📝")

    reopen_button_text = Column(String, default="Ré-ouvrir")
    reopen_button_color = Column(String, default="success")
    reopen_button_emoji = Column(String, default="🔓")

    delete_button_text = Column(String, default="Supprimer")
    delete_button_color = Column(String, default="danger")
    delete_button_emoji = Column(String, default="🗑️")

    # Permissions de fermeture pour le créateur & Transcript MP
    creator_can_close = Column(Boolean, default=True)
    creator_can_close_with_reason = Column(Boolean, default=False)
    dm_transcript_on_close = Column(Boolean, default=True)

    # Titre personnalisé de la modale (questionnaire)
    modal_title = Column(String, nullable=True)

class FormField(Base):
    """Les questions posées dans le formulaire (Modal Discord)"""
    __tablename__ = 'form_fields'
    id = Column(Integer, primary_key=True, index=True)
    panel_id = Column(Integer, ForeignKey('panels.id'))
    label = Column(String)
    # Type de champ : 'short_text' (texte court), 'paragraph' (texte long), 'select' (liste déroulante),
    # 'user_select', 'role_select', 'channel_select', 'file_upload' (Component Type 19)
    field_type = Column(String, default="short_text")
    is_paragraph = Column(Boolean, default=False)
    description = Column(String, nullable=True)       # Sous-texte d'aide sous le label (max 100 car.)
    placeholder = Column(String, nullable=True)       # Texte indicatif dans le champ / menu (max 100-150 car.)
    required = Column(Boolean, default=True)
    min_length = Column(Integer, nullable=True)       # Longueur min (texte)
    max_length = Column(Integer, nullable=True)       # Longueur max (texte, ex: 1000)
    default_value = Column(Text, nullable=True)       # Valeur pré-remplie (texte)
    options = Column(JSON, nullable=True)             # Liste des choix pour 'select': [{label, value, description, emoji, default}]
    min_values = Column(Integer, default=1)           # Nombre min de choix / fichiers
    max_values = Column(Integer, default=1)           # Nombre max de choix / fichiers
    order = Column(Integer, default=0)

class Ticket(Base):
    """Un ticket ouvert ou fermé"""
    __tablename__ = 'tickets'
    id = Column(Integer, primary_key=True, index=True)
    panel_id = Column(Integer, ForeignKey('panels.id'))
    channel_id = Column(BigInteger, unique=True, index=True, nullable=True)
    creator_id = Column(BigInteger)
    creator_username = Column(String, nullable=True)
    ticket_name = Column(String, nullable=True, index=True)
    user_ticket_number = Column(Integer, nullable=True)
    status = Column(String, default="open") # open, closed
    
    # 🆕 Sauvegarde des réponses du formulaire en base de données JSON
    form_answers = Column(JSON, nullable=True)

    # Motif de fermeture et auteur de la fermeture
    close_reason = Column(Text, nullable=True)
    closed_by_username = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TicketArchive(Base):
    """L'archive d'un ticket (HTML généré)"""
    __tablename__ = 'ticket_archives'
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id', ondelete="CASCADE"), unique=True)
    html_content = Column(Text, nullable=True)
