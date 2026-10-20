import discord
from discord.ext import commands, tasks
import chat_exporter
import io
import datetime
import logging
import asyncio
import os
import socket
import re
from typing import Optional
from sqlalchemy.future import select
from sqlalchemy import func
from database import AsyncSessionLocal, Panel, FormField, Ticket, TicketArchive, InfoEmbed, Announcement, BotSettings

logger = logging.getLogger(__name__)

# Identifiant de l'instance (nom d'hôte / conteneur) pour détecter plusieurs bots lancés avec le même token
INSTANCE_ID = os.getenv("BOT_INSTANCE_NAME") or socket.gethostname()

async def find_ticket_for_channel(session, channel: discord.abc.GuildChannel) -> Optional[Ticket]:
    """Retrouve le ticket associé à un salon par channel.id, avec fallback par nom de salon (ticket006 -> ID 6)."""
    if not channel:
        return None

    # 1. Recherche directe par channel_id
    res = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
    ticket = res.scalar_one_or_none()
    if ticket:
        return ticket

    channel_name = getattr(channel, "name", "") or ""

    # 2. Fallback par nom de salon exact (ticket_name, ex: alan-001)
    if channel_name:
        res_name = await session.execute(
            select(Ticket).where(Ticket.ticket_name == channel_name).order_by(Ticket.id.desc())
        )
        ticket = res_name.scalars().first()
        if ticket:
            logger.info(
                f"[{INSTANCE_ID}] Fallback réussi : Ticket #{ticket.id} ({ticket.ticket_name}) retrouvé via nom de salon '{channel_name}' "
                f"(channel_id resynchronisé de {ticket.channel_id} vers {channel.id})"
            )
            ticket.channel_id = channel.id
            await session.commit()
            return ticket

    # 3. Fallback pour anciens tickets nommés ticket006, ticket6, etc.
    match = re.search(r"^ticket[-_]?(\d+)$", channel_name, re.IGNORECASE)
    if match:
        try:
            t_id = int(match.group(1))
            ticket = await session.get(Ticket, t_id)
            if ticket:
                logger.info(
                    f"[{INSTANCE_ID}] Fallback réussi : Ticket #{ticket.id} retrouvé via ancien nom de salon '{channel_name}' "
                    f"(channel_id resynchronisé de {ticket.channel_id} vers {channel.id})"
                )
                ticket.channel_id = channel.id
                await session.commit()
                return ticket
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Erreur fallback nom de salon '{channel_name}': {e}")

    # 4. Fallback par recherche dans l'historique du salon (footer embed ou fichier transcript)
    try:
        async for msg in channel.history(limit=20):
            for emb in msg.embeds:
                footer_text = emb.footer.text if emb.footer else ""
                match_id = re.search(r"ID:\s*(\d+)", footer_text or "", re.IGNORECASE)
                if match_id:
                    t_id = int(match_id.group(1))
                    ticket = await session.get(Ticket, t_id)
                    if ticket:
                        logger.info(f"[{INSTANCE_ID}] Fallback réussi : Ticket #{ticket.id} retrouvé via footer embed ID '{footer_text}'")
                        ticket.channel_id = channel.id
                        await session.commit()
                        return ticket
                match_footer = re.search(r"ticket[-_]?(\d+)", footer_text or "", re.IGNORECASE)
                if match_footer:
                    t_id = int(match_footer.group(1))
                    ticket = await session.get(Ticket, t_id)
                    if ticket:
                        logger.info(f"[{INSTANCE_ID}] Fallback réussi : Ticket #{ticket.id} retrouvé via footer embed '{footer_text}'")
                        ticket.channel_id = channel.id
                        await session.commit()
                        return ticket
            for att in msg.attachments:
                match_att = re.search(r"transcript[-_]?(\d+)", att.filename or "", re.IGNORECASE)
                if match_att:
                    t_id = int(match_att.group(1))
                    ticket = await session.get(Ticket, t_id)
                    if ticket:
                        logger.info(f"[{INSTANCE_ID}] Fallback réussi : Ticket #{ticket.id} retrouvé via fichier '{att.filename}'")
                        ticket.channel_id = channel.id
                        await session.commit()
                        return ticket
    except Exception as e:
        logger.warning(f"[{INSTANCE_ID}] Erreur fallback historique salon: {e}")

    return None

async def remove_close_buttons_from_channel(channel: discord.TextChannel):
    """Retire les boutons de fermeture ('Fermer' et 'Fermer avec raison') de tous les messages du bot dans ce salon."""
    if not channel:
        return
    try:
        async for msg in channel.history(limit=30):
            if bot.user and msg.author.id == bot.user.id and msg.components:
                has_close = False
                for row in msg.components:
                    for comp in getattr(row, "children", []):
                        if getattr(comp, "custom_id", None) in ("close_ticket", "close_ticket_reason"):
                            has_close = True
                            break
                    if has_close:
                        break
                if has_close:
                    try:
                        await msg.edit(view=None)
                    except Exception as e:
                        logger.warning(f"Impossible d'éditer le message {msg.id} pour retirer le bouton close: {e}")
    except Exception as e:
        logger.warning(f"Erreur nettoyage boutons fermer dans channel {channel.id}: {e}")

async def remove_archive_buttons_from_channel(channel: discord.TextChannel):
    """Retire les boutons 'Ré-ouvrir' et 'Supprimer' de tous les messages d'archive du bot dans ce salon."""
    if not channel:
        return
    try:
        async for msg in channel.history(limit=30):
            if bot.user and msg.author.id == bot.user.id and msg.components:
                has_archive = False
                for row in msg.components:
                    for comp in getattr(row, "children", []):
                        if getattr(comp, "custom_id", None) in ["reopen_ticket", "delete_ticket"]:
                            has_archive = True
                            break
                    if has_archive:
                        break
                if has_archive:
                    try:
                        await msg.edit(view=None)
                    except Exception as e:
                        logger.warning(f"Impossible d'éditer le message archive {msg.id}: {e}")
    except Exception as e:
        logger.warning(f"Erreur nettoyage boutons archive dans channel {channel.id}: {e}")

def get_button_style(color_str: str) -> discord.ButtonStyle:
    styles = {
        "primary": discord.ButtonStyle.primary,     # Blurple / Bleu
        "secondary": discord.ButtonStyle.secondary, # Gris
        "success": discord.ButtonStyle.success,     # Vert
        "danger": discord.ButtonStyle.danger        # Rouge
    }
    return styles.get(color_str, discord.ButtonStyle.primary)

