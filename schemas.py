from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class SelectOptionSchema(BaseModel):
    label: str
    value: Optional[str] = None
    description: Optional[str] = None
    emoji: Optional[str] = None
    default: Optional[bool] = False

class PanelCreate(BaseModel):
    name: str
    embed_title: str
    embed_description: str
    embed_color: str
    button_text: str
    button_emoji: str
    button_color: str
    active_category_id: Optional[str] = None
    archive_category_id: Optional[str] = None
    ping_role_id: Optional[str] = None
    ping_creator: Optional[bool] = True
    max_tickets_per_user: Optional[int] = 1
    intro_message: str
    inactivity_days: int
    modal_title: Optional[str] = None
    
    # Customisation avancée de l'Embed Principal
    embed_title_url: Optional[str] = None
    embed_author_name: Optional[str] = None
    embed_author_icon_url: Optional[str] = None
    embed_author_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_footer_text: Optional[str] = "Système de Ticketing"
    embed_footer_icon_url: Optional[str] = None
    embed_show_timestamp: Optional[bool] = False

    # Customisation de l'Embed de Bienvenue du Ticket
    intro_embed_title: Optional[str] = "Bienvenue dans votre ticket"
    intro_embed_color: Optional[str] = None
    intro_thumbnail_url: Optional[str] = None
    intro_image_url: Optional[str] = None
    intro_footer_text: Optional[str] = None

    # Action buttons customization
    close_button_text: Optional[str] = "Fermer le ticket"
    close_button_color: Optional[str] = "danger"
    close_button_emoji: Optional[str] = "🔒"

    close_reason_button_text: Optional[str] = "Fermer avec raison"
    close_reason_button_color: Optional[str] = "secondary"
    close_reason_button_emoji: Optional[str] = "📝"

    reopen_button_text: Optional[str] = "Ré-ouvrir"
    reopen_button_color: Optional[str] = "success"
    reopen_button_emoji: Optional[str] = "🔓"

    delete_button_text: Optional[str] = "Supprimer"
    delete_button_color: Optional[str] = "danger"
    delete_button_emoji: Optional[str] = "🗑️"

    # Creator permissions & DM transcript
    creator_can_close: Optional[bool] = True
    creator_can_close_with_reason: Optional[bool] = False
    dm_transcript_on_close: Optional[bool] = True

    # Customisation de l'Embed MP envoyé au créateur lors de la fermeture
    dm_embed_title: Optional[str] = "🔒 Ticket fermé — {ticket_name}"
    dm_embed_title_url: Optional[str] = None
    dm_embed_description: Optional[str] = "Votre ticket **{ticket_name}** a été clôturé par **{closed_by}**.\nVous trouverez ci-joint l'archive complète de votre conversation."
    dm_embed_color: Optional[str] = None
    dm_thumbnail_url: Optional[str] = None
    dm_image_url: Optional[str] = None
    dm_footer_text: Optional[str] = "Téléchargez et ouvrez le fichier .html pour relire la conversation."
    dm_footer_icon_url: Optional[str] = None
    dm_show_timestamp: Optional[bool] = True

class FieldCreate(BaseModel):
    label: str
    field_type: Optional[str] = "short_text" # short_text, paragraph, select, user_select, role_select, channel_select, file_upload
    is_paragraph: bool = False
    description: Optional[str] = None
    placeholder: Optional[str] = None
    required: bool = True
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    default_value: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None
    min_values: Optional[int] = 1
    max_values: Optional[int] = 1
    order: int = 0

class BotProfileUpdate(BaseModel):
    username: Optional[str] = None
    avatar_base64: Optional[str] = None

class InfoEmbedUpdate(BaseModel):
    key: Optional[str] = None
    title: Optional[str] = "Comment vendre"
    channel_id: Optional[str] = None
    embed_title: Optional[str] = "📖 Comment vendre votre collection ?"
    embed_title_url: Optional[str] = None
    embed_description: Optional[str] = ""
    embed_color: Optional[str] = "#5865F2"
    embed_author_name: Optional[str] = None
    embed_author_icon_url: Optional[str] = None
    embed_author_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_footer_text: Optional[str] = "BRB Collection • Guide Officiel"
    embed_footer_icon_url: Optional[str] = None
    embed_show_timestamp: Optional[bool] = False
    auto_deploy: Optional[bool] = True

class AnnouncementCreate(BaseModel):
    title: Optional[str] = "Nouvelle Annonce"
    channel_id: str
    content: Optional[str] = None
    embed_title: Optional[str] = None
    embed_title_url: Optional[str] = None
    embed_description: Optional[str] = None
    embed_color: Optional[str] = "#5865F2"
    embed_author_name: Optional[str] = None
    embed_author_icon_url: Optional[str] = None
    embed_author_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_footer_text: Optional[str] = None
    embed_footer_icon_url: Optional[str] = None
    embed_show_timestamp: Optional[bool] = True

class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    channel_id: Optional[str] = None
    content: Optional[str] = None
    embed_title: Optional[str] = None
    embed_title_url: Optional[str] = None
    embed_description: Optional[str] = None
    embed_color: Optional[str] = None
    embed_author_name: Optional[str] = None
    embed_author_icon_url: Optional[str] = None
    embed_author_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_footer_text: Optional[str] = None
    embed_footer_icon_url: Optional[str] = None
    embed_show_timestamp: Optional[bool] = None




