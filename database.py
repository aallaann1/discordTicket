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
    embed_title_url = Column(String, nullable=True) # Lien hypertexte sur le titre
    embed_description = Column(Text, default="Cliquez sur le bouton ci-dessous pour ouvrir un ticket.")
    embed_color = Column(String, default="#2ecc71") # Couleur Hexadécimale (Vert par défaut)
    
    # Auteur & Images de l'Embed Principal
    embed_author_name = Column(String, nullable=True)
    embed_author_icon_url = Column(String, nullable=True)
    embed_author_url = Column(String, nullable=True)
    embed_thumbnail_url = Column(String, nullable=True) # Miniature en haut à droite
    embed_image_url = Column(String, nullable=True) # Grande bannière en bas
    embed_footer_text = Column(String, default="Système de Ticketing")
    embed_footer_icon_url = Column(String, nullable=True)
    embed_show_timestamp = Column(Boolean, default=False)
    
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
    
    # Premier message envoyé à l'ouverture du ticket (Embed d'accueil)
    intro_embed_title = Column(String, default="Bienvenue dans votre ticket")
    intro_message = Column(Text, default="Veuillez patienter, un membre du staff va vous répondre.")
    intro_embed_color = Column(String, nullable=True) # Hérite de embed_color si vide
    intro_thumbnail_url = Column(String, nullable=True) # Miniature pour l'accueil du ticket
    intro_image_url = Column(String, nullable=True) # Grande bannière pour l'accueil du ticket
    intro_footer_text = Column(String, nullable=True) # Pied de page personnalisé du ticket
    
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

    # Customisation de l'Embed MP envoyé au créateur lors de la fermeture
    dm_embed_title = Column(String, default="🔒 Ticket fermé — {ticket_name}")
    dm_embed_title_url = Column(String, nullable=True)
    dm_embed_description = Column(Text, default="Votre ticket **{ticket_name}** a été clôturé par **{closed_by}**.\nVous trouverez ci-joint l'archive complète de votre conversation.")
    dm_embed_color = Column(String, nullable=True)
    dm_thumbnail_url = Column(String, nullable=True)
    dm_image_url = Column(String, nullable=True)
    dm_footer_text = Column(String, default="Téléchargez et ouvrez le fichier .html pour relire la conversation.")
    dm_footer_icon_url = Column(String, nullable=True)
    dm_show_timestamp = Column(Boolean, default=True)

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

class InfoEmbed(Base):
    """Message d'information / guide en Embed déployé dans un salon (ex: Comment vendre)"""
    __tablename__ = 'info_embeds'
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, default="how_to_sell")
    title = Column(String, default="Comment vendre votre collection")
    
    # Customisation de l'Embed
    embed_title = Column(String, default="📖 Comment vendre votre collection ?")
    embed_title_url = Column(String, nullable=True)
    embed_description = Column(Text, default="Voici toutes les étapes pour nous vendre votre collection...")
    embed_color = Column(String, default="#5865F2")
    
    embed_author_name = Column(String, nullable=True)
    embed_author_icon_url = Column(String, nullable=True)
    embed_author_url = Column(String, nullable=True)
    embed_thumbnail_url = Column(String, nullable=True)
    embed_image_url = Column(String, nullable=True)
    embed_footer_text = Column(String, default="BRB Collection • Guide Officiel")
    embed_footer_icon_url = Column(String, nullable=True)
    embed_show_timestamp = Column(Boolean, default=False)
    
    # Salon cible et message déployé sur Discord
    channel_id = Column(BigInteger, nullable=True)
    deployed_message_id = Column(BigInteger, nullable=True)
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Announcement(Base):
    """Annonce ou message informatif diffusé dans un salon Discord"""
    __tablename__ = 'announcements'
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, default="Nouvelle Annonce")  # Nom interne de l'annonce pour l'historique
    channel_id = Column(BigInteger, nullable=False)     # Salon Discord cible
    message_id = Column(BigInteger, nullable=True)      # ID du message sur Discord
    
    # Message hors embed (optionnel pour mention @everyone, @here ou rôle)
    content = Column(Text, nullable=True)
    
    # Customisation de l'Embed
    embed_title = Column(String, nullable=True)
    embed_title_url = Column(String, nullable=True)
    embed_description = Column(Text, nullable=True)
    embed_color = Column(String, default="#5865F2")
    embed_author_name = Column(String, nullable=True)
    embed_author_icon_url = Column(String, nullable=True)
    embed_author_url = Column(String, nullable=True)
    embed_thumbnail_url = Column(String, nullable=True)
    embed_image_url = Column(String, nullable=True)
    embed_footer_text = Column(String, nullable=True)
    embed_footer_icon_url = Column(String, nullable=True)
    embed_show_timestamp = Column(Boolean, default=True)
    
    # Statut : 'sent' (envoyé), 'edited' (modifié), 'deleted' (supprimé de Discord)
    status = Column(String, default="sent")
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