def is_staff(member: discord.Member, panel: Panel = None) -> bool:
    """Vérifie si un membre appartient au staff (Admin, Gérer les salons, ou rôle staff configuré)."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels or member.guild_permissions.manage_messages:
        return True
    if panel and panel.ping_role_id:
        try:
            r_id = int(str(panel.ping_role_id).strip())
            if any(role.id == r_id for role in member.roles):
                return True
        except Exception:
            pass
    return False

# --- VUE POUR LE PANNEAU DÉPLOYÉ (Bouton d'ouverture) ---
class PanelView(discord.ui.View):
    def __init__(self, panel: Panel):
        super().__init__(timeout=None)
        btn_style = get_button_style(panel.button_color)
        button = discord.ui.Button(
            label=panel.button_text, 
            style=btn_style, 
            custom_id=f"panel_{panel.id}", 
            emoji=panel.button_emoji if panel.button_emoji else None
        )
        self.add_item(button)


def build_panel_embed(panel: Panel) -> discord.Embed:
    """Construit un objet discord.Embed riche et moderne pour un panel de tickets."""
    raw_color = panel.embed_color or "#2ecc71"
    try:
        color_int = int(str(raw_color).lstrip('#'), 16)
    except Exception:
        color_int = 0x2ecc71

    title_url = str(panel.embed_title_url).strip() if getattr(panel, "embed_title_url", None) else None
    if title_url and not (title_url.startswith("http://") or title_url.startswith("https://")):
        title_url = None

    embed = discord.Embed(
        title=(panel.embed_title or "Système de Ticket")[:256],
        description=(panel.embed_description or "Cliquez sur le bouton ci-dessous pour ouvrir un ticket.")[:4096],
        color=discord.Color(color_int),
        url=title_url,
    )

    # 1. Author (Auteur avec icône et lien cliquable)
    author_name = str(getattr(panel, "embed_author_name", "") or "").strip()
    if author_name:
        author_kwargs = {"name": author_name[:256]}
        a_icon = str(getattr(panel, "embed_author_icon_url", "") or "").strip()
        if a_icon and (a_icon.startswith("http://") or a_icon.startswith("https://")):
            author_kwargs["icon_url"] = a_icon
        a_url = str(getattr(panel, "embed_author_url", "") or "").strip()
        if a_url and (a_url.startswith("http://") or a_url.startswith("https://")):
            author_kwargs["url"] = a_url
        embed.set_author(**author_kwargs)

    # 2. Thumbnail (Miniature en haut à droite)
    thumb_url = str(getattr(panel, "embed_thumbnail_url", "") or "").strip()
    if thumb_url and (thumb_url.startswith("http://") or thumb_url.startswith("https://")):
        embed.set_thumbnail(url=thumb_url)

    # 3. Image (Grande bannière en bas de l'embed)
    img_url = str(getattr(panel, "embed_image_url", "") or "").strip()
    if img_url and (img_url.startswith("http://") or img_url.startswith("https://")):
        embed.set_image(url=img_url)

    # 4. Footer & Icon
    footer_text = str(getattr(panel, "embed_footer_text", "") or "Système de Ticketing").strip()
    if not footer_text:
        footer_text = "Système de Ticketing"
    footer_kwargs = {"text": footer_text[:2048]}
    f_icon = str(getattr(panel, "embed_footer_icon_url", "") or "").strip()
    if f_icon and (f_icon.startswith("http://") or f_icon.startswith("https://")):
        footer_kwargs["icon_url"] = f_icon
    embed.set_footer(**footer_kwargs)

    # 5. Timestamp
    if getattr(panel, "embed_show_timestamp", False):
        embed.timestamp = datetime.datetime.utcnow()

    return embed


def build_info_embed(info: InfoEmbed) -> discord.Embed:
    """Construit un objet discord.Embed riche pour un message guide/information (ex: Comment vendre)."""
    raw_color = getattr(info, "embed_color", None) or "#5865F2"
    try:
        color_int = int(str(raw_color).lstrip('#'), 16)
    except Exception:
        color_int = 0x5865F2

    title_url = str(getattr(info, "embed_title_url", "") or "").strip()
    if title_url and not (title_url.startswith("http://") or title_url.startswith("https://")):
        title_url = None

    embed = discord.Embed(
        title=(getattr(info, "embed_title", "") or "📖 Comment vendre votre collection ?")[:256],
        description=(getattr(info, "embed_description", "") or "")[:4096],
        color=discord.Color(color_int),
        url=title_url,
    )

    # 1. Author
    author_name = str(getattr(info, "embed_author_name", "") or "").strip()
    if author_name:
        author_kwargs = {"name": author_name[:256]}
        a_icon = str(getattr(info, "embed_author_icon_url", "") or "").strip()
        if a_icon and (a_icon.startswith("http://") or a_icon.startswith("https://")):
            author_kwargs["icon_url"] = a_icon
        a_url = str(getattr(info, "embed_author_url", "") or "").strip()
        if a_url and (a_url.startswith("http://") or a_url.startswith("https://")):
            author_kwargs["url"] = a_url
        embed.set_author(**author_kwargs)

    # 2. Thumbnail
    thumb_url = str(getattr(info, "embed_thumbnail_url", "") or "").strip()
    if thumb_url and (thumb_url.startswith("http://") or thumb_url.startswith("https://")):
        embed.set_thumbnail(url=thumb_url)

    # 3. Image
    img_url = str(getattr(info, "embed_image_url", "") or "").strip()
    if img_url and (img_url.startswith("http://") or img_url.startswith("https://")):
        embed.set_image(url=img_url)

    # 4. Footer & Icon
    footer_text = str(getattr(info, "embed_footer_text", "") or "BRB Collection • Guide Officiel").strip()
    if not footer_text:
        footer_text = "BRB Collection • Guide Officiel"
    footer_kwargs = {"text": footer_text[:2048]}
    f_icon = str(getattr(info, "embed_footer_icon_url", "") or "").strip()
    if f_icon and (f_icon.startswith("http://") or f_icon.startswith("https://")):
        footer_kwargs["icon_url"] = f_icon
    embed.set_footer(**footer_kwargs)

    # 5. Timestamp
    if getattr(info, "embed_show_timestamp", False):
        embed.timestamp = datetime.datetime.utcnow()

    return embed


def build_announcement_embed(ann) -> Optional[discord.Embed]:
    """Construit un objet discord.Embed riche pour une annonce."""
    title = str(getattr(ann, "embed_title", None) or "").strip()
    desc = str(getattr(ann, "embed_description", None) or "").strip()
    img = str(getattr(ann, "embed_image_url", None) or "").strip()
    thumb = str(getattr(ann, "embed_thumbnail_url", None) or "").strip()
    author = str(getattr(ann, "embed_author_name", None) or "").strip()
    footer = str(getattr(ann, "embed_footer_text", None) or "").strip()

    # Si aucun champ d'embed n'est renseigné, renvoie None (permet l'envoi de texte simple)
    if not any([title, desc, img, thumb, author, footer]):
        return None

    raw_color = getattr(ann, "embed_color", None) or "#5865F2"
    try:
        color_int = int(str(raw_color).lstrip('#'), 16)
    except Exception:
        color_int = 0x5865F2

    title_url = str(getattr(ann, "embed_title_url", "") or "").strip()
    if title_url and not (title_url.startswith("http://") or title_url.startswith("https://")):
        title_url = None

    embed = discord.Embed(
        title=title[:256] if title else None,
        description=desc[:4096] if desc else None,
        color=discord.Color(color_int),
        url=title_url,
    )

    if author:
        author_kwargs = {"name": author[:256]}
        a_icon = str(getattr(ann, "embed_author_icon_url", "") or "").strip()
        if a_icon and (a_icon.startswith("http://") or a_icon.startswith("https://")):
            author_kwargs["icon_url"] = a_icon
        a_url = str(getattr(ann, "embed_author_url", "") or "").strip()
        if a_url and (a_url.startswith("http://") or a_url.startswith("https://")):
            author_kwargs["url"] = a_url
        embed.set_author(**author_kwargs)

    if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):
        embed.set_thumbnail(url=thumb)

    if img and (img.startswith("http://") or img.startswith("https://")):
        embed.set_image(url=img)

    if footer:
        footer_kwargs = {"text": footer[:2048]}
        f_icon = str(getattr(ann, "embed_footer_icon_url", "") or "").strip()
        if f_icon and (f_icon.startswith("http://") or f_icon.startswith("https://")):
            footer_kwargs["icon_url"] = f_icon
        embed.set_footer(**footer_kwargs)

    if getattr(ann, "embed_show_timestamp", False):
        embed.timestamp = datetime.datetime.utcnow()

    return embed




def parse_discord_emoji(emoji_str: str) -> Optional[dict]:
    """Convertit un emoji Unicode ou custom (<:nom:id>) au format dict attendu par l'API Discord."""
    if not emoji_str or not str(emoji_str).strip():
        return None
    raw = str(emoji_str).strip()
    custom_match = re.match(r"^<(a?):([a-zA-Z0-9_]+):(\d+)>$", raw)
    if custom_match:
        return {
            "animated": bool(custom_match.group(1)),
            "name": custom_match.group(2),
            "id": custom_match.group(3),
        }
    return {"name": raw}


def get_effective_field_type(f: FormField) -> str:
    raw_type = getattr(f, "field_type", None)
    if not raw_type:
        return "paragraph" if getattr(f, "is_paragraph", False) else "short_text"
    if raw_type == "short_text" and getattr(f, "is_paragraph", False):
        return "paragraph"
    return raw_type


def build_modal_payload(panel: Panel, fields: list) -> dict:
    """
    Construit le payload JSON d'une modale Discord utilisant les composants Label (type 18),
    permettant d'intégrer des listes déroulantes (type 3, 5, 6, 8), des descriptions sous les champs,
    des placeholders, valeurs par défaut et limites de caractères.
    """
    modal_title = (getattr(panel, "modal_title", None) or panel.name or "Formulaire").strip()[:45]
    sorted_fields = sorted(fields, key=lambda x: (x.order if x.order is not None else 0, x.id))[:5]
    components = []

    for f in sorted_fields:
        ftype = get_effective_field_type(f)
        is_req = bool(f.required) if f.required is not None else True
        cid = f"field_{f.id}"

        if ftype == "select":
            raw_options = getattr(f, "options", None) or []
            select_options = []
            seen_values = set()
            for opt in raw_options[:25]:
                if isinstance(opt, str):
                    opt = {"label": opt, "value": opt}
                if not isinstance(opt, dict):
                    continue
                lbl = str(opt.get("label") or "").strip()[:100]
                if not lbl:
                    continue
                val = str(opt.get("value") or lbl).strip()[:100]
                base_val = val
                idx_dup = 2
                while val in seen_values:
                    suffix = f" ({idx_dup})"
                    val = f"{base_val[:100 - len(suffix)]}{suffix}"
                    idx_dup += 1
                seen_values.add(val)

                opt_dict = {
                    "label": lbl,
                    "value": val,
                }
                desc = str(opt.get("description") or "").strip()[:100]
                if desc:
                    opt_dict["description"] = desc
                emoji_obj = parse_discord_emoji(opt.get("emoji"))
                if emoji_obj:
                    opt_dict["emoji"] = emoji_obj
                if opt.get("default"):
                    opt_dict["default"] = True
                select_options.append(opt_dict)

            if not select_options:
                select_options = [{"label": "Option par défaut", "value": "Option par défaut"}]

            num_opts = len(select_options)
            max_v = getattr(f, "max_values", 1) or 1
            max_v = max(1, min(int(max_v), num_opts))
            min_v = getattr(f, "min_values", 1) if getattr(f, "min_values", None) is not None else (1 if is_req else 0)
            min_v = max(0 if not is_req else 1, min(int(min_v), max_v))

            inner_comp = {
                "type": 3,  # STRING_SELECT
                "custom_id": cid,
                "options": select_options,
                "min_values": min_v,
                "max_values": max_v,
                "required": is_req,
            }
            placeholder = str(getattr(f, "placeholder", None) or "Fais un choix").strip()[:150]
            if placeholder:
                inner_comp["placeholder"] = placeholder

        elif ftype in ("user_select", "role_select", "channel_select"):
            comp_type_map = {
                "user_select": 5,
                "role_select": 6,
                "channel_select": 8,
            }
            max_v = max(1, min(int(getattr(f, "max_values", 1) or 1), 25))
            min_v = getattr(f, "min_values", 1) if getattr(f, "min_values", None) is not None else (1 if is_req else 0)
            min_v = max(0 if not is_req else 1, min(int(min_v), max_v))

            inner_comp = {
                "type": comp_type_map[ftype],
                "custom_id": cid,
                "min_values": min_v,
                "max_values": max_v,
                "required": is_req,
            }
            placeholder = str(getattr(f, "placeholder", None) or "Fais un choix").strip()[:150]
            if placeholder:
                inner_comp["placeholder"] = placeholder

        elif ftype == "file_upload":
            # Component Type 19 : FILE_UPLOAD (1 à 10 fichiers/photos dans la modale)
            max_v = max(1, min(int(getattr(f, "max_values", 5) or 5), 10))
            raw_min = getattr(f, "min_values", None)
            if not is_req:
                min_v = 0
            else:
                min_v = max(1, min(int(raw_min if raw_min is not None else 1), max_v))

            inner_comp = {
                "type": 19,  # FILE_UPLOAD
                "custom_id": cid,
                "min_values": min_v,
                "max_values": max_v,
                "required": is_req,
            }

        else:
            # short_text ou paragraph -> TEXT_INPUT (type 4)
            inner_comp = {
                "type": 4,
                "custom_id": cid,
                "style": 2 if ftype == "paragraph" else 1,
                "required": is_req,
            }
            placeholder = str(getattr(f, "placeholder", None) or "").strip()[:100]
            if placeholder:
                inner_comp["placeholder"] = placeholder

            default_val = str(getattr(f, "default_value", None) or "").strip()[:4000]
            if default_val:
                inner_comp["value"] = default_val

            min_len = getattr(f, "min_length", None)
            if min_len is not None and str(min_len).strip() != "":
                try:
                    ml = int(min_len)
                    if 0 <= ml <= 4000:
                        inner_comp["min_length"] = ml
                except (ValueError, TypeError):
                    pass

            max_len = getattr(f, "max_length", None)
            if max_len is not None and str(max_len).strip() != "":
                try:
                    mxl = int(max_len)
                    if 1 <= mxl <= 4000:
                        inner_comp["max_length"] = mxl
                except (ValueError, TypeError):
                    pass

        label_wrapper = {
            "type": 18,  # ComponentType.LABEL
            "label": (f.label or "Question").strip()[:45],
            "component": inner_comp,
        }
        desc_text = str(getattr(f, "description", None) or "").strip()[:100]
        if desc_text:
            label_wrapper["description"] = desc_text

        components.append(label_wrapper)

    return {
        "custom_id": f"ticket_modal_{panel.id}",
        "title": modal_title or "Formulaire",
        "components": components,
    }


