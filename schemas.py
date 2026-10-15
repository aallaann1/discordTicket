from pydantic import BaseModel
from typing import Optional, Dict, Any

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

    # Action buttons customization
    close_button_text: Optional[str] = "Fermer le ticket"
    close_button_color: Optional[str] = "danger"
    close_button_emoji: Optional[str] = "🔒"

    reopen_button_text: Optional[str] = "Ré-ouvrir"
    reopen_button_color: Optional[str] = "success"
    reopen_button_emoji: Optional[str] = "🔓"

    delete_button_text: Optional[str] = "Supprimer"
    delete_button_color: Optional[str] = "danger"
    delete_button_emoji: Optional[str] = "🗑️"

class FieldCreate(BaseModel):
    label: str
    is_paragraph: bool = False
    required: bool = True
    order: int = 0

class BotProfileUpdate(BaseModel):
    username: Optional[str] = None
    avatar_base64: Optional[str] = None

