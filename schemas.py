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