async def send_custom_modal(interaction: discord.Interaction, panel: Panel, fields: list):
    """Envoie la modale enrichie (Label type 18 + listes déroulantes / champs texte / upload) via l'API Discord."""
    from discord.interactions import async_context, interaction_response_params

    modal_data = build_modal_payload(panel, fields)
    adapter = async_context.get()
    http = interaction._state.http
    params = interaction_response_params(9, modal_data)
    try:
        await adapter.create_interaction_response(
            interaction.id,
            interaction.token,
            session=interaction._session,
            proxy=http.proxy,
            proxy_auth=http.proxy_auth,
            params=params,
        )
        interaction.response._response_type = discord.InteractionResponseType.modal
    except discord.HTTPException as e:
        logger.warning(f"[{INSTANCE_ID}] send_custom_modal a échoué ({e}). Tentative de fallback sur FormModal standard.")
        text_fields = [f for f in fields if get_effective_field_type(f) in ("short_text", "paragraph")]
        if text_fields and not interaction.response.is_done():
            await interaction.response.send_modal(FormModal(panel, text_fields))
        else:
            raise


MAX_CATEGORY_CHANNELS = 50


async def get_base_category(
    guild: discord.Guild,
    base_category_id,
    bot_client: Optional[discord.Client] = None,
) -> Optional[discord.CategoryChannel]:
    """Récupère la catégorie principale configurée par son ID."""
    if not guild or not base_category_id:
        return None
    try:
        cat_id = int(str(base_category_id).strip())
        if cat_id <= 0:
            return None
        cat = guild.get_channel(cat_id) or discord.utils.get(guild.categories, id=cat_id)
        if not cat and bot_client:
            try:
                fetched = await bot_client.fetch_channel(cat_id)
                if isinstance(fetched, discord.CategoryChannel):
                    cat = fetched
            except Exception as e:
                logger.warning(f"Impossible de fetch la catégorie {cat_id}: {e}")
        return cat if isinstance(cat, discord.CategoryChannel) else None
    except Exception as e:
        logger.error(f"Erreur get_base_category({base_category_id}): {e}")
        return None


def get_overflow_categories_for_base(
    guild: discord.Guild,
    base_cat: discord.CategoryChannel,
) -> list:
    """
    Retourne la liste triée [(num, category), ...] des catégories de débordement
    associées à `base_cat` (ex: 'ticket (2)', 'ticket (3)', 'archive 2', 'archive (2)').
    """
    if not guild or not base_cat:
        return []
    base_name = base_cat.name.strip()
    pattern = re.compile(
        rf"^{re.escape(base_name)}\s*(?:\((\d+)\)|(\d+))$",
        re.IGNORECASE,
    )
    overflows = []
    for cat in guild.categories:
        if cat.id == base_cat.id:
            continue
        m = pattern.match(cat.name.strip())
        if m:
            num_str = m.group(1) or m.group(2)
            try:
                num = int(num_str)
                if num >= 2:
                    overflows.append((num, cat))
            except ValueError:
                pass
    overflows.sort(key=lambda x: (x[0], x[1].id))
    return overflows


async def create_overflow_category(
    guild: discord.Guild,
    base_cat: discord.CategoryChannel,
    next_num: int,
) -> discord.CategoryChannel:
    """Crée une catégorie de débordement `Nom (N)` avec exactement les mêmes permissions que `base_cat`."""
    new_name = f"{base_cat.name.strip()} ({next_num})"
    overwrites_copy = dict(base_cat.overwrites) if base_cat.overwrites else {}
    new_cat = await guild.create_category(
        name=new_name[:100],
        overwrites=overwrites_copy,
        reason=f"Débordement automatique (> {MAX_CATEGORY_CHANNELS} salons dans {base_cat.name})",
    )
    logger.info(f"✨ Catégorie de débordement créée : '{new_cat.name}' (ID: {new_cat.id}) avec les mêmes droits que '{base_cat.name}'.")
    return new_cat


async def resolve_category_with_overflow(
    guild: discord.Guild,
    base_category_id,
    bot_client: Optional[discord.Client] = None,
) -> tuple:
    """
    Résout la catégorie cible où placer un salon.
    Si la catégorie principale a < 50 salons, la retourne.
    Sinon, cherche ou crée `Nom (2)`, `Nom (3)`, etc. avec exactement les mêmes droits.
    Retourne `(target_category, base_category)`.
    """
    base_cat = await get_base_category(guild, base_category_id, bot_client)
    if not base_cat:
        return None, None

    if len(base_cat.channels) < MAX_CATEGORY_CHANNELS:
        return base_cat, base_cat

    overflows = get_overflow_categories_for_base(guild, base_cat)
    for num, o_cat in overflows:
        if len(o_cat.channels) < MAX_CATEGORY_CHANNELS:
            return o_cat, base_cat

    existing_nums = {num for num, _ in overflows}
    next_num = 2
    while next_num in existing_nums:
        next_num += 1

    new_cat = await create_overflow_category(guild, base_cat, next_num)
    return new_cat, base_cat


async def move_channel_with_overflow(
    channel: discord.TextChannel,
    base_category_id,
    bot_client: Optional[discord.Client] = None,
) -> Optional[discord.CategoryChannel]:
    """
    Déplace un salon existant vers `base_category_id` (ou sa catégorie de débordement `Nom (2)`, etc.).
    Gère aussi le cas où le cache Discord n'était pas à jour et que Discord renvoie le code 30052.
    """
    if not channel or not channel.guild or not base_category_id:
        return None
    guild = channel.guild
    target_cat, base_cat = await resolve_category_with_overflow(guild, base_category_id, bot_client)
    if not target_cat:
        return None
    if channel.category_id == target_cat.id:
        return target_cat
    try:
        await channel.edit(category=target_cat)
        return target_cat
    except discord.HTTPException as e:
        if (getattr(e, "code", None) == 30052 or "Maximum number of channels in category reached" in str(e)) and base_cat:
            overflows = get_overflow_categories_for_base(guild, base_cat)
            existing_nums = {num for num, _ in overflows}
            next_num = 2
            while next_num in existing_nums:
                next_num += 1
            new_cat = await create_overflow_category(guild, base_cat, next_num)
            await channel.edit(category=new_cat)
            return new_cat
        raise


async def create_ticket_channel_with_overflow(
    guild: discord.Guild,
    channel_name: str,
    base_category_id,
    overwrites: dict,
    bot_client: Optional[discord.Client] = None,
) -> discord.TextChannel:
    """
    Crée un salon textuel dans la catégorie configurée (ou dans `Nom (2)` si pleine).
    Gère aussi le fallback si Discord renvoie 30052 (catégorie pleine côté serveur).
    """
    target_cat, base_cat = await resolve_category_with_overflow(guild, base_category_id, bot_client)
    try:
        return await guild.create_text_channel(
            name=channel_name,
            category=target_cat,
            overwrites=overwrites,
        )
    except discord.HTTPException as e:
        if (getattr(e, "code", None) == 30052 or "Maximum number of channels in category reached" in str(e)) and base_cat:
            overflows = get_overflow_categories_for_base(guild, base_cat)
            existing_nums = {num for num, _ in overflows}
            next_num = 2
            while next_num in existing_nums:
                next_num += 1
            new_cat = await create_overflow_category(guild, base_cat, next_num)
            return await guild.create_text_channel(
                name=channel_name,
                category=new_cat,
                overwrites=overwrites,
            )
        raise


async def cleanup_and_rebalance_category_family(
    guild: discord.Guild,
    base_category_id,
    bot_client: Optional[discord.Client] = None,
):
    """
    Rééquilibre les salons entre la catégorie principale et ses catégories de débordement (`Nom (2)`, `Nom (3)`...).
    Si la catégorie principale (ou une catégorie précédente) a moins de 50 salons, rapatrie les salons
    depuis la dernière catégorie de débordement, puis supprime toute catégorie de débordement devenue vide.
    """
    base_cat = await get_base_category(guild, base_category_id, bot_client)
    if not base_cat:
        return

    overflows = get_overflow_categories_for_base(guild, base_cat)
    if not overflows:
        return

    all_cats = [base_cat] + [cat for _, cat in overflows]

    # 1. Rapatrier les salons des catégories de débordement vers les catégories précédentes ayant de la place
    for idx_dest, dest_cat in enumerate(all_cats[:-1]):
        while len(dest_cat.channels) < MAX_CATEGORY_CHANNELS:
            src_cat = None
            for candidate in reversed(all_cats[idx_dest + 1:]):
                if len(candidate.channels) > 0:
                    src_cat = candidate
                    break
            if not src_cat:
                break
            ch_to_move = src_cat.channels[0]
            try:
                await ch_to_move.edit(
                    category=dest_cat,
                    reason=f"Rééquilibrage automatique vers {dest_cat.name} (< {MAX_CATEGORY_CHANNELS} salons)",
                )
            except Exception as move_err:
                logger.warning(f"Impossible de rapatrier le salon #{ch_to_move.name} vers {dest_cat.name}: {move_err}")
                break

    # 2. Supprimer toutes les catégories de débordement qui sont désormais vides (0 salon)
    overflows_after = get_overflow_categories_for_base(guild, base_cat)
    for num, o_cat in reversed(overflows_after):
        if len(o_cat.channels) == 0:
            try:
                cat_name = o_cat.name
                await o_cat.delete(reason=f"Suppression automatique de la catégorie de débordement vide {cat_name}")
                logger.info(f"🗑️ Catégorie de débordement supprimée car vide : {cat_name}")
            except Exception as del_err:
                logger.warning(f"Impossible de supprimer la catégorie de débordement vide {o_cat.name}: {del_err}")


async def build_ticket_channel_overwrites(
    guild: discord.Guild,
    base_cat: Optional[discord.CategoryChannel],
    creator_id: Optional[int],
    panel: Optional[Panel],
) -> tuple:
    """
    Construit le dictionnaire `overwrites` exact pour un salon de ticket :
    - Héritage des permissions de la catégorie active (Staff)
    - @everyone refusé
    - Créateur autorisé (lecture, écriture, fichiers, liens)
    - Bot autorisé
    - Rôle staff du panel (`ping_role_id`) autorisé
    """
    overwrites = {}
    if base_cat and base_cat.overwrites:
        for target, ow in base_cat.overwrites.items():
            overwrites[target] = ow

    overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False)

    creator_member = None
    if creator_id:
        try:
            cid = int(creator_id)
            creator_member = guild.get_member(cid)
            if not creator_member:
                try:
                    creator_member = await guild.fetch_member(cid)
                except Exception:
                    creator_member = None
            if creator_member:
                overwrites[creator_member] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, attach_files=True, embed_links=True
                )
        except Exception:
            pass

    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True, manage_channels=True, manage_permissions=True
        )

    if panel and panel.ping_role_id:
        try:
            r_id = int(str(panel.ping_role_id).strip())
            role = guild.get_role(r_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, attach_files=True, embed_links=True
                )
        except Exception:
            pass

    return overwrites, creator_member


def build_ticket_welcome_embed_and_pings(
    panel: Optional[Panel],
    ticket_id: int,
    ticket_name: str,
    creator_id: Optional[int],
    answers_dict: Optional[dict],
) -> tuple:
    """Construit l'embed d'accueil du ticket avec ses réponses et les mentions configurées."""
    intro_color_hex = (getattr(panel, "intro_embed_color", None) or getattr(panel, "embed_color", None)) if panel else None
    try:
        color_int = int(str(intro_color_hex).lstrip('#'), 16) if intro_color_hex else 0x2ecc71
    except Exception:
        color_int = 0x2ecc71

    intro_title = ((getattr(panel, "intro_embed_title", None) if panel else None) or "Bienvenue dans votre ticket").strip()
    intro_desc = (panel.intro_message if panel and panel.intro_message else "Bienvenue dans votre ticket !")
    embed = discord.Embed(
        title=intro_title[:256] if intro_title else None,
        description=intro_desc,
        color=discord.Color(color_int),
    )

    if panel:
        intro_thumb = str(getattr(panel, "intro_thumbnail_url", "") or "").strip()
        if intro_thumb and (intro_thumb.startswith("http://") or intro_thumb.startswith("https://")):
            embed.set_thumbnail(url=intro_thumb)

        intro_img = str(getattr(panel, "intro_image_url", "") or "").strip()
        if intro_img and (intro_img.startswith("http://") or intro_img.startswith("https://")):
            embed.set_image(url=intro_img)

    if isinstance(answers_dict, dict):
        for label, value in answers_dict.items():
            display_val = str(value).strip() if value is not None and str(value).strip() else "*Non renseigné*"
            embed.add_field(name=str(label)[:256], value=display_val[:1024], inline=False)

    intro_footer = getattr(panel, "intro_footer_text", None) if panel else None
    if not intro_footer or not str(intro_footer).strip():
        intro_footer = f"Système de Ticketing - {ticket_name} (ID: {ticket_id})"
    embed.set_footer(text=str(intro_footer)[:2048])

    pings = []
    if (panel is None or getattr(panel, "ping_creator", True)) and creator_id:
        pings.append(f"<@{creator_id}>")
    if panel and panel.ping_role_id:
        try:
            r_id = int(str(panel.ping_role_id).strip())
            if r_id > 0:
                pings.append(f"<@&{r_id}>")
        except Exception:
            pass

    content = " ".join(pings) if pings else None
    return embed, content


def resolve_guild_for_panel(bot_client: discord.Client, panel: Optional[Panel]) -> Optional[discord.Guild]:
    """Trouve le serveur Discord associé à un panel."""
    if panel:
        for attr in ("active_category_id", "archive_category_id", "deployed_channel_id"):
            val = getattr(panel, attr, None)
            if val:
                try:
                    cid = int(str(val).strip())
                    ch = bot_client.get_channel(cid)
                    if ch and getattr(ch, "guild", None):
                        return ch.guild
                except Exception:
                    pass
    if bot_client.guilds:
        return bot_client.guilds[0]
    return None


async def process_ticket_creation(
    interaction: discord.Interaction,
    panel: Panel,
    answers_dict: dict,
    uploaded_attachments: Optional[list] = None,
):
    """Crée le salon de ticket (avec gestion de débordement > 50 salons), enregistre le ticket en BDD et envoie l'embed récapitulatif."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    # 1. Résolution de la catégorie active principale et construction des permissions exactes
    base_cat = await get_base_category(guild, panel.active_category_id if panel else None, interaction.client)
    overwrites, _ = await build_ticket_channel_overwrites(guild, base_cat, interaction.user.id, panel)
    overwrites[interaction.user] = discord.PermissionOverwrite(
        read_messages=True, send_messages=True, attach_files=True, embed_links=True
    )

    # 2. Création du ticket en BDD pour obtenir son nom incrémenté par utilisateur (<pseudo>-001, 002, ...)
    async with AsyncSessionLocal() as session:
        count_res = await session.execute(
            select(func.count(Ticket.id)).where(Ticket.creator_id == interaction.user.id)
        )
        total_user_tickets = count_res.scalar() or 0
        user_ticket_number = total_user_tickets + 1

        preferred_name = interaction.user.display_name or interaction.user.name
        clean_pseudo = preferred_name.lower().replace(" ", "-")
        clean_pseudo = re.sub(r"[^a-z0-9\-_]", "", clean_pseudo)
        clean_pseudo = re.sub(r"-+", "-", clean_pseudo).strip("-")
        if not clean_pseudo:
            clean_pseudo = re.sub(r"[^a-z0-9\-_]", "", interaction.user.name.lower()).strip("-") or "ticket"
        clean_pseudo = clean_pseudo[:25].rstrip("-")

        ticket_name = f"{clean_pseudo}-{user_ticket_number:03d}"

        existing_channel_names = {c.name.lower() for c in guild.channels}
        while ticket_name.lower() in existing_channel_names:
            user_ticket_number += 1
            ticket_name = f"{clean_pseudo}-{user_ticket_number:03d}"

        user_pseudo = interaction.user.display_name or interaction.user.name
        ticket = Ticket(
            panel_id=panel.id,
            channel_id=None,
            creator_id=interaction.user.id,
            creator_username=user_pseudo,
            ticket_name=ticket_name,
            user_ticket_number=user_ticket_number,
            form_answers=answers_dict,
            status="open",
        )
        session.add(ticket)
        await session.flush()
        ticket_id = ticket.id

        # 3. Création du salon textuel avec débordement automatique (ticket (2), etc.)
        try:
            channel = await create_ticket_channel_with_overflow(
                guild=guild,
                channel_name=ticket_name,
                base_category_id=panel.active_category_id if panel else None,
                overwrites=overwrites,
                bot_client=interaction.client,
            )
            ticket.channel_id = channel.id
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Erreur création salon ticket: {e}")
            return await interaction.followup.send(f"❌ Erreur lors de la création du salon : {e}", ephemeral=True)

    # 4. Téléchargement des éventuels fichiers uploadés dans la modale (Component Type 19)
    discord_files = []
    if uploaded_attachments:
        import aiohttp
        async with aiohttp.ClientSession() as http_sess:
            for att in uploaded_attachments[:10]:
                url = att.get("url")
                fname = att.get("filename") or "fichier"
                if url:
                    try:
                        async with http_sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                            if resp.status == 200:
                                data_bytes = await resp.read()
                                discord_files.append(discord.File(io.BytesIO(data_bytes), filename=fname))
                    except Exception as dl_err:
                        logger.warning(f"Impossible de télécharger la pièce jointe {fname}: {dl_err}")

    # 5. Construction de l'Embed de bienvenue personnalisé
    intro_color_hex = getattr(panel, "intro_embed_color", None) or panel.embed_color
    try:
        color_int = int(str(intro_color_hex).lstrip('#'), 16) if intro_color_hex else 0x2ecc71
    except Exception:
        color_int = 0x2ecc71

    intro_title = (getattr(panel, "intro_embed_title", None) or "Bienvenue dans votre ticket").strip()
    embed = discord.Embed(
        title=intro_title[:256] if intro_title else None,
        description=panel.intro_message or "Bienvenue dans votre ticket !",
        color=discord.Color(color_int),
    )

    # Miniature & Bannière dans le ticket si renseignées
    intro_thumb = str(getattr(panel, "intro_thumbnail_url", "") or "").strip()
    if intro_thumb and (intro_thumb.startswith("http://") or intro_thumb.startswith("https://")):
        embed.set_thumbnail(url=intro_thumb)

    intro_img = str(getattr(panel, "intro_image_url", "") or "").strip()
    if intro_img and (intro_img.startswith("http://") or intro_img.startswith("https://")):
        embed.set_image(url=intro_img)

    for label, value in answers_dict.items():
        display_val = str(value).strip() if value is not None and str(value).strip() else "*Non renseigné*"
        embed.add_field(name=str(label)[:256], value=display_val[:1024], inline=False)

    intro_footer = getattr(panel, "intro_footer_text", None)
    if not intro_footer or not str(intro_footer).strip():
        intro_footer = f"Système de Ticketing - {ticket_name} (ID: {ticket_id})"
    embed.set_footer(text=str(intro_footer)[:2048])

    # 6. Pings paramétrables (créateur et/ou rôle staff)
    pings = []
    if getattr(panel, "ping_creator", True):
        pings.append(interaction.user.mention)
    if panel.ping_role_id:
        try:
            r_id = int(str(panel.ping_role_id).strip())
            if r_id > 0:
                pings.append(f"<@&{r_id}>")
        except Exception:
            pass

    content = " ".join(pings) if pings else None

    # 7. Envoi du message initial avec les boutons d'action (Fermer & Fermer avec raison) + fichiers joints
    try:
        if discord_files:
            sent_msg = await channel.send(content=content, embed=embed, files=discord_files, view=TicketView(panel))
            # Mettre à jour les URLs dans form_answers avec les URLs permanentes du message posté dans le salon
            if sent_msg and sent_msg.attachments:
                perm_by_name = {a.filename: a.url for a in sent_msg.attachments}
                updated_answers = dict(answers_dict)
                changed = False
                for att in (uploaded_attachments or []):
                    lbl = att.get("label")
                    fn = att.get("filename")
                    old_url = att.get("url")
                    new_url = perm_by_name.get(fn)
                    if lbl and old_url and new_url and lbl in updated_answers:
                        updated_answers[lbl] = str(updated_answers[lbl]).replace(old_url, new_url)
                        changed = True
                if changed:
                    async with AsyncSessionLocal() as session:
                        t_db = await session.get(Ticket, ticket_id)
                        if t_db:
                            t_db.form_answers = updated_answers
                            await session.commit()
        else:
            await channel.send(content=content, embed=embed, view=TicketView(panel))
    except Exception as send_err:
        logger.warning(f"Erreur envoi avec fichiers joints, fallback sans fichiers: {send_err}")
        await channel.send(content=content, embed=embed, view=TicketView(panel))

    await interaction.followup.send(f"✅ Ticket ouvert : {channel.mention}", ephemeral=True)

    # 8. Envoi du log d'ouverture dans le salon de logs si activé
    try:
        actor_disp = f"{interaction.user.mention} (`{interaction.user.display_name or interaction.user.name}`)"
        await send_ticket_log(
            bot_client=interaction.client,
            action="open",
            ticket=ticket,
            panel=panel,
            source="discord",
            actor_display=actor_disp,
            channel_id=channel.id,
        )
    except Exception as log_err:
        logger.warning(f"Erreur envoi log ouverture ticket: {log_err}")


_explicitly_deleted_channel_ids = set()


async def send_ticket_log(
    bot_client: discord.Client,
    action: str,
    ticket: Ticket,
    panel: Optional[Panel] = None,
    source: str = "discord",
    actor_display: str = "Utilisateur inconnu",
    close_reason: Optional[str] = None,
    recreated: bool = False,
    channel_id: Optional[int] = None,
    transcript_html: Optional[str] = None,
):
    """
    Envoie un Embed de journalisation dans le salon de logs configuré (si `logs_enabled` est activé).
    `action` : 'open' | 'close' | 'reopen' | 'delete'
    `source` : 'discord' | 'web'
    """
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(BotSettings).where(BotSettings.key == "global"))
            settings = res.scalar_one_or_none()
            if not settings or not settings.logs_enabled or not settings.logs_channel_id:
                return
            log_channel_id = int(settings.logs_channel_id)
    except Exception as db_err:
        logger.warning(f"Impossible de lire les paramètres de logs: {db_err}")
        return

    log_channel = bot_client.get_channel(log_channel_id)
    if not log_channel:
        try:
            log_channel = await bot_client.fetch_channel(log_channel_id)
        except Exception as fetch_err:
            logger.warning(f"Salon de logs {log_channel_id} introuvable: {fetch_err}")
            return

    t_name = getattr(ticket, "ticket_name", None) or f"ticket{ticket.id:03d}"
    panel_name = panel.name if (panel and getattr(panel, "name", None)) else "Panel par défaut"
    source_badge = "💬 Discord" if source == "discord" else "🌐 Interface Web"
    eff_channel_id = channel_id or getattr(ticket, "channel_id", None)

    creator_str = "Inconnu"
    if getattr(ticket, "creator_id", None):
        c_uname = getattr(ticket, "creator_username", None) or "Membre"
        creator_str = f"<@{ticket.creator_id}> (`{c_uname}`)"
    elif getattr(ticket, "creator_username", None):
        creator_str = f"`{ticket.creator_username}`"

    action_meta = {
        "open": ("🟢 Ouverture de Ticket", 0x10B981, "Ouvert par"),
        "close": ("🟠 Fermeture de Ticket", 0xF59E0B, "Fermé par"),
        "reopen": ("🔵 Ré-ouverture de Ticket", 0x3B82F6, "Ré-ouvert par"),
        "delete": ("🔴 Suppression de Ticket", 0xEF4444, "Supprimé par"),
    }
    title_prefix, color_hex, actor_field_label = action_meta.get(
        action, ("📋 Événement Ticket", 0x5865F2, "Effectué par")
    )

    embed = discord.Embed(
        title=f"{title_prefix} — {t_name}",
        color=discord.Color(color_hex),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    ticket_val = f"**`{t_name}`** (ID: `#{ticket.id}`)"
    if eff_channel_id and action != "delete":
        ticket_val += f"\nSalon : <#{eff_channel_id}>"

    embed.add_field(name="🎫 Ticket", value=ticket_val, inline=True)
    embed.add_field(name="📂 Panel", value=f"**{panel_name}**", inline=True)
    embed.add_field(name="📍 Depuis", value=f"**{source_badge}**", inline=True)
    embed.add_field(name="👤 Créateur du ticket", value=creator_str, inline=True)
    embed.add_field(name=f"⚡ {actor_field_label}", value=actor_display or "Non spécifié", inline=True)

    if action == "open":
        answers = getattr(ticket, "form_answers", None)
        if isinstance(answers, dict) and answers:
            summary_lines = []
            for k, v in list(answers.items())[:5]:
                val_clean = str(v).strip()
                if len(val_clean) > 120:
                    val_clean = val_clean[:117] + "..."
                summary_lines.append(f"• **{k}** : {val_clean}")
            if summary_lines:
                embed.add_field(
                    name="📋 Réponses au formulaire",
                    value="\n".join(summary_lines)[:1024],
                    inline=False,
                )

    elif action == "close":
        reason_clean = str(close_reason).strip() if (close_reason and str(close_reason).strip()) else None
        embed.add_field(
            name="📝 Motif de fermeture",
            value=f"> {reason_clean}"[:1024] if reason_clean else "*Aucun motif renseigné (fermeture simple)*",
            inline=False,
        )

    elif action == "reopen":
        detail_msg = (
            "✨ **Salon recréé automatiquement** dans la catégorie active avec tous ses droits d'origine (le salon précédent avait été supprimé)."
            if recreated
            else "🔄 Salon restauré depuis la catégorie d'archive vers la catégorie active."
        )
        embed.add_field(name="ℹ️ Détails de la ré-ouverture", value=detail_msg, inline=False)

    elif action == "delete":
        last_reason = getattr(ticket, "close_reason", None)
        if last_reason:
            embed.add_field(
                name="📝 Dernier motif de fermeture enregistré",
                value=f"> {last_reason}"[:1024],
                inline=False,
            )

    embed.set_footer(text=f"Logs de Ticketing • Action via {'Discord' if source == 'discord' else 'Interface Web'}")

    try:
        if action == "close" and transcript_html:
            file = discord.File(
                io.BytesIO(transcript_html.encode()),
                filename=f"transcript-{t_name}.html",
            )
            await log_channel.send(embed=embed, file=file)
        else:
            await log_channel.send(embed=embed)
    except Exception as send_err:
        logger.warning(f"Erreur lors de l'envoi du log de ticket dans #{getattr(log_channel, 'name', log_channel_id)}: {send_err}")


async def send_dm_transcript_to_creator(
    bot_client: discord.Client,
    ticket: Ticket,
    panel: Optional[Panel],
    closed_by_display: str,
    close_reason: Optional[str],
    transcript_html: Optional[str],
) -> bool:
    """Envoie automatiquement le résumé de fermeture et le transcript HTML en Message Privé au créateur du ticket."""
    if not ticket or not ticket.creator_id:
        return False

    try:
        user = bot_client.get_user(ticket.creator_id)
        if not user:
            user = await bot_client.fetch_user(ticket.creator_id)
        if not user:
            return False

        t_label = getattr(ticket, "ticket_name", None) or f"ticket-{ticket.id:03d}"
        panel_name = panel.name if panel and panel.name else "Support"
        user_name = user.display_name or user.name or "Membre"
        reason_display = str(close_reason).strip() if (close_reason and str(close_reason).strip()) else "Aucun motif spécifié"

        # Dictionnaire des variables de remplacement pour le texte personnalisé
        replacements = {
            "{ticket_name}": t_label,
            "{user_name}": user_name,
            "{closed_by}": closed_by_display,
            "{close_reason}": reason_display,
            "{reason}": reason_display,
            "{panel_name}": panel_name,
        }

        # Couleur de l'embed MP
        dm_color_hex = getattr(panel, "dm_embed_color", None) or (panel.embed_color if panel else "#5865F2")
        try:
            color_int = int(str(dm_color_hex).lstrip('#'), 16) if dm_color_hex else 0x5865F2
        except Exception:
            color_int = 0x5865F2

        # Titre personnalisé
        raw_title = getattr(panel, "dm_embed_title", None) or "🔒 Ticket fermé — {ticket_name}"
        for k, v in replacements.items():
            raw_title = raw_title.replace(k, v)

        # URL du Titre
        title_url = str(getattr(panel, "dm_embed_title_url", "") or "").strip()
        if title_url and not (title_url.startswith("http://") or title_url.startswith("https://")):
            title_url = None

        # Description personnalisée avec remplacement des variables et mentions
        raw_desc = getattr(panel, "dm_embed_description", None)
        if not raw_desc or not str(raw_desc).strip():
            raw_desc = (
                "Votre ticket **{ticket_name}** a été clôturé par **{closed_by}**.\n"
                "Vous trouverez ci-joint l'archive complète de votre conversation."
            )
        for k, v in replacements.items():
            raw_desc = raw_desc.replace(k, v)

        dm_embed = discord.Embed(
            title=raw_title[:256],
            description=raw_desc[:4096],
            color=discord.Color(color_int),
            url=title_url,
        )

        # Miniature (Thumbnail)
        thumb_url = str(getattr(panel, "dm_thumbnail_url", "") or "").strip()
        if thumb_url and (thumb_url.startswith("http://") or thumb_url.startswith("https://")):
            dm_embed.set_thumbnail(url=thumb_url)

        # Grande Bannière (Image)
        img_url = str(getattr(panel, "dm_image_url", "") or "").strip()
        if img_url and (img_url.startswith("http://") or img_url.startswith("https://")):
            dm_embed.set_image(url=img_url)

        # Champs récapitulatifs
        dm_embed.add_field(name="📂 Catégorie / Panel", value=panel_name, inline=True)
        dm_embed.add_field(name="👤 Fermé par", value=closed_by_display, inline=True)

        if close_reason and str(close_reason).strip():
            # Si le motif n'a pas déjà été inséré dans le corps via {close_reason}, on l'ajoute en field dédié
            if "{close_reason}" not in (getattr(panel, "dm_embed_description", "") or ""):
                dm_embed.add_field(
                    name="📝 Motif de la fermeture",
                    value=f"> {str(close_reason).strip()[:1000]}",
                    inline=False,
                )

        # Footer & Icon
        raw_footer = getattr(panel, "dm_footer_text", None) or "Téléchargez et ouvrez le fichier .html pour relire la conversation."
        for k, v in replacements.items():
            raw_footer = raw_footer.replace(k, v)
        footer_kwargs = {"text": raw_footer[:2048]}

        f_icon = str(getattr(panel, "dm_footer_icon_url", "") or "").strip()
        if f_icon and (f_icon.startswith("http://") or f_icon.startswith("https://")):
            footer_kwargs["icon_url"] = f_icon
        dm_embed.set_footer(**footer_kwargs)

        # Horodatage
        if getattr(panel, "dm_show_timestamp", True):
            dm_embed.timestamp = datetime.datetime.utcnow()

        if transcript_html:
            dm_file = discord.File(
                io.BytesIO(transcript_html.encode("utf-8")),
                filename=f"transcript-{t_label}.html",
            )
            await user.send(embed=dm_embed, file=dm_file)
        else:
            await user.send(embed=dm_embed)
        return True
    except discord.Forbidden:
        logger.info(f"[{INSTANCE_ID}] Impossible d'envoyer le transcript en MP à {ticket.creator_id} (MP fermés).")
        return False
    except Exception as e:
        logger.warning(f"[{INSTANCE_ID}] Erreur lors de l'envoi du transcript en MP à {ticket.creator_id}: {e}")
        return False


async def execute_ticket_close(interaction: discord.Interaction, close_reason: Optional[str] = None):
    """Logique partagée de fermeture d'un ticket (avec ou sans motif), archivage et envoi du transcript en MP."""
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except (discord.InteractionResponded, discord.HTTPException):
            pass
    channel = interaction.channel

    async with AsyncSessionLocal() as session:
        ticket = await find_ticket_for_channel(session, channel)
        if not ticket:
            logger.warning(f"[{INSTANCE_ID}] execute_ticket_close: aucun ticket trouvé pour le salon #{getattr(channel, 'name', '?')}")
            return await interaction.followup.send("❌ Ticket introuvable.", ephemeral=True)

        panel = await session.get(Panel, ticket.panel_id)

        # Si le ticket est déjà fermé : nettoyer les boutons et informer
        if ticket.status == "closed":
            await remove_close_buttons_from_channel(channel)
            return await interaction.followup.send("ℹ️ Ce ticket est déjà fermé.", ephemeral=True)

        transcript = await chat_exporter.export(
            channel,
            limit=None,
            tz_info="Europe/Paris",
            military_time=True,
            bot=interaction.client,
        )

        clean_reason = str(close_reason).strip() if close_reason and str(close_reason).strip() else None
        closer_name = interaction.user.display_name or interaction.user.name

        ticket.status = "closed"
        ticket.updated_at = datetime.datetime.utcnow()
        ticket.closed_by_username = closer_name
        ticket.close_reason = clean_reason

        res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
        archive = res_arc.scalar_one_or_none()
        if archive:
            archive.html_content = transcript  # Écrase l'ancienne retranscription
        else:
            session.add(TicketArchive(ticket_id=ticket.id, html_content=transcript))
        await session.commit()

        # Retirer l'accès au membre créateur
        member = interaction.guild.get_member(ticket.creator_id)
        if not member:
            try:
                member = await interaction.guild.fetch_member(ticket.creator_id)
            except Exception:
                member = None
        if member:
            try:
                await channel.set_permissions(member, overwrite=None)
            except Exception:
                pass

        # Déplacer vers la catégorie d'archive si configurée (avec gestion du débordement archive (2), etc.)
        if panel and panel.archive_category_id:
            try:
                await move_channel_with_overflow(channel, panel.archive_category_id, interaction.client)
            except Exception as e:
                logger.warning(f"Erreur déplacement archive: {e}")

        # Rééquilibrer et nettoyer la catégorie active (supprime ticket (2) si on repasse <= 50 salons)
        if panel and panel.active_category_id:
            try:
                await cleanup_and_rebalance_category_family(interaction.guild, panel.active_category_id, interaction.client)
            except Exception as e:
                logger.warning(f"Erreur nettoyage catégorie active: {e}")

        # 1. Enlever TOUS les boutons 'Fermer' et 'Fermer avec raison' du salon
        await remove_close_buttons_from_channel(channel)

        # 2. Envoyer le résumé + transcript en MP au créateur si activé sur le panel
        dm_enabled = True if panel is None else (bool(panel.dm_transcript_on_close) if getattr(panel, "dm_transcript_on_close", None) is not None else True)
        dm_sent = False
        if dm_enabled:
            dm_sent = await send_dm_transcript_to_creator(
                bot_client=interaction.client,
                ticket=ticket,
                panel=panel,
                closed_by_display=f"{closer_name} ({interaction.user.mention})",
                close_reason=clean_reason,
                transcript_html=transcript,
            )

        # 3. Envoyer l'archive dans le salon avec la vue contenant Ré-ouvrir et Supprimer
        desc_parts = [
            f"Le ticket a été fermé par {interaction.user.mention}.",
            "La conversation a été archivée.",
        ]
        if clean_reason:
            desc_parts.append(f"\n📝 **Raison de la fermeture :**\n> {clean_reason}")

        embed = discord.Embed(
            title="🔒 Ticket Fermé",
            description="\n".join(desc_parts),
            color=discord.Color.red(),
        )
        if dm_enabled:
            embed.set_footer(
                text="📩 Transcript envoyé en MP au créateur." if dm_sent else "⚠️ Transcript non envoyé en MP (MP du membre fermés)."
            )

        if transcript:
            file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{ticket.id}.html")
            await channel.send(embed=embed, file=file, view=ArchivedTicketView(panel))
        else:
            await channel.send(embed=embed, view=ArchivedTicketView(panel))

        # 4. Envoi du log de fermeture dans le salon de logs si activé
        try:
            actor_disp = f"{interaction.user.mention} (`{closer_name}`)"
            await send_ticket_log(
                bot_client=interaction.client,
                action="close",
                ticket=ticket,
                panel=panel,
                source="discord",
                actor_display=actor_disp,
                close_reason=clean_reason,
                channel_id=channel.id,
                transcript_html=transcript,
            )
        except Exception as log_err:
            logger.warning(f"Erreur envoi log fermeture ticket: {log_err}")


class CloseReasonModal(discord.ui.Modal, title="Fermer le ticket avec raison"):
    reason_input = discord.ui.TextInput(
        label="Motif de la fermeture",
        style=discord.TextStyle.paragraph,
        placeholder="Ex : Achat conclu, Refusé - Prix trop haut, Inactivité...",
        required=True,
        min_length=2,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await execute_ticket_close(interaction, close_reason=self.reason_input.value)


class FormModal(discord.ui.Modal):
    def __init__(self, panel: Panel, fields: list):
        modal_title = (getattr(panel, "modal_title", None) or panel.name or "Formulaire")[:45]
        super().__init__(title=modal_title)
        self.panel = panel
        for f in sorted(fields, key=lambda x: (x.order if x.order is not None else 0, x.id))[:5]:
            style = discord.TextStyle.paragraph if f.is_paragraph else discord.TextStyle.short
            self.add_item(discord.ui.TextInput(
                label=f.label[:45],
                style=style,
                required=f.required,
                placeholder=(getattr(f, "placeholder", None) or "")[:100] or None,
                default=(getattr(f, "default_value", None) or "")[:4000] or None,
                min_length=getattr(f, "min_length", None),
                max_length=getattr(f, "max_length", None),
                custom_id=f"field_{f.id}",
            ))

    async def on_submit(self, interaction: discord.Interaction):
        answers_dict = {child.label: child.value for child in self.children}
        await process_ticket_creation(interaction, self.panel, answers_dict)


class TicketView(discord.ui.View):
    def __init__(self, panel: Panel = None):
        super().__init__(timeout=None)
        btn_label = panel.close_button_text if panel and panel.close_button_text else "Fermer le ticket"
        btn_style = get_button_style(panel.close_button_color if panel and panel.close_button_color else "danger")
        btn_emoji = panel.close_button_emoji if panel and panel.close_button_emoji else "🔒"

        button = discord.ui.Button(
            label=btn_label,
            style=btn_style,
            custom_id="close_ticket",
            emoji=btn_emoji,
        )
        button.callback = self.close_ticket
        self.add_item(button)

        reason_label = getattr(panel, "close_reason_button_text", None) if panel else None
        reason_color = getattr(panel, "close_reason_button_color", None) if panel else None
        reason_emoji = getattr(panel, "close_reason_button_emoji", None) if panel else None

        reason_btn = discord.ui.Button(
            label=reason_label or "Fermer avec raison",
            style=get_button_style(reason_color or "secondary"),
            custom_id="close_ticket_reason",
            emoji=reason_emoji or "📝",
        )
        reason_btn.callback = self.close_ticket_with_reason
        self.add_item(reason_btn)

    async def close_ticket(self, interaction: discord.Interaction):
        channel = interaction.channel
        async with AsyncSessionLocal() as session:
            ticket = await find_ticket_for_channel(session, channel)
            if not ticket:
                return await interaction.response.send_message("❌ Ticket introuvable.", ephemeral=True)
            panel = await session.get(Panel, ticket.panel_id)

        is_creator = (interaction.user.id == ticket.creator_id)
        staff_member = is_staff(interaction.user, panel)
        creator_can_close = (
            bool(panel.creator_can_close)
            if (panel and getattr(panel, "creator_can_close", None) is not None)
            else True
        )

        if not staff_member:
            if is_creator and not creator_can_close:
                return await interaction.response.send_message(
                    "❌ Vous n'êtes pas autorisé à fermer ce ticket vous-même. Un membre du staff s'en chargera.",
                    ephemeral=True,
                )
            if not is_creator:
                return await interaction.response.send_message(
                    "❌ Seuls les membres du staff (ou le créateur si autorisé) peuvent fermer ce ticket.",
                    ephemeral=True,
                )

        await execute_ticket_close(interaction, close_reason=None)

    async def close_ticket_with_reason(self, interaction: discord.Interaction):
        channel = interaction.channel
        async with AsyncSessionLocal() as session:
            ticket = await find_ticket_for_channel(session, channel)
            if not ticket:
                return await interaction.response.send_message("❌ Ticket introuvable.", ephemeral=True)
            if ticket.status == "closed":
                await remove_close_buttons_from_channel(channel)
                return await interaction.response.send_message("ℹ️ Ce ticket est déjà fermé.", ephemeral=True)
            panel = await session.get(Panel, ticket.panel_id)

        is_creator = (interaction.user.id == ticket.creator_id)
        staff_member = is_staff(interaction.user, panel)
        creator_can_close_reason = (
            bool(panel.creator_can_close_with_reason)
            if (panel and getattr(panel, "creator_can_close_with_reason", None) is not None)
            else False
        )

        if not staff_member:
            if is_creator and not creator_can_close_reason:
                return await interaction.response.send_message(
                    "❌ Seuls les membres du staff sont autorisés à utiliser la fermeture avec motif sur ce panel.",
                    ephemeral=True,
                )
            if not is_creator:
                return await interaction.response.send_message(
                    "❌ Vous n'avez pas la permission de fermer ce ticket.",
                    ephemeral=True,
                )

        await interaction.response.send_modal(CloseReasonModal())


class ConfirmDeleteTicketView(discord.ui.View):
    def __init__(self, ticket_id: Optional[int], panel_id: Optional[int], channel_id: int, user_id: int):
        super().__init__(timeout=60)
        self.ticket_id = ticket_id
        self.panel_id = panel_id
        self.channel_id = channel_id
        self.user_id = user_id

    @discord.ui.button(label="Confirmer la suppression", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ Seul l'utilisateur ayant cliqué sur Supprimer peut confirmer.",
                ephemeral=True
            )

        # Désactiver les boutons de confirmation et indiquer la suppression
        await interaction.response.edit_message(
            content="🗑️ Suppression confirmée. Suppression du salon en cours...",
            embed=None,
            view=None
        )

        channel = interaction.channel or (interaction.guild.get_channel(self.channel_id) if interaction.guild else None)
        guild = interaction.guild

        ticket = None
        panel = None
        async with AsyncSessionLocal() as session:
            if self.ticket_id:
                ticket = await session.get(Ticket, self.ticket_id)
            elif channel:
                ticket = await find_ticket_for_channel(session, channel)

            if ticket:
                panel = await session.get(Panel, ticket.panel_id)
            elif self.panel_id:
                panel = await session.get(Panel, self.panel_id)

            if not is_staff(interaction.user, panel):
                return await interaction.followup.send(
                    "❌ Seuls les membres du staff peuvent supprimer définitivement ce ticket.",
                    ephemeral=True
                )

            if ticket and channel:
                _explicitly_deleted_channel_ids.add(channel.id)
                ticket.channel_id = None
                ticket.status = "closed"
                ticket.updated_at = datetime.datetime.utcnow()
                await session.commit()

        # Envoi du log de suppression dans le salon de logs si activé
        if ticket:
            try:
                actor_disp = f"{interaction.user.mention} (`{interaction.user.display_name or interaction.user.name}`)"
                await send_ticket_log(
                    bot_client=interaction.client,
                    action="delete",
                    ticket=ticket,
                    panel=panel,
                    source="discord",
                    actor_display=actor_disp,
                    channel_id=channel.id if channel else self.channel_id,
                )
            except Exception as log_err:
                logger.warning(f"Erreur envoi log suppression ticket: {log_err}")

        if channel:
            embed = discord.Embed(
                description=f"🗑️ Ticket supprimé par {interaction.user.mention}. Fermeture dans 3 secondes...",
                color=discord.Color.red()
            )
            try:
                await channel.send(embed=embed)
            except Exception as e:
                logger.warning(f"Impossible d'envoyer le message de suppression: {e}")

            await asyncio.sleep(3)
            try:
                await channel.delete()
            except Exception as e:
                logger.warning(f"Impossible de supprimer le salon {channel.id}: {e}")

        # Rééquilibrer et nettoyer les catégories en double si on repasse <= 50 salons
        if guild and panel:
            if panel.archive_category_id:
                try:
                    await cleanup_and_rebalance_category_family(guild, panel.archive_category_id, interaction.client)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage catégorie archive après suppression: {e}")
            if panel.active_category_id:
                try:
                    await cleanup_and_rebalance_category_family(guild, panel.active_category_id, interaction.client)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage catégorie active après suppression: {e}")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ Action non autorisée.",
                ephemeral=True
            )
        await interaction.response.edit_message(
            content="✅ Suppression annulée. Le ticket est conservé.",
            embed=None,
            view=None
        )

    async def on_timeout(self):
        try:
            for child in self.children:
                child.disabled = True
        except Exception:
            pass


class ArchivedTicketView(discord.ui.View):
    def __init__(self, panel: Panel = None):
        super().__init__(timeout=None)
        reopen_label = panel.reopen_button_text if panel and panel.reopen_button_text else "Ré-ouvrir"
        reopen_style = get_button_style(panel.reopen_button_color if panel and panel.reopen_button_color else "success")
        reopen_emoji = panel.reopen_button_emoji if panel and panel.reopen_button_emoji else "🔓"

        delete_label = panel.delete_button_text if panel and panel.delete_button_text else "Supprimer"
        delete_style = get_button_style(panel.delete_button_color if panel and panel.delete_button_color else "danger")
        delete_emoji = panel.delete_button_emoji if panel and panel.delete_button_emoji else "🗑️"

        reopen_btn = discord.ui.Button(
            label=reopen_label,
            style=reopen_style,
            custom_id="reopen_ticket",
            emoji=reopen_emoji
        )
        reopen_btn.callback = self.reopen
        self.add_item(reopen_btn)

        delete_btn = discord.ui.Button(
            label=delete_label,
            style=delete_style,
            custom_id="delete_ticket",
            emoji=delete_emoji
        )
        delete_btn.callback = self.delete
        self.add_item(delete_btn)

    async def reopen(self, interaction: discord.Interaction):
        await interaction.response.defer()
        channel = interaction.channel
        async with AsyncSessionLocal() as session:
            ticket = await find_ticket_for_channel(session, channel)
            
            if not ticket:
                logger.warning(f"[{INSTANCE_ID}] reopen_ticket: aucun ticket trouvé pour le salon #{channel.name} ({channel.id})")
                return await interaction.followup.send("❌ Ticket introuvable.", ephemeral=True)

            panel = await session.get(Panel, ticket.panel_id)

            # SÉCURITÉ : Seuls les membres du staff peuvent ré-ouvrir
            if not is_staff(interaction.user, panel):
                return await interaction.followup.send(
                    "❌ Seuls les membres du staff peuvent ré-ouvrir ce ticket.", 
                    ephemeral=True
                )

            ticket.status = "open"
            ticket.close_reason = None
            ticket.closed_by_username = None
            ticket.updated_at = datetime.datetime.utcnow()
            await session.commit()
            
            # Restaurer les permissions du créateur
            member = interaction.guild.get_member(ticket.creator_id)
            if not member:
                try:
                    member = await interaction.guild.fetch_member(ticket.creator_id)
                except Exception:
                    member = None
            if member:
                await channel.set_permissions(
                    member, read_messages=True, send_messages=True, attach_files=True, embed_links=True
                )
            
            # Déplacer vers la catégorie active si configurée (avec gestion du débordement ticket (2), etc.)
            if panel and panel.active_category_id:
                try:
                    await move_channel_with_overflow(channel, panel.active_category_id, interaction.client)
                except Exception as e:
                    logger.warning(f"Erreur déplacement catégorie active: {e}")

            # Rééquilibrer et nettoyer la catégorie d'archive (supprime archive (2) si on repasse <= 50 salons)
            if panel and panel.archive_category_id:
                try:
                    await cleanup_and_rebalance_category_family(interaction.guild, panel.archive_category_id, interaction.client)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage catégorie archive: {e}")

        # Enlever TOUS les boutons d'archive du salon
        await remove_archive_buttons_from_channel(channel)

        # Envoyer le message de réouverture avec les boutons Fermer / Fermer avec raison
        await channel.send(f"🔓 Ticket réouvert par {interaction.user.mention}", view=TicketView(panel))

        # Envoi du log de ré-ouverture dans le salon de logs si activé
        try:
            actor_disp = f"{interaction.user.mention} (`{interaction.user.display_name or interaction.user.name}`)"
            await send_ticket_log(
                bot_client=interaction.client,
                action="reopen",
                ticket=ticket,
                panel=panel,
                source="discord",
                actor_display=actor_disp,
                recreated=False,
                channel_id=channel.id,
            )
        except Exception as log_err:
            logger.warning(f"Erreur envoi log ré-ouverture ticket: {log_err}")

    async def delete(self, interaction: discord.Interaction):
        channel = interaction.channel

        # SÉCURITÉ : Seuls les membres du staff peuvent supprimer définitivement
        async with AsyncSessionLocal() as session:
            ticket = await find_ticket_for_channel(session, channel)
            panel = await session.get(Panel, ticket.panel_id) if ticket else None

            if not is_staff(interaction.user, panel):
                if interaction.response.is_done():
                    return await interaction.followup.send(
                        "❌ Seuls les membres du staff peuvent supprimer définitivement ce ticket.", 
                        ephemeral=True
                    )
                else:
                    return await interaction.response.send_message(
                        "❌ Seuls les membres du staff peuvent supprimer définitivement ce ticket.", 
                        ephemeral=True
                    )

        # Étape de confirmation pour éviter les miss-clicks
        embed = discord.Embed(
            title="⚠️ Confirmation de suppression",
            description=(
                f"Êtes-vous sûr de vouloir supprimer définitivement le ticket **#{getattr(channel, 'name', 'ce-ticket')}** ?\n\n"
                f"• Le salon Discord sera supprimé.\n"
                f"• L'historique et l'archive HTML restent consultables sur le site web.\n"
                f"• Vous pourrez recréer le salon à tout moment depuis le site web si besoin."
            ),
            color=0xED4245
        )
        view = ConfirmDeleteTicketView(
            ticket_id=ticket.id if ticket else None,
            panel_id=panel.id if panel else None,
            channel_id=channel.id,
            user_id=interaction.user.id
        )

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class TicketingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guild_messages = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(TicketView())
        self.add_view(ArchivedTicketView())

    async def on_ready(self):
        logger.info(
            f"✅ Bot connecté en tant que {self.user} (instance '{INSTANCE_ID}', "
            f"{len(self.guilds)} serveur(s)). ⚠️ Une seule instance doit utiliser ce token."
        )

bot = TicketingBot()


@bot.listen("on_guild_channel_delete")
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Synchronise la BDD, envoie un log si supprimé manuellement sur Discord, et nettoie les catégories de débordement."""
    if not isinstance(channel, discord.TextChannel):
        return
    was_explicit = channel.id in _explicitly_deleted_channel_ids
    _explicitly_deleted_channel_ids.discard(channel.id)
    try:
        panel = None
        ticket = None
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
            ticket = res.scalar_one_or_none()
            if ticket:
                panel = await session.get(Panel, ticket.panel_id) if ticket.panel_id else None
                ticket.channel_id = None
                ticket.status = "closed"
                ticket.updated_at = datetime.datetime.utcnow()
                await session.commit()
                logger.info(f"🗑️ Salon #{channel.name} ({channel.id}) supprimé sur Discord -> ticket #{ticket.id} marqué sans salon.")

        if ticket and not was_explicit:
            actor_disp = "Suppression manuelle sur Discord"
            try:
                async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
                    if entry.target and getattr(entry.target, "id", None) == channel.id and entry.user:
                        actor_disp = f"{entry.user.mention} (`{entry.user.display_name or entry.user.name}`)"
                        break
            except Exception:
                pass
            await send_ticket_log(
                bot_client=bot,
                action="delete",
                ticket=ticket,
                panel=panel,
                source="discord",
                actor_display=actor_disp,
                channel_id=channel.id,
            )

        if panel and channel.guild:
            if panel.archive_category_id:
                await cleanup_and_rebalance_category_family(channel.guild, panel.archive_category_id, bot)
            if panel.active_category_id:
                await cleanup_and_rebalance_category_family(channel.guild, panel.active_category_id, bot)
    except Exception as e:
        logger.warning(f"Erreur dans on_guild_channel_delete: {e}")


@bot.listen("on_interaction")
async def on_interaction(interaction: discord.Interaction):
    # --- 1. SOUMISSION DE LA MODALE (QUESTIONNAIRE) ---
    if interaction.type == discord.InteractionType.modal_submit:
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("ticket_modal_"):
            return
        try:
            panel_id = int(custom_id.split("_")[-1])
        except (IndexError, ValueError):
            return

        logger.info(f"[{INSTANCE_ID}] Soumission modale ticket_modal_{panel_id} par {interaction.user} ({interaction.user.id})")
        try:
            async with AsyncSessionLocal() as session:
                panel = await session.get(Panel, panel_id)
                if not panel:
                    return await interaction.response.send_message("❌ Ce panneau n'existe plus.", ephemeral=True)
                res = await session.execute(
                    select(FormField)
                    .where(FormField.panel_id == panel_id)
                    .order_by(FormField.order.asc(), FormField.id.asc())
                )
                fields = res.scalars().all()

            fields_by_cid = {f"field_{f.id}": f for f in fields}
            answers_dict = {}
            uploaded_attachments = []
            resolved_attachments = ((interaction.data or {}).get("resolved") or {}).get("attachments") or {}

            raw_components = (interaction.data or {}).get("components", [])
            for idx, top_comp in enumerate(raw_components):
                comp_type = top_comp.get("type")
                if comp_type == 18:  # ComponentType.LABEL
                    inner = top_comp.get("component") or {}
                    fallback_label = top_comp.get("label") or f"Question {idx + 1}"
                else:  # ActionRow (type 1)
                    children = top_comp.get("components") or []
                    inner = children[0] if children else {}
                    fallback_label = f"Question {idx + 1}"

                cid = inner.get("custom_id", "")
                field_obj = fields_by_cid.get(cid)
                label = field_obj.label if field_obj and field_obj.label else fallback_label
                ftype = get_effective_field_type(field_obj) if field_obj else "short_text"
                inner_type = inner.get("type")

                if inner_type == 19 or ftype == "file_upload":
                    vals = inner.get("values") or []
                    file_links = []
                    for att_id in vals:
                        att_info = resolved_attachments.get(str(att_id)) or {}
                        fname = att_info.get("filename") or f"fichier_{att_id}"
                        furl = att_info.get("url") or att_info.get("proxy_url") or ""
                        if furl:
                            uploaded_attachments.append({
                                "label": label,
                                "filename": fname,
                                "url": furl,
                                "content_type": att_info.get("content_type", ""),
                            })
                            file_links.append(f"📎 [{fname}]({furl})")
                        else:
                            file_links.append(f"📎 {fname}")
                    formatted = "\n".join(file_links)
                elif "values" in inner:
                    vals = inner.get("values") or []
                    if ftype == "user_select":
                        formatted = ", ".join(f"<@{v}>" for v in vals)
                    elif ftype == "role_select":
                        formatted = ", ".join(f"<@&{v}>" for v in vals)
                    elif ftype == "channel_select":
                        formatted = ", ".join(f"<#{v}>" for v in vals)
                    else:
                        # StringSelect : retrouver le label/emoji d'origine si configuré
                        opt_map = {}
                        if field_obj and getattr(field_obj, "options", None):
                            for o in field_obj.options:
                                if isinstance(o, dict) and o.get("label"):
                                    k = str(o.get("value") or o.get("label")).strip()
                                    em = str(o.get("emoji") or "").strip()
                                    opt_map[k] = f"{em} {o['label']}".strip() if em else str(o["label"]).strip()
                        formatted = ", ".join(opt_map.get(str(v), str(v)) for v in vals)
                else:
                    formatted = str(inner.get("value") or "").strip()

                answers_dict[label] = formatted if formatted else "*Non renseigné*"

            await process_ticket_creation(interaction, panel, answers_dict, uploaded_attachments=uploaded_attachments)
        except (discord.InteractionResponded, discord.NotFound) as e:
            logger.warning(f"[{INSTANCE_ID}] Soumission ticket_modal_{panel_id} déjà traitée ou expirée : {e}")
        except Exception as e:
            logger.exception(f"[{INSTANCE_ID}] Erreur lors du traitement de ticket_modal_{panel_id}: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Une erreur est survenue lors de la création du ticket.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Une erreur est survenue lors de la création du ticket.", ephemeral=True)
            except Exception:
                pass
        return

    # --- 2. CLIC SUR LE BOUTON D'OUVERTURE DU PANEL ---
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get('custom_id', '')
    if not custom_id.startswith("panel_"):
        return

    try:
        panel_id = int(custom_id.split("_")[1])
    except (IndexError, ValueError):
        return

    logger.info(f"[{INSTANCE_ID}] Clic sur panel_{panel_id} par {interaction.user} ({interaction.user.id})")

    try:
        async with AsyncSessionLocal() as session:
            panel = await session.get(Panel, panel_id)
            if not panel:
                logger.warning(
                    f"[{INSTANCE_ID}] panel_{panel_id} absent de la base de cette instance. "
                    f"Si le clic fonctionne parfois, une AUTRE instance du bot tourne avec le même token."
                )
                await asyncio.sleep(2)
                try:
                    await interaction.response.send_message(
                        f"❌ Ce panneau n'existe plus (ID {panel_id}). Il a probablement été supprimé : "
                        f"redéployez un panel à jour depuis le site.",
                        ephemeral=True
                    )
                except (discord.InteractionResponded, discord.HTTPException):
                    pass
                return

            # Vérification du nombre maximal de tickets ouverts par utilisateur
            max_tickets = getattr(panel, "max_tickets_per_user", 1) or 1
            if max_tickets > 0:
                res_open = await session.execute(
                    select(func.count(Ticket.id)).where(
                        Ticket.panel_id == panel_id,
                        Ticket.creator_id == interaction.user.id,
                        Ticket.status == "open"
                    )
                )
                open_count = res_open.scalar() or 0
                if open_count >= max_tickets:
                    return await interaction.response.send_message(
                        f"❌ Vous avez déjà {open_count} ticket(s) ouvert(s) pour ce panel (maximum autorisé : {max_tickets}).",
                        ephemeral=True
                    )

            res = await session.execute(
                select(FormField)
                .where(FormField.panel_id == panel_id)
                .order_by(FormField.order.asc(), FormField.id.asc())
            )
            fields = res.scalars().all()

        if not fields:
            # Si aucune question n'est configurée, ouvre directement le ticket sans pop-up
            return await process_ticket_creation(interaction, panel, {})

        has_advanced_components = any(
            get_effective_field_type(f) in ("select", "user_select", "role_select", "channel_select", "file_upload")
            for f in fields
        )
        if not has_advanced_components:
            # Questions standard 100% textuelles -> FormModal natif discord.ui (100% stable & compatible)
            await interaction.response.send_modal(FormModal(panel, fields))
        else:
            # Questions avancées (listes déroulantes, upload de fichiers...) -> modal enrichi
            await send_custom_modal(interaction, panel, fields)
    except (discord.InteractionResponded, discord.NotFound) as e:
        logger.warning(f"[{INSTANCE_ID}] Interaction panel_{panel_id} non traitée : {e}")
    except Exception as e:
        logger.exception(f"[{INSTANCE_ID}] Erreur lors du clic sur panel_{panel_id}: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Une erreur est survenue, veuillez réessayer.", ephemeral=True)
        except Exception:
            pass


# --- DÉTECTION EN TEMPS RÉEL DE LA SUPPRESSION DE MESSAGES OU SALONS SUR DISCORD ---
@bot.listen("on_raw_message_delete")
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    """Détecte quand un message de panel ou d'info-embed déployé est supprimé manuellement sur Discord."""
    msg_id = payload.message_id
    try:
        async with AsyncSessionLocal() as session:
            # 1. Panel
            res = await session.execute(
                select(Panel).where(Panel.deployed_message_id == msg_id)
            )
            panel = res.scalars().first()
            if panel:
                logger.info(f"[{INSTANCE_ID}] Message déployé {msg_id} du panel '{panel.name}' (ID {panel.id}) supprimé sur Discord. Statut réinitialisé à non déployé.")
                panel.deployed_channel_id = None
                panel.deployed_message_id = None
                await session.commit()

            # 2. InfoEmbed (ex: Comment Vendre)
            res_ie = await session.execute(
                select(InfoEmbed).where(InfoEmbed.deployed_message_id == msg_id)
            )
            ie = res_ie.scalars().first()
            if ie:
                logger.info(f"[{INSTANCE_ID}] Message déployé {msg_id} du guide '{ie.key}' supprimé sur Discord. Statut réinitialisé.")
                ie.deployed_message_id = None
                await session.commit()

            # 3. Announcement
            res_ann = await session.execute(
                select(Announcement).where(Announcement.message_id == msg_id)
            )
            ann = res_ann.scalars().first()
            if ann:
                logger.info(f"[{INSTANCE_ID}] Message d'annonce {msg_id} ('{ann.title}') supprimé sur Discord. Statut mis à jour.")
                ann.status = "deleted"
                ann.message_id = None
                await session.commit()
    except Exception as e:
        logger.error(f"[{INSTANCE_ID}] Erreur dans on_raw_message_delete: {e}")


@bot.listen("on_raw_bulk_message_delete")
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    """Détecte quand des messages de panels ou info-embeds sont supprimés en groupe sur Discord."""
    try:
        async with AsyncSessionLocal() as session:
            # 1. Panels
            res = await session.execute(
                select(Panel).where(Panel.deployed_message_id.in_(payload.message_ids))
            )
            panels = res.scalars().all()
            if panels:
                for panel in panels:
                    logger.info(f"[{INSTANCE_ID}] Message déployé {panel.deployed_message_id} du panel '{panel.name}' supprimé en groupe. Statut réinitialisé.")
                    panel.deployed_channel_id = None
                    panel.deployed_message_id = None
                await session.commit()

            # 2. InfoEmbeds
            res_ie = await session.execute(
                select(InfoEmbed).where(InfoEmbed.deployed_message_id.in_(payload.message_ids))
            )
            ies = res_ie.scalars().all()
            if ies:
                for ie in ies:
                    logger.info(f"[{INSTANCE_ID}] Message déployé {ie.deployed_message_id} du guide '{ie.key}' supprimé en groupe. Statut réinitialisé.")
                    ie.deployed_message_id = None
                await session.commit()

            # 3. Announcements
            res_ann = await session.execute(
                select(Announcement).where(Announcement.message_id.in_(payload.message_ids))
            )
            anns = res_ann.scalars().all()
            if anns:
                for a in anns:
                    logger.info(f"[{INSTANCE_ID}] Message d'annonce {a.message_id} ('{a.title}') supprimé en groupe. Statut mis à jour.")
                    a.status = "deleted"
                    a.message_id = None
                await session.commit()
    except Exception as e:
        logger.error(f"[{INSTANCE_ID}] Erreur dans on_raw_bulk_message_delete: {e}")


@bot.listen("on_guild_channel_delete")
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Détecte quand un salon contenant un panel ou info-embed déployé est supprimé sur Discord."""
    try:
        async with AsyncSessionLocal() as session:
            # 1. Panels
            res = await session.execute(
                select(Panel).where(Panel.deployed_channel_id == channel.id)
            )
            panels = res.scalars().all()
            if panels:
                for panel in panels:
                    logger.info(f"[{INSTANCE_ID}] Salon #{channel.name} ({channel.id}) supprimé sur Discord. Panel '{panel.name}' réinitialisé.")
                    panel.deployed_channel_id = None
                    panel.deployed_message_id = None
                await session.commit()

            # 2. InfoEmbeds
            res_ie = await session.execute(
                select(InfoEmbed).where(InfoEmbed.channel_id == channel.id)
            )
            ies = res_ie.scalars().all()
            if ies:
                for ie in ies:
                    logger.info(f"[{INSTANCE_ID}] Salon #{channel.name} ({channel.id}) supprimé sur Discord. Guide '{ie.key}' réinitialisé.")
                    ie.channel_id = None
                    ie.deployed_message_id = None
                await session.commit()

            # 3. Announcements
            res_ann = await session.execute(
                select(Announcement).where(Announcement.channel_id == channel.id)
            )
            anns = res_ann.scalars().all()
            if anns:
                for a in anns:
                    logger.info(f"[{INSTANCE_ID}] Salon #{channel.name} ({channel.id}) supprimé sur Discord. Annonce '{a.title}' marquée supprimée.")
                    a.status = "deleted"
                    a.message_id = None
                await session.commit()
    except Exception as e:
        logger.error(f"[{INSTANCE_ID}] Erreur dans on_guild_channel_delete: {e}")


