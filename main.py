import asyncio
import os
import logging
import base64
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Security, Request, Query
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager
import uvicorn
from dotenv import load_dotenv
import discord
import datetime
from sqlalchemy.future import select
from sqlalchemy import func, text, delete, or_

from database import engine, Base, AsyncSessionLocal, Panel, FormField, Ticket, TicketArchive, InfoEmbed, Announcement, BotSettings
from bot import bot, PanelView, build_panel_embed, build_info_embed, build_announcement_embed, send_ticket_log, _explicitly_deleted_channel_ids
from schemas import PanelCreate, FieldCreate, BotProfileUpdate, InfoEmbedUpdate, AnnouncementCreate, AnnouncementUpdate
from migrator import auto_migrate_db

load_dotenv(override=True)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Security(api_key_header)):
    load_dotenv(override=True)
    expected_key = os.getenv("API_KEY")
    if not expected_key:
        raise HTTPException(status_code=500, detail="API_KEY non configurée.")
    if api_key == expected_key:
        return api_key
    raise HTTPException(status_code=403, detail="Clé API invalide.")

def panel_to_dict(p: Panel) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "embed_title": p.embed_title,
        "embed_title_url": getattr(p, "embed_title_url", None) or "",
        "embed_description": p.embed_description,
        "embed_color": p.embed_color,
        "embed_author_name": getattr(p, "embed_author_name", None) or "",
        "embed_author_icon_url": getattr(p, "embed_author_icon_url", None) or "",
        "embed_author_url": getattr(p, "embed_author_url", None) or "",
        "embed_thumbnail_url": getattr(p, "embed_thumbnail_url", None) or "",
        "embed_image_url": getattr(p, "embed_image_url", None) or "",
        "embed_footer_text": getattr(p, "embed_footer_text", None) or "Système de Ticketing",
        "embed_footer_icon_url": getattr(p, "embed_footer_icon_url", None) or "",
        "embed_show_timestamp": bool(p.embed_show_timestamp) if getattr(p, "embed_show_timestamp", None) is not None else False,
        "button_text": p.button_text,
        "button_color": p.button_color,
        "button_emoji": p.button_emoji,
        "active_category_id": str(p.active_category_id) if p.active_category_id else None,
        "archive_category_id": str(p.archive_category_id) if p.archive_category_id else None,
        "ping_role_id": str(p.ping_role_id) if p.ping_role_id else None,
        "ping_creator": bool(p.ping_creator) if p.ping_creator is not None else True,
        "max_tickets_per_user": p.max_tickets_per_user if p.max_tickets_per_user is not None else 1,
        "intro_embed_title": getattr(p, "intro_embed_title", None) or "Bienvenue dans votre ticket",
        "intro_message": p.intro_message,
        "intro_embed_color": getattr(p, "intro_embed_color", None) or "",
        "intro_thumbnail_url": getattr(p, "intro_thumbnail_url", None) or "",
        "intro_image_url": getattr(p, "intro_image_url", None) or "",
        "intro_footer_text": getattr(p, "intro_footer_text", None) or "",
        "inactivity_days": p.inactivity_days,
        "deployed_channel_id": str(p.deployed_channel_id) if p.deployed_channel_id else None,
        "deployed_message_id": str(p.deployed_message_id) if p.deployed_message_id else None,
        "close_button_text": p.close_button_text or "Fermer le ticket",
        "close_button_color": p.close_button_color or "danger",
        "close_button_emoji": p.close_button_emoji or "🔒",
        "close_reason_button_text": getattr(p, "close_reason_button_text", None) or "Fermer avec raison",
        "close_reason_button_color": getattr(p, "close_reason_button_color", None) or "secondary",
        "close_reason_button_emoji": getattr(p, "close_reason_button_emoji", None) or "📝",
        "reopen_button_text": p.reopen_button_text or "Ré-ouvrir",
        "reopen_button_color": p.reopen_button_color or "success",
        "reopen_button_emoji": p.reopen_button_emoji or "🔓",
        "delete_button_text": p.delete_button_text or "Supprimer",
        "delete_button_color": p.delete_button_color or "danger",
        "delete_button_emoji": p.delete_button_emoji or "🗑️",
        "creator_can_close": bool(p.creator_can_close) if getattr(p, "creator_can_close", None) is not None else True,
        "creator_can_close_with_reason": bool(p.creator_can_close_with_reason) if getattr(p, "creator_can_close_with_reason", None) is not None else False,
        "dm_transcript_on_close": bool(p.dm_transcript_on_close) if getattr(p, "dm_transcript_on_close", None) is not None else True,
        "dm_embed_title": getattr(p, "dm_embed_title", None) or "🔒 Ticket fermé — {ticket_name}",
        "dm_embed_title_url": getattr(p, "dm_embed_title_url", None) or "",
        "dm_embed_description": getattr(p, "dm_embed_description", None) or "Votre ticket **{ticket_name}** a été clôturé par **{closed_by}**.\nVous trouverez ci-joint l'archive complète de votre conversation.",
        "dm_embed_color": getattr(p, "dm_embed_color", None) or "",
        "dm_thumbnail_url": getattr(p, "dm_thumbnail_url", None) or "",
        "dm_image_url": getattr(p, "dm_image_url", None) or "",
        "dm_footer_text": getattr(p, "dm_footer_text", None) or "Téléchargez et ouvrez le fichier .html pour relire la conversation.",
        "dm_footer_icon_url": getattr(p, "dm_footer_icon_url", None) or "",
        "dm_show_timestamp": bool(p.dm_show_timestamp) if getattr(p, "dm_show_timestamp", None) is not None else True,
        "modal_title": getattr(p, "modal_title", None) or "",
    }

def field_to_dict(f: FormField) -> dict:
    raw_type = getattr(f, "field_type", None)
    if not raw_type:
        effective_type = "paragraph" if getattr(f, "is_paragraph", False) else "short_text"
    elif raw_type == "short_text" and getattr(f, "is_paragraph", False):
        effective_type = "paragraph"
    else:
        effective_type = raw_type
    return {
        "id": f.id,
        "panel_id": f.panel_id,
        "label": f.label or "",
        "field_type": effective_type,
        "is_paragraph": effective_type == "paragraph" or bool(getattr(f, "is_paragraph", False)),
        "description": getattr(f, "description", None) or "",
        "placeholder": getattr(f, "placeholder", None) or "",
        "required": bool(f.required) if f.required is not None else True,
        "min_length": getattr(f, "min_length", None),
        "max_length": getattr(f, "max_length", None),
        "default_value": getattr(f, "default_value", None) or "",
        "options": getattr(f, "options", None) or [],
        "min_values": getattr(f, "min_values", 1) if getattr(f, "min_values", None) is not None else 1,
        "max_values": getattr(f, "max_values", 1) if getattr(f, "max_values", None) is not None else 1,
        "order": f.order if f.order is not None else 0,
    }

def ticket_to_dict(t: Ticket) -> dict:
    ch_exists = False
    if t.channel_id:
        try:
            if bot.is_ready():
                ch_exists = bot.get_channel(int(t.channel_id)) is not None
            else:
                ch_exists = True
        except Exception:
            ch_exists = False
    return {
        "id": t.id,
        "panel_id": t.panel_id,
        "channel_id": str(t.channel_id) if (t.channel_id and ch_exists) else None,
        "channel_exists": ch_exists,
        "creator_id": str(t.creator_id) if t.creator_id else None,
        "creator_username": getattr(t, "creator_username", None),
        "ticket_name": getattr(t, "ticket_name", None) or f"ticket{t.id:03d}",
        "user_ticket_number": getattr(t, "user_ticket_number", None),
        "status": t.status,
        "form_answers": t.form_answers,
        "close_reason": getattr(t, "close_reason", None),
        "closed_by_username": getattr(t, "closed_by_username", None),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }

def info_embed_to_dict(ie: InfoEmbed) -> dict:
    return {
        "id": ie.id,
        "key": ie.key,
        "title": ie.title or "Comment vendre",
        "channel_id": str(ie.channel_id) if ie.channel_id else "",
        "deployed_message_id": str(ie.deployed_message_id) if ie.deployed_message_id else None,
        "embed_title": ie.embed_title or "📖 Comment vendre votre collection ?",
        "embed_title_url": getattr(ie, "embed_title_url", None) or "",
        "embed_description": ie.embed_description or "",
        "embed_color": ie.embed_color or "#5865F2",
        "embed_author_name": getattr(ie, "embed_author_name", None) or "",
        "embed_author_icon_url": getattr(ie, "embed_author_icon_url", None) or "",
        "embed_author_url": getattr(ie, "embed_author_url", None) or "",
        "embed_thumbnail_url": getattr(ie, "embed_thumbnail_url", None) or "",
        "embed_image_url": getattr(ie, "embed_image_url", None) or "",
        "embed_footer_text": getattr(ie, "embed_footer_text", None) or "BRB Collection • Guide Officiel",
        "embed_footer_icon_url": getattr(ie, "embed_footer_icon_url", None) or "",
        "embed_show_timestamp": bool(ie.embed_show_timestamp) if getattr(ie, "embed_show_timestamp", None) is not None else False,
        "updated_at": ie.updated_at.isoformat() if ie.updated_at else None,
    }

def announcement_to_dict(ann: Announcement) -> dict:
    return {
        "id": ann.id,
        "title": ann.title or "Nouvelle Annonce",
        "channel_id": str(ann.channel_id) if ann.channel_id else "",
        "message_id": str(ann.message_id) if ann.message_id else None,
        "content": ann.content or "",
        "embed_title": ann.embed_title or "",
        "embed_title_url": ann.embed_title_url or "",
        "embed_description": ann.embed_description or "",
        "embed_color": ann.embed_color or "#5865F2",
        "embed_author_name": ann.embed_author_name or "",
        "embed_author_icon_url": ann.embed_author_icon_url or "",
        "embed_author_url": ann.embed_author_url or "",
        "embed_thumbnail_url": ann.embed_thumbnail_url or "",
        "embed_image_url": ann.embed_image_url or "",
        "embed_footer_text": ann.embed_footer_text or "",
        "embed_footer_icon_url": ann.embed_footer_icon_url or "",
        "embed_show_timestamp": bool(ann.embed_show_timestamp) if getattr(ann, "embed_show_timestamp", None) is not None else True,
        "status": ann.status or "sent",
        "created_at": ann.created_at.isoformat() if ann.created_at else None,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Synchronisation automatique et sécurisée du schéma de la BDD avec retry
    for attempt in range(1, 16):
        try:
            await auto_migrate_db(engine, Base)
            break
        except Exception as e:
            if attempt == 15:
                logger.error(f"[Startup] Impossible de se connecter à la DB après 15 tentatives: {e}")
                raise
            logger.warning(f"[Startup] Base de données pas encore prête (tentative {attempt}/15): {e}. Réessai dans 2s...")
            await asyncio.sleep(2)
    
    # Création d'un Panel par défaut (WTB) si aucun n'existe
    for attempt in range(1, 10):
        try:
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(Panel))
                if not res.scalars().first():
                    session.add(Panel(
                        name="WTB",
                        embed_title="WTB",
                        embed_description="Vendre vos items / cartes",
                        embed_color="#2ecc71",
                        button_text="Proposer des articles",
                        button_color="success",
                        button_emoji="📦",
                        intro_message="Veuillez dans un premier temps nous lister tous vos items. Ensuite le staff reviendra vers vous rapidement.\nPour fermer le ticket appuyez sur close."
                    ))
                    await session.commit()
            break
        except Exception as e:
            if attempt == 9:
                logger.error(f"[Startup] Erreur création panel par défaut: {e}")
                raise
            await asyncio.sleep(2)

    # Initialisation du message "Comment vendre" (InfoEmbed) par défaut si inexistant
    for attempt in range(1, 10):
        try:
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(InfoEmbed).where(InfoEmbed.key == "how_to_sell"))
                if not res.scalars().first():
                    session.add(InfoEmbed(
                        key="how_to_sell",
                        title="Comment vendre votre collection",
                        embed_title="📖 Comment vendre votre collection ?",
                        embed_title_url="",
                        embed_description="Bienvenue chez **BRB Collection** !\n\nVoici les étapes pour nous vendre votre collection en toute sécurité :\n\n1️⃣ **Ouvrez un ticket** en cliquant sur le bouton dans notre salon dédié.\n2️⃣ **Remplissez le formulaire** en indiquant la liste de vos pièces et joignez vos photos.\n3️⃣ **Recevez notre offre** d'achat sous 24 à 48h ouvrées.\n4️⃣ **Expédition & Paiement** immédiat après réception et contrôle !\n\nPour en savoir plus, consultez notre [Site Web](https://monsite.com).",
                        embed_color="#5865F2",
                        embed_footer_text="BRB Collection • Guide Officiel",
                        embed_show_timestamp=False
                    ))
                    await session.commit()
            break
        except Exception as e:
            if attempt == 9:
                logger.error(f"[Startup] Erreur création InfoEmbed par défaut: {e}")
                raise
            await asyncio.sleep(2)
            
    load_dotenv(override=True)
    token = os.getenv("DISCORD_BOT_TOKEN")
    bot_task = None
    if token:
        bot_task = asyncio.create_task(bot.start(token.strip()))
    
    yield

    try:
        if not bot.is_closed():
            await bot.close()
        if bot_task and not bot_task.done():
            bot_task.cancel()
    except Exception as e:
        logger.warning(f"[Shutdown] Erreur fermeture bot: {e}")

app = FastAPI(title="Discord Ticket API", lifespan=lifespan)

# --- LISTER LES SALONS, CATÉGORIES ET RÔLES POUR LE FRONTEND ---
@app.get("/api/channels", dependencies=[Depends(get_api_key)])
async def get_discord_channels():
    """Récupère tous les serveurs, salons textuels, catégories et rôles."""
    data = []
    for guild in bot.guilds:
        channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]
        categories = [{"id": str(c.id), "name": c.name} for c in guild.categories]
        roles = [{"id": str(r.id), "name": r.name} for r in guild.roles if not r.is_default()]
        data.append({
            "guild_id": str(guild.id), 
            "guild_name": guild.name, 
            "channels": channels,
            "categories": categories,
            "roles": roles
        })
    return data

# --- PROFIL DU BOT DISCORD (NOM ET AVATAR) ---
@app.get("/api/bot/profile", dependencies=[Depends(get_api_key)])
async def get_bot_profile():
    """Récupère le nom et l'avatar actuels du bot Discord."""
    if not bot.is_ready() or not bot.user:
        raise HTTPException(status_code=503, detail="Le bot Discord n'est pas encore prêt ou connecté.")
    
    avatar_url = str(bot.user.display_avatar.url) if bot.user.display_avatar else None
    return {
        "id": str(bot.user.id),
        "username": bot.user.name,
        "avatar_url": avatar_url
    }

@app.post("/api/bot/profile", dependencies=[Depends(get_api_key)])
async def update_bot_profile(profile_data: BotProfileUpdate):
    """Met à jour le nom et/ou l'avatar du bot Discord."""
    if not bot.is_ready() or not bot.user:
        raise HTTPException(status_code=503, detail="Le bot Discord n'est pas encore prêt ou connecté.")

    edit_kwargs = {}
    
    # Mise à jour du nom d'utilisateur
    if profile_data.username and profile_data.username.strip():
        new_username = profile_data.username.strip()
        if new_username != bot.user.name:
            edit_kwargs["username"] = new_username

    # Mise à jour de l'avatar
    if profile_data.avatar_base64:
        try:
            b64_str = profile_data.avatar_base64
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            edit_kwargs["avatar"] = base64.b64decode(b64_str)
        except Exception as e:
            logger.error(f"Erreur décodage base64 avatar: {e}")
            raise HTTPException(status_code=400, detail="Format d'image base64 invalide.")

    if not edit_kwargs:
        return {
            "id": str(bot.user.id),
            "username": bot.user.name,
            "avatar_url": str(bot.user.display_avatar.url) if bot.user.display_avatar else None,
            "message": "Aucune modification demandée."
        }

    try:
        await bot.user.edit(**edit_kwargs)
        logger.info(f"Profil du bot Discord mis à jour: {list(edit_kwargs.keys())}")
        return {
            "id": str(bot.user.id),
            "username": bot.user.name,
            "avatar_url": str(bot.user.display_avatar.url) if bot.user.display_avatar else None,
            "message": "Profil du bot mis à jour avec succès !"
        }
    except discord.HTTPException as exc:
        logger.error(f"Erreur Discord API lors de l'édition du profil: {exc}")
        if exc.status == 429:
            raise HTTPException(
                status_code=429, 
                detail="Discord limite le changement de nom ou d'avatar (max 2 changements de nom par heure). Veuillez patienter avant de réessayer."
            )
        error_msg = exc.text if hasattr(exc, "text") and exc.text else str(exc)
        raise HTTPException(status_code=exc.status or 400, detail=f"Erreur Discord : {error_msg}")
    except Exception as exc:
        logger.error(f"Erreur inattendue lors de l'édition du profil: {exc}")
        raise HTTPException(status_code=500, detail=f"Erreur serveur : {str(exc)}")


# --- GUIDES & MESSAGES D'INFORMATION PERMANENTS (ex: FAQ, Règlement, Guides personnalisés...) ---
async def _handle_get_all_info_embeds():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(InfoEmbed).where(InfoEmbed.key != "how_to_sell").order_by(InfoEmbed.id.asc())
        )
        items = res.scalars().all()

        # Vérification en arrière-plan de l'existence des messages sur Discord
        if bot.is_ready():
            for ie in items:
                if ie.channel_id and ie.deployed_message_id:
                    try:
                        ch_id = int(str(ie.channel_id).strip())
                        msg_id = int(str(ie.deployed_message_id).strip())
                        ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                        if ch:
                            await ch.fetch_message(msg_id)
                    except (discord.NotFound, discord.Forbidden):
                        ie.deployed_message_id = None
                        await session.commit()
                    except Exception:
                        pass
        return [info_embed_to_dict(ie) for ie in items]

@app.get("/api/info-embeds", dependencies=[Depends(get_api_key)])
@app.get("/info-embeds", dependencies=[Depends(get_api_key)])
async def get_all_info_embeds():
    return await _handle_get_all_info_embeds()


async def _handle_create_info_embed(data: InfoEmbedUpdate):
    async with AsyncSessionLocal() as session:
        import time, uuid
        raw_key = (data.key or "").strip()
        if not raw_key:
            raw_key = f"guide_{int(time.time())}"
        
        existing = await session.execute(select(InfoEmbed).where(InfoEmbed.key == raw_key))
        if existing.scalars().first():
            raw_key = f"{raw_key}_{uuid.uuid4().hex[:6]}"

        ie = InfoEmbed(key=raw_key)
        session.add(ie)

        fields_data = data.dict(exclude={"auto_deploy", "key"})
        for field, value in fields_data.items():
            if field == "channel_id":
                if value and str(value).strip():
                    try:
                        ie.channel_id = int(str(value).strip())
                    except (ValueError, TypeError):
                        ie.channel_id = None
                else:
                    ie.channel_id = None
            else:
                setattr(ie, field, value)

        await session.commit()
        await session.refresh(ie)

        deploy_status = None
        if data.auto_deploy and ie.channel_id and bot.is_ready():
            try:
                embed = build_info_embed(ie)
                target_channel = bot.get_channel(ie.channel_id) or await bot.fetch_channel(ie.channel_id)
                if target_channel:
                    new_msg = await target_channel.send(embed=embed)
                    ie.deployed_message_id = new_msg.id
                    deploy_status = "Guide déployé avec succès sur Discord !"
                    await session.commit()
            except Exception as e:
                logger.error(f"Erreur déploiement nouveau guide: {e}")
                deploy_status = f"Enregistré, mais erreur Discord : {str(e)}"

        result = info_embed_to_dict(ie)
        if deploy_status:
            result["deploy_status"] = deploy_status
        return result

@app.post("/api/info-embeds", dependencies=[Depends(get_api_key)])
@app.post("/info-embeds", dependencies=[Depends(get_api_key)])
async def create_info_embed(data: InfoEmbedUpdate):
    return await _handle_create_info_embed(data)


async def _handle_get_info_embed(key_or_id: str):
    async with AsyncSessionLocal() as session:
        query = select(InfoEmbed).where(
            or_(InfoEmbed.id == int(key_or_id), InfoEmbed.key == str(key_or_id))
        ) if str(key_or_id).isdigit() else select(InfoEmbed).where(InfoEmbed.key == str(key_or_id))

        res = await session.execute(query)
        ie = res.scalars().first()
        if not ie:
            if str(key_or_id) == "how_to_sell":
                ie = InfoEmbed(
                    key="how_to_sell",
                    title="Comment vendre votre collection",
                    embed_title="📖 Comment vendre votre collection ?",
                    embed_title_url="",
                    embed_description="Bienvenue chez **BRB Collection** !\n\nVoici les étapes pour nous vendre votre collection en toute sécurité :\n\n1️⃣ **Ouvrez un ticket** en cliquant sur le bouton dans notre salon dédié.\n2️⃣ **Remplissez le formulaire** en indiquant la liste de vos pièces et joignez vos photos.\n3️⃣ **Recevez notre offre** d'achat sous 24 à 48h ouvrées.\n4️⃣ **Expédition & Paiement** immédiat après réception et contrôle !\n\nPour en savoir plus, consultez notre [Site Web](https://monsite.com).",
                    embed_color="#5865F2",
                    embed_footer_text="BRB Collection • Guide Officiel",
                    embed_show_timestamp=False
                )
                session.add(ie)
                await session.commit()
                await session.refresh(ie)
            else:
                raise HTTPException(status_code=404, detail="Guide introuvable.")
        
        # Vérification en direct sur Discord
        if bot.is_ready() and ie.channel_id and ie.deployed_message_id:
            try:
                ch_id = int(str(ie.channel_id).strip())
                msg_id = int(str(ie.deployed_message_id).strip())
                ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                if ch:
                    await ch.fetch_message(msg_id)
            except (discord.NotFound, discord.Forbidden):
                logger.info(f"Le message guide '{ie.key}' ({ie.deployed_message_id}) n'existe plus sur Discord. Réinitialisation.")
                ie.deployed_message_id = None
                await session.commit()
            except Exception as e:
                logger.debug(f"Vérification message guide '{ie.key}' ignorée: {e}")
        
        return info_embed_to_dict(ie)

@app.get("/api/info-embeds/{key:path}", dependencies=[Depends(get_api_key)])
@app.get("/info-embeds/{key:path}", dependencies=[Depends(get_api_key)])
async def get_info_embed(key: str):
    return await _handle_get_info_embed(key)


async def _handle_update_info_embed(key_or_id: str, data: InfoEmbedUpdate):
    async with AsyncSessionLocal() as session:
        query = select(InfoEmbed).where(
            or_(InfoEmbed.id == int(key_or_id), InfoEmbed.key == str(key_or_id))
        ) if str(key_or_id).isdigit() else select(InfoEmbed).where(InfoEmbed.key == str(key_or_id))

        res = await session.execute(query)
        ie = res.scalars().first()
        if not ie:
            if not str(key_or_id).isdigit():
                ie = InfoEmbed(key=key_or_id)
                session.add(ie)
            else:
                raise HTTPException(status_code=404, detail="Guide introuvable.")

        old_channel_id = ie.channel_id
        old_message_id = ie.deployed_message_id

        # Mise à jour des champs
        fields_data = data.dict(exclude={"auto_deploy", "key"})
        for field, value in fields_data.items():
            if field == "channel_id":
                if value and str(value).strip():
                    try:
                        ie.channel_id = int(str(value).strip())
                    except (ValueError, TypeError):
                        ie.channel_id = None
                else:
                    ie.channel_id = None
            else:
                setattr(ie, field, value)

        await session.commit()
        await session.refresh(ie)

        deploy_status = None
        # Auto-déploiement ou mise à jour immédiate si un salon est spécifié
        if data.auto_deploy and ie.channel_id and bot.is_ready():
            try:
                embed = build_info_embed(ie)
                target_channel = bot.get_channel(ie.channel_id) or await bot.fetch_channel(ie.channel_id)

                if target_channel:
                    edited = False
                    # Cas 1 : Même salon et message déjà déployé -> Édition du message existant !
                    if old_message_id and old_channel_id == ie.channel_id:
                        try:
                            msg = await target_channel.fetch_message(old_message_id)
                            if msg:
                                await msg.edit(embed=embed)
                                ie.deployed_message_id = msg.id
                                edited = True
                                deploy_status = "Message mis à jour avec succès sur Discord !"
                                logger.info(f"Guide '{ie.key}' édité en direct sur message {msg.id}")
                        except (discord.NotFound, discord.Forbidden):
                            edited = False
                    
                    # Cas 2 : Changement de salon ou message non trouvé -> suppression ancien et envoi nouveau
                    if not edited:
                        if old_message_id and old_channel_id and old_channel_id != ie.channel_id:
                            try:
                                old_ch = bot.get_channel(old_channel_id) or await bot.fetch_channel(old_channel_id)
                                if old_ch:
                                    old_msg = await old_ch.fetch_message(old_message_id)
                                    if old_msg:
                                        await old_msg.delete()
                            except Exception as e:
                                logger.debug(f"Suppression ancien message guide ignorée: {e}")
                        
                        new_msg = await target_channel.send(embed=embed)
                        ie.deployed_message_id = new_msg.id
                        deploy_status = "Guide déployé avec succès sur Discord !"
                        logger.info(f"Guide '{ie.key}' posté sur salon #{target_channel.name} ({new_msg.id})")
                    
                    await session.commit()
            except Exception as e:
                logger.error(f"Erreur lors du déploiement du guide '{ie.key}': {e}")
                deploy_status = f"Enregistré, mais erreur Discord : {str(e)}"

        result = info_embed_to_dict(ie)
        if deploy_status:
            result["deploy_status"] = deploy_status
        return result

@app.put("/api/info-embeds/{key:path}", dependencies=[Depends(get_api_key)])
@app.put("/info-embeds/{key:path}", dependencies=[Depends(get_api_key)])
async def update_info_embed(key: str, data: InfoEmbedUpdate):
    return await _handle_update_info_embed(key, data)


async def _handle_undeploy_info_embed(key_or_id: str):
    async with AsyncSessionLocal() as session:
        query = select(InfoEmbed).where(
            or_(InfoEmbed.id == int(key_or_id), InfoEmbed.key == str(key_or_id))
        ) if str(key_or_id).isdigit() else select(InfoEmbed).where(InfoEmbed.key == str(key_or_id))

        res = await session.execute(query)
        ie = res.scalars().first()
        if not ie:
            raise HTTPException(status_code=404, detail="Guide introuvable.")

        if ie.channel_id and ie.deployed_message_id and bot.is_ready():
            try:
                ch = bot.get_channel(ie.channel_id) or await bot.fetch_channel(ie.channel_id)
                if ch:
                    msg = await ch.fetch_message(ie.deployed_message_id)
                    if msg:
                        await msg.delete()
                        logger.info(f"Message Guide '{ie.key}' ({ie.deployed_message_id}) supprimé sur Discord.")
            except (discord.NotFound, discord.Forbidden):
                pass
            except Exception as e:
                logger.warning(f"Erreur suppression message Guide: {e}")

        ie.deployed_message_id = None
        await session.commit()
        return info_embed_to_dict(ie)

@app.post("/api/info-embeds/{key:path}/undeploy", dependencies=[Depends(get_api_key)])
@app.post("/info-embeds/{key:path}/undeploy", dependencies=[Depends(get_api_key)])
async def undeploy_info_embed(key: str):
    return await _handle_undeploy_info_embed(key)


async def _handle_delete_info_embed(key_or_id: str):
    async with AsyncSessionLocal() as session:
        query = select(InfoEmbed).where(
            or_(InfoEmbed.id == int(key_or_id), InfoEmbed.key == str(key_or_id))
        ) if str(key_or_id).isdigit() else select(InfoEmbed).where(InfoEmbed.key == str(key_or_id))

        res = await session.execute(query)
        ie = res.scalars().first()
        if not ie:
            raise HTTPException(status_code=404, detail="Guide introuvable.")

        if ie.channel_id and ie.deployed_message_id and bot.is_ready():
            try:
                ch = bot.get_channel(ie.channel_id) or await bot.fetch_channel(ie.channel_id)
                if ch:
                    msg = await ch.fetch_message(ie.deployed_message_id)
                    if msg:
                        await msg.delete()
                        logger.info(f"Message Guide '{ie.key}' supprimé sur Discord lors de la suppression.")
            except Exception as e:
                logger.warning(f"Erreur suppression message Discord guide: {e}")

        await session.delete(ie)
        await session.commit()
        return {"success": True, "message": "Guide supprimé avec succès."}

@app.delete("/api/info-embeds/{key:path}", dependencies=[Depends(get_api_key)])
@app.delete("/info-embeds/{key:path}", dependencies=[Depends(get_api_key)])
async def delete_info_embed(key: str):
    return await _handle_delete_info_embed(key)


# --- ANNOUNCEMENTS (MODULE D'ANNONCES & DIFFUSION) ---

async def _handle_get_announcements():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Announcement).order_by(Announcement.created_at.desc()))
        announcements = res.scalars().all()
        
        # Vérification en arrière-plan de l'existence des messages sur Discord
        if bot.is_ready():
            db_changed = False
            for ann in announcements:
                if ann.channel_id and ann.message_id and ann.status != "deleted":
                    try:
                        ch_id = int(str(ann.channel_id).strip())
                        msg_id = int(str(ann.message_id).strip())
                        ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                        if ch:
                            await ch.fetch_message(msg_id)
                    except (discord.NotFound, discord.Forbidden):
                        ann.status = "deleted"
                        ann.message_id = None
                        db_changed = True
                    except Exception:
                        pass
            if db_changed:
                await session.commit()
                
        return [announcement_to_dict(a) for a in announcements]

@app.get("/api/announcements", dependencies=[Depends(get_api_key)])
@app.get("/announcements", dependencies=[Depends(get_api_key)])
async def get_announcements():
    return await _handle_get_announcements()


async def _handle_get_announcement(ann_id: int):
    async with AsyncSessionLocal() as session:
        ann = await session.get(Announcement, ann_id)
        if not ann:
            raise HTTPException(404, detail="Annonce introuvable.")
        
        if bot.is_ready() and ann.channel_id and ann.message_id and ann.status != "deleted":
            try:
                ch_id = int(str(ann.channel_id).strip())
                msg_id = int(str(ann.message_id).strip())
                ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                if ch:
                    await ch.fetch_message(msg_id)
            except (discord.NotFound, discord.Forbidden):
                ann.status = "deleted"
                ann.message_id = None
                await session.commit()
            except Exception:
                pass
        return announcement_to_dict(ann)

@app.get("/api/announcements/{ann_id}", dependencies=[Depends(get_api_key)])
@app.get("/announcements/{ann_id}", dependencies=[Depends(get_api_key)])
async def get_announcement(ann_id: int):
    return await _handle_get_announcement(ann_id)


async def _handle_create_announcement(data: AnnouncementCreate):
    if not bot.is_ready():
        raise HTTPException(503, detail="Le bot Discord n'est pas encore prêt. Veuillez patienter.")
    
    try:
        ch_id = int(str(data.channel_id).strip())
        channel = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
        if not channel:
            raise HTTPException(404, detail=f"Salon Discord ID {ch_id} introuvable.")
    except Exception as e:
        raise HTTPException(400, detail=f"Salon Discord invalide: {e}")

    embed = build_announcement_embed(data)
    content_str = str(data.content).strip() if data.content and str(data.content).strip() else None

    if not content_str and not embed:
        raise HTTPException(400, detail="Veuillez renseigner un message texte ou au moins un élément d'embed (titre, description...).")

    try:
        send_kwargs = {}
        if content_str:
            send_kwargs["content"] = content_str
        if embed:
            send_kwargs["embed"] = embed
        
        msg = await channel.send(**send_kwargs)
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'annonce sur Discord: {e}")
        raise HTTPException(500, detail=f"Erreur lors de l'envoi sur Discord: {e}")

    async with AsyncSessionLocal() as session:
        ann = Announcement(
            title=str(data.title).strip() if data.title else "Nouvelle Annonce",
            channel_id=ch_id,
            message_id=msg.id,
            content=content_str,
            embed_title=data.embed_title,
            embed_title_url=data.embed_title_url,
            embed_description=data.embed_description,
            embed_color=data.embed_color or "#5865F2",
            embed_author_name=data.embed_author_name,
            embed_author_icon_url=data.embed_author_icon_url,
            embed_author_url=data.embed_author_url,
            embed_thumbnail_url=data.embed_thumbnail_url,
            embed_image_url=data.embed_image_url,
            embed_footer_text=data.embed_footer_text,
            embed_footer_icon_url=data.embed_footer_icon_url,
            embed_show_timestamp=bool(data.embed_show_timestamp) if data.embed_show_timestamp is not None else True,
            status="sent",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        session.add(ann)
        await session.commit()
        await session.refresh(ann)
        return announcement_to_dict(ann)

@app.post("/api/announcements", dependencies=[Depends(get_api_key)])
@app.post("/announcements", dependencies=[Depends(get_api_key)])
async def create_announcement(data: AnnouncementCreate):
    return await _handle_create_announcement(data)


async def _handle_update_announcement(ann_id: int, data: AnnouncementUpdate):
    if not bot.is_ready():
        raise HTTPException(503, detail="Le bot Discord n'est pas encore prêt. Veuillez patienter.")

    async with AsyncSessionLocal() as session:
        ann = await session.get(Announcement, ann_id)
        if not ann:
            raise HTTPException(404, detail="Annonce introuvable.")

        # Mise à jour des attributs si fournis
        if data.title is not None: ann.title = str(data.title).strip()
        if data.content is not None: ann.content = str(data.content).strip() if str(data.content).strip() else None
        if data.embed_title is not None: ann.embed_title = data.embed_title
        if data.embed_title_url is not None: ann.embed_title_url = data.embed_title_url
        if data.embed_description is not None: ann.embed_description = data.embed_description
        if data.embed_color is not None: ann.embed_color = data.embed_color
        if data.embed_author_name is not None: ann.embed_author_name = data.embed_author_name
        if data.embed_author_icon_url is not None: ann.embed_author_icon_url = data.embed_author_icon_url
        if data.embed_author_url is not None: ann.embed_author_url = data.embed_author_url
        if data.embed_thumbnail_url is not None: ann.embed_thumbnail_url = data.embed_thumbnail_url
        if data.embed_image_url is not None: ann.embed_image_url = data.embed_image_url
        if data.embed_footer_text is not None: ann.embed_footer_text = data.embed_footer_text
        if data.embed_footer_icon_url is not None: ann.embed_footer_icon_url = data.embed_footer_icon_url
        if data.embed_show_timestamp is not None: ann.embed_show_timestamp = bool(data.embed_show_timestamp)

        old_channel_id = ann.channel_id
        new_channel_id = int(str(data.channel_id).strip()) if data.channel_id and str(data.channel_id).strip() else old_channel_id

        embed = build_announcement_embed(ann)
        content_str = ann.content if ann.content and str(ann.content).strip() else None

        # Cas 1 : Le salon a changé -> Supprime l'ancien message et reposte dans le nouveau salon
        if new_channel_id != old_channel_id:
            # Supprime l'ancien
            if old_channel_id and ann.message_id:
                try:
                    old_ch = bot.get_channel(old_channel_id) or await bot.fetch_channel(old_channel_id)
                    if old_ch:
                        old_msg = await old_ch.fetch_message(ann.message_id)
                        if old_msg:
                            await old_msg.delete()
                except Exception as e:
                    logger.warning(f"Impossible de supprimer l'ancien message d'annonce: {e}")

            # Envoie dans le nouveau salon
            try:
                new_ch = bot.get_channel(new_channel_id) or await bot.fetch_channel(new_channel_id)
                send_kwargs = {}
                if content_str: send_kwargs["content"] = content_str
                if embed: send_kwargs["embed"] = embed
                new_msg = await new_ch.send(**send_kwargs)
                ann.channel_id = new_channel_id
                ann.message_id = new_msg.id
                ann.status = "sent"
            except Exception as e:
                logger.error(f"Erreur lors de la diffusion de l'annonce dans le nouveau salon: {e}")
                raise HTTPException(500, detail=f"Erreur lors de la publication dans le nouveau salon: {e}")

        # Cas 2 : Le salon reste le même -> Modifie le message en place
        else:
            edited = False
            if ann.channel_id and ann.message_id:
                try:
                    ch = bot.get_channel(ann.channel_id) or await bot.fetch_channel(ann.channel_id)
                    if ch:
                        msg = await ch.fetch_message(ann.message_id)
                        if msg:
                            edit_kwargs = {
                                "content": content_str,
                                "embed": embed
                            }
                            await msg.edit(**edit_kwargs)
                            edited = True
                            ann.status = "edited"
                except Exception as e:
                    logger.warning(f"Impossible d'éditer le message {ann.message_id} sur Discord: {e}")

            if not edited:
                # Si le message d'origine n'existait plus, on le reposte
                try:
                    ch = bot.get_channel(ann.channel_id) or await bot.fetch_channel(ann.channel_id)
                    send_kwargs = {}
                    if content_str: send_kwargs["content"] = content_str
                    if embed: send_kwargs["embed"] = embed
                    new_msg = await ch.send(**send_kwargs)
                    ann.message_id = new_msg.id
                    ann.status = "sent"
                except Exception as e:
                    raise HTTPException(500, detail=f"Erreur ré-envoi annonce: {e}")

        ann.updated_at = datetime.datetime.utcnow()
        await session.commit()
        await session.refresh(ann)
        return announcement_to_dict(ann)

@app.put("/api/announcements/{ann_id}", dependencies=[Depends(get_api_key)])
@app.put("/announcements/{ann_id}", dependencies=[Depends(get_api_key)])
async def update_announcement(ann_id: int, data: AnnouncementUpdate):
    return await _handle_update_announcement(ann_id, data)


async def _handle_delete_announcement(ann_id: int):
    async with AsyncSessionLocal() as session:
        ann = await session.get(Announcement, ann_id)
        if not ann:
            raise HTTPException(404, detail="Annonce introuvable.")

        # Suppression du message sur Discord
        if bot.is_ready() and ann.channel_id and ann.message_id:
            try:
                ch = bot.get_channel(ann.channel_id) or await bot.fetch_channel(ann.channel_id)
                if ch:
                    msg = await ch.fetch_message(ann.message_id)
                    if msg:
                        await msg.delete()
                        logger.info(f"Message d'annonce {ann.message_id} supprimé sur Discord.")
            except Exception as e:
                logger.warning(f"Impossible de supprimer le message Discord {ann.message_id}: {e}")

        await session.delete(ann)
        await session.commit()
        return {"success": True, "message": "Annonce supprimée avec succès."}

@app.delete("/api/announcements/{ann_id}", dependencies=[Depends(get_api_key)])
@app.delete("/announcements/{ann_id}", dependencies=[Depends(get_api_key)])
async def delete_announcement(ann_id: int):
    return await _handle_delete_announcement(ann_id)


@app.get("/api/panels", dependencies=[Depends(get_api_key)])
async def get_panels():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Panel).order_by(Panel.id.asc()))
        panels = res.scalars().all()
        db_changed = False
        if bot.is_ready():
            for p in panels:
                if p.deployed_channel_id and p.deployed_message_id:
                    try:
                        ch_id = int(str(p.deployed_channel_id).strip())
                        msg_id = int(str(p.deployed_message_id).strip())
                        ch = bot.get_channel(ch_id)
                        if not ch:
                            ch = await bot.fetch_channel(ch_id)
                        await ch.fetch_message(msg_id)
                    except (discord.NotFound, discord.Forbidden):
                        logger.info(f"Le message déployé {p.deployed_message_id} du panel '{p.name}' n'existe plus sur Discord. Réinitialisation du statut.")
                        p.deployed_channel_id = None
                        p.deployed_message_id = None
                        db_changed = True
                    except Exception as e:
                        logger.debug(f"Vérification message panel {p.id} ignorée: {e}")
            if db_changed:
                await session.commit()
        return [panel_to_dict(p) for p in panels]

@app.post("/api/panels", dependencies=[Depends(get_api_key)])
async def create_panel(panel_data: PanelCreate):
    async with AsyncSessionLocal() as session:
        panel = Panel(
            name=panel_data.name,
            embed_title=panel_data.embed_title,
            embed_title_url=panel_data.embed_title_url,
            embed_description=panel_data.embed_description,
            embed_color=panel_data.embed_color,
            embed_author_name=panel_data.embed_author_name,
            embed_author_icon_url=panel_data.embed_author_icon_url,
            embed_author_url=panel_data.embed_author_url,
            embed_thumbnail_url=panel_data.embed_thumbnail_url,
            embed_image_url=panel_data.embed_image_url,
            embed_footer_text=panel_data.embed_footer_text or "Système de Ticketing",
            embed_footer_icon_url=panel_data.embed_footer_icon_url,
            embed_show_timestamp=panel_data.embed_show_timestamp if panel_data.embed_show_timestamp is not None else False,
            button_text=panel_data.button_text,
            button_color=panel_data.button_color,
            button_emoji=panel_data.button_emoji,
            intro_embed_title=panel_data.intro_embed_title or "Bienvenue dans votre ticket",
            intro_message=panel_data.intro_message,
            intro_embed_color=panel_data.intro_embed_color,
            intro_thumbnail_url=panel_data.intro_thumbnail_url,
            intro_image_url=panel_data.intro_image_url,
            intro_footer_text=panel_data.intro_footer_text,
            inactivity_days=panel_data.inactivity_days,
            ping_creator=panel_data.ping_creator,
            max_tickets_per_user=panel_data.max_tickets_per_user,
            close_button_text=panel_data.close_button_text,
            close_button_color=panel_data.close_button_color,
            close_button_emoji=panel_data.close_button_emoji,
            close_reason_button_text=panel_data.close_reason_button_text,
            close_reason_button_color=panel_data.close_reason_button_color,
            close_reason_button_emoji=panel_data.close_reason_button_emoji,
            reopen_button_text=panel_data.reopen_button_text,
            reopen_button_color=panel_data.reopen_button_color,
            reopen_button_emoji=panel_data.reopen_button_emoji,
            delete_button_text=panel_data.delete_button_text,
            delete_button_color=panel_data.delete_button_color,
            delete_button_emoji=panel_data.delete_button_emoji,
            creator_can_close=panel_data.creator_can_close if panel_data.creator_can_close is not None else True,
            creator_can_close_with_reason=panel_data.creator_can_close_with_reason if panel_data.creator_can_close_with_reason is not None else False,
            dm_transcript_on_close=panel_data.dm_transcript_on_close if panel_data.dm_transcript_on_close is not None else True,
            dm_embed_title=panel_data.dm_embed_title or "🔒 Ticket fermé — {ticket_name}",
            dm_embed_title_url=panel_data.dm_embed_title_url,
            dm_embed_description=panel_data.dm_embed_description or "Votre ticket **{ticket_name}** a été clôturé par **{closed_by}**.\nVous trouverez ci-joint l'archive complète de votre conversation.",
            dm_embed_color=panel_data.dm_embed_color,
            dm_thumbnail_url=panel_data.dm_thumbnail_url,
            dm_image_url=panel_data.dm_image_url,
            dm_footer_text=panel_data.dm_footer_text or "Téléchargez et ouvrez le fichier .html pour relire la conversation.",
            dm_footer_icon_url=panel_data.dm_footer_icon_url,
            dm_show_timestamp=panel_data.dm_show_timestamp if panel_data.dm_show_timestamp is not None else True,
            modal_title=panel_data.modal_title,
        )
        if panel_data.active_category_id and str(panel_data.active_category_id).strip():
            try:
                panel.active_category_id = int(str(panel_data.active_category_id).strip())
            except ValueError:
                panel.active_category_id = None
        if panel_data.archive_category_id and str(panel_data.archive_category_id).strip():
            try:
                panel.archive_category_id = int(str(panel_data.archive_category_id).strip())
            except ValueError:
                panel.archive_category_id = None
        if panel_data.ping_role_id and str(panel_data.ping_role_id).strip():
            try:
                panel.ping_role_id = int(str(panel_data.ping_role_id).strip())
            except ValueError:
                panel.ping_role_id = None

        session.add(panel)
        await session.commit()
        return panel_to_dict(panel)

@app.put("/api/panels/{panel_id}", dependencies=[Depends(get_api_key)])
async def update_panel(panel_id: int, panel_data: PanelCreate):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if not panel:
            raise HTTPException(status_code=404, detail="Panel introuvable.")
        
        data = panel_data.dict()
        for key, value in data.items():
            if key in ["active_category_id", "archive_category_id", "ping_role_id"]:
                if value and str(value).strip():
                    try:
                        setattr(panel, key, int(str(value).strip()))
                    except (ValueError, TypeError):
                        setattr(panel, key, None)
                else:
                    setattr(panel, key, None)
            else:
                setattr(panel, key, value)
        
        await session.commit()

        # 🚀 REDÉPLOIEMENT AUTOMATIQUE si le panel est déjà déployé sur un salon
        if panel.deployed_channel_id and panel.deployed_message_id:
            try:
                ch_id = int(str(panel.deployed_channel_id).strip())
                msg_id = int(str(panel.deployed_message_id).strip())
                ch = bot.get_channel(ch_id)
                if not ch:
                    ch = await bot.fetch_channel(ch_id)
                if ch:
                    msg = await ch.fetch_message(msg_id)
                    if msg:
                        embed = build_panel_embed(panel)
                        await msg.edit(embed=embed, view=PanelView(panel))
                        logger.info(f"Panel {panel.id} auto-redéployé sur message {msg_id}")
            except (discord.NotFound, discord.Forbidden):
                logger.info(f"Le message {panel.deployed_message_id} n'existe plus sur Discord. Réinitialisation du statut.")
                panel.deployed_channel_id = None
                panel.deployed_message_id = None
                await session.commit()
            except Exception as e:
                logger.warning(f"Impossible d'auto-redéployer le panel {panel.id}: {e}")

        return panel_to_dict(panel)

@app.delete("/api/panels/{panel_id}", dependencies=[Depends(get_api_key)])
async def delete_panel(panel_id: int):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if panel:
            # Supprime aussi le message déployé sur Discord pour ne pas laisser de bouton orphelin
            if panel.deployed_channel_id and panel.deployed_message_id:
                try:
                    ch_id = int(str(panel.deployed_channel_id).strip())
                    msg_id = int(str(panel.deployed_message_id).strip())
                    ch = bot.get_channel(ch_id)
                    if not ch:
                        ch = await bot.fetch_channel(ch_id)
                    if ch:
                        msg = await ch.fetch_message(msg_id)
                        if msg:
                            await msg.delete()
                            logger.info(f"Message Discord {msg_id} du panel {panel_id} supprimé avec succès.")
                except (discord.NotFound, discord.Forbidden):
                    logger.info(f"Le message du panel {panel_id} n'existait déjà plus sur Discord.")
                except Exception as e:
                    logger.warning(f"Impossible de supprimer le message déployé du panel {panel_id}: {e}")
            await session.execute(delete(FormField).where(FormField.panel_id == panel_id))
            await session.delete(panel)
            await session.commit()
        return {"success": True}

@app.post("/api/panels/{panel_id}/undeploy", dependencies=[Depends(get_api_key)])
async def undeploy_panel(panel_id: int):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if not panel:
            raise HTTPException(status_code=404, detail="Panel introuvable.")
        if panel.deployed_channel_id and panel.deployed_message_id:
            try:
                ch_id = int(str(panel.deployed_channel_id).strip())
                msg_id = int(str(panel.deployed_message_id).strip())
                ch = bot.get_channel(ch_id)
                if not ch:
                    ch = await bot.fetch_channel(ch_id)
                if ch:
                    msg = await ch.fetch_message(msg_id)
                    if msg:
                        await msg.delete()
                        logger.info(f"Message Discord {msg_id} du panel {panel_id} retiré.")
            except (discord.NotFound, discord.Forbidden):
                pass
            except Exception as e:
                logger.warning(f"Erreur lors du retrait du message Discord: {e}")
            panel.deployed_channel_id = None
            panel.deployed_message_id = None
            await session.commit()
        return {"success": True, "message": "Panel retiré de Discord."}

@app.post("/api/panels/{panel_id}/send", dependencies=[Depends(get_api_key)])
async def send_panel_to_discord(panel_id: int, channel_id: int):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if not panel:
            raise HTTPException(status_code=404, detail="Panel introuvable.")
        
        target_ch_id = int(str(channel_id).strip())
        channel = bot.get_channel(target_ch_id)
        if not channel:
            channel = await bot.fetch_channel(target_ch_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Salon Discord introuvable.")
        
        # Nettoyage de l'ancien message déployé pour éviter les doublons sur le serveur
        if panel.deployed_channel_id and panel.deployed_message_id:
            try:
                old_ch_id = int(str(panel.deployed_channel_id).strip())
                old_msg_id = int(str(panel.deployed_message_id).strip())
                old_ch = bot.get_channel(old_ch_id)
                if not old_ch:
                    old_ch = await bot.fetch_channel(old_ch_id)
                if old_ch:
                    old_msg = await old_ch.fetch_message(old_msg_id)
                    if old_msg:
                        await old_msg.delete()
                        logger.info(f"Ancien message {old_msg_id} du panel {panel_id} nettoyé avant redéploiement.")
            except (discord.NotFound, discord.Forbidden):
                pass
            except Exception as e:
                logger.debug(f"Nettoyage ancien message panel {panel_id}: {e}")
        
        embed = build_panel_embed(panel)
        sent_msg = await channel.send(embed=embed, view=PanelView(panel))
        
        # Enregistrer l'emplacement du déploiement pour permettre les mises à jour automatiques
        panel.deployed_channel_id = channel.id
        panel.deployed_message_id = sent_msg.id
        await session.commit()
        
        return {"success": True, "message": "Bouton envoyé dans le salon !"}

def _normalize_field_payload(field_data: FieldCreate) -> dict:
    data = field_data.dict()
    ftype = data.get("field_type") or ("paragraph" if data.get("is_paragraph") else "short_text")
    if ftype not in ("short_text", "paragraph", "select", "user_select", "role_select", "channel_select", "file_upload"):
        ftype = "short_text"
    data["field_type"] = ftype
    data["is_paragraph"] = (ftype == "paragraph")

    is_req = bool(data.get("required")) if data.get("required") is not None else True
    data["required"] = is_req

    # Nettoyage des options pour select
    if ftype == "select":
        raw_opts = data.get("options") or []
        cleaned_opts = []
        for opt in raw_opts[:25]:
            if isinstance(opt, dict):
                lbl = str(opt.get("label") or "").strip()[:100]
                if not lbl:
                    continue
                val = str(opt.get("value") or lbl).strip()[:100]
                desc = str(opt.get("description") or "").strip()[:100]
                emoji = str(opt.get("emoji") or "").strip()
                cleaned_opts.append({
                    "label": lbl,
                    "value": val,
                    "description": desc,
                    "emoji": emoji,
                    "default": bool(opt.get("default", False))
                })
        data["options"] = cleaned_opts
    elif ftype == "file_upload":
        data["options"] = None
        max_v = data.get("max_values")
        max_v = max(1, min(int(max_v if max_v is not None else 5), 10))
        min_v = data.get("min_values")
        if not is_req:
            min_v = 0
        else:
            min_v = max(1, min(int(min_v if min_v is not None else 1), max_v))
        data["min_values"] = min_v
        data["max_values"] = max_v
    else:
        data["options"] = None

    return data

@app.get("/api/panels/{panel_id}/fields", dependencies=[Depends(get_api_key)])
async def get_fields(panel_id: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(FormField)
            .where(FormField.panel_id == panel_id)
            .order_by(FormField.order.asc(), FormField.id.asc())
        )
        return [field_to_dict(f) for f in res.scalars().all()]

@app.post("/api/panels/{panel_id}/fields", dependencies=[Depends(get_api_key)])
async def create_field(panel_id: int, field_data: FieldCreate):
    async with AsyncSessionLocal() as session:
        count_res = await session.execute(
            select(func.count(FormField.id)).where(FormField.panel_id == panel_id)
        )
        if (count_res.scalar() or 0) >= 5:
            raise HTTPException(status_code=400, detail="Discord limite les formulaires à 5 questions maximum.")
        payload = _normalize_field_payload(field_data)
        if payload["field_type"] == "select" and not payload.get("options"):
            raise HTTPException(status_code=400, detail="Une liste déroulante doit comporter au moins 1 choix.")
        field = FormField(panel_id=panel_id, **payload)
        session.add(field)
        await session.commit()
        return field_to_dict(field)

@app.put("/api/fields/{field_id}", dependencies=[Depends(get_api_key)])
async def update_field(field_id: int, field_data: FieldCreate):
    async with AsyncSessionLocal() as session:
        field = await session.get(FormField, field_id)
        if field:
            payload = _normalize_field_payload(field_data)
            if payload["field_type"] == "select" and not payload.get("options"):
                raise HTTPException(status_code=400, detail="Une liste déroulante doit comporter au moins 1 choix.")
            for k, v in payload.items():
                setattr(field, k, v)
            await session.commit()
            return field_to_dict(field)
        raise HTTPException(404, detail="Question introuvable.")

@app.put("/api/panels/{panel_id}/fields/reorder", dependencies=[Depends(get_api_key)])
async def reorder_fields(panel_id: int, request: Request):
    body = await request.json()
    field_ids = body.get("field_ids") or []
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(FormField).where(FormField.panel_id == panel_id))
        fields_map = {f.id: f for f in res.scalars().all()}
        for idx, fid in enumerate(field_ids):
            if int(fid) in fields_map:
                fields_map[int(fid)].order = idx
        await session.commit()
        ordered = sorted(fields_map.values(), key=lambda x: (x.order or 0, x.id))
        return [field_to_dict(f) for f in ordered]

@app.delete("/api/fields/{field_id}", dependencies=[Depends(get_api_key)])
async def delete_field(field_id: int):
    async with AsyncSessionLocal() as session:
        field = await session.get(FormField, field_id)
        if field:
            await session.delete(field)
            await session.commit()
        return {"success": True}

@app.get("/api/stats", dependencies=[Depends(get_api_key)])
async def get_stats():
    async with AsyncSessionLocal() as session:
        total = await session.execute(select(func.count(Ticket.id)))
        open_t = await session.execute(select(func.count(Ticket.id)).where(Ticket.status == "open"))
        closed_t = await session.execute(select(func.count(Ticket.id)).where(Ticket.status == "closed"))
        return {
            "total_tickets": total.scalar() or 0,
            "open_tickets": open_t.scalar() or 0,
            "closed_tickets": closed_t.scalar() or 0
        }

@app.get("/api/tickets", dependencies=[Depends(get_api_key)])
async def get_tickets(limit: Optional[int] = Query(None), offset: int = 0):
    async with AsyncSessionLocal() as session:
        query = select(Ticket).order_by(Ticket.created_at.desc())
        if limit is not None:
            query = query.limit(limit).offset(offset)
        res = await session.execute(query)
        tickets = res.scalars().all()
        result = []
        updated = False
        for t in tickets:
            td = ticket_to_dict(t)
            if not td.get("creator_username") and t.creator_id:
                user = bot.get_user(t.creator_id)
                if not user:
                    try:
                        user = await bot.fetch_user(t.creator_id)
                    except Exception:
                        user = None
                if user:
                    u_name = user.display_name if user.display_name else user.name
                    td["creator_username"] = u_name
                    t.creator_username = u_name
                    updated = True
            result.append(td)
        if updated:
            try:
                await session.commit()
            except Exception:
                pass
        return result

def extract_web_actor(request: Request, body: Optional[dict] = None) -> tuple:
    """Extrait le nom et l'email de l'utilisateur Web ayant déclenché l'action."""
    actor_name = ""
    actor_email = ""
    if isinstance(body, dict):
        actor_name = str(body.get("actor_name") or "").strip()
        actor_email = str(body.get("actor_email") or "").strip()
    if not actor_name:
        actor_name = (request.headers.get("X-Actor-Name") or "").strip()
    if not actor_email:
        actor_email = (request.headers.get("X-Actor-Email") or "").strip()

    if actor_name and actor_email and actor_name != actor_email:
        display = f"👤 **{actor_name}** (`{actor_email}`)"
        short = f"{actor_name} ({actor_email})"
    elif actor_name:
        display = f"👤 **{actor_name}**"
        short = actor_name
    elif actor_email:
        display = f"👤 `{actor_email}`"
        short = actor_email
    else:
        display = "👤 **Staff (Dashboard Web)**"
        short = "Dashboard Web"
    return display, short


@app.get("/api/settings/logs", dependencies=[Depends(get_api_key)])
async def get_logs_settings():
    """Récupère l'état d'activation et le salon configuré pour les logs des tickets."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(BotSettings).where(BotSettings.key == "global"))
        settings = res.scalar_one_or_none()
        if not settings:
            return {"logs_enabled": False, "logs_channel_id": ""}
        return {
            "logs_enabled": bool(settings.logs_enabled),
            "logs_channel_id": str(settings.logs_channel_id) if settings.logs_channel_id else "",
        }


@app.put("/api/settings/logs", dependencies=[Depends(get_api_key)])
async def update_logs_settings(request: Request):
    """Met à jour l'activation des logs et le salon Discord de destination."""
    body = await request.json()
    logs_enabled = bool(body.get("logs_enabled", False))
    raw_channel = body.get("logs_channel_id")
    logs_channel_id = None
    if raw_channel and str(raw_channel).strip():
        try:
            logs_channel_id = int(str(raw_channel).strip())
        except ValueError:
            raise HTTPException(400, detail="ID de salon invalide.")

    if logs_enabled and not logs_channel_id:
        raise HTTPException(400, detail="Veuillez sélectionner un salon Discord pour activer les logs.")

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(BotSettings).where(BotSettings.key == "global"))
        settings = res.scalar_one_or_none()
        if not settings:
            settings = BotSettings(
                key="global",
                logs_enabled=logs_enabled,
                logs_channel_id=logs_channel_id,
            )
            session.add(settings)
        else:
            settings.logs_enabled = logs_enabled
            settings.logs_channel_id = logs_channel_id
            settings.updated_at = datetime.datetime.utcnow()
        await session.commit()
        return {
            "logs_enabled": bool(settings.logs_enabled),
            "logs_channel_id": str(settings.logs_channel_id) if settings.logs_channel_id else "",
        }


@app.post("/api/tickets/{ticket_id}/close", dependencies=[Depends(get_api_key)])
async def close_ticket(ticket_id: int, request: Request):
    close_reason = None
    body = {}
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("reason"):
            close_reason = str(body.get("reason")).strip()
    except Exception:
        pass

    actor_display, actor_short = extract_web_actor(request, body)

    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(404, detail="Ticket introuvable.")
        
        panel = await session.get(Panel, ticket.panel_id)
        
        channel = None
        if ticket.channel_id:
            try:
                channel = bot.get_channel(ticket.channel_id) or await bot.fetch_channel(ticket.channel_id)
            except Exception:
                pass

        # Fallback : si non trouvé par ID, chercher le salon par son nom (ex: alan-001 ou ticket006)
        if not channel:
            candidates = []
            if getattr(ticket, "ticket_name", None):
                candidates.append(ticket.ticket_name)
            candidates.append(f"ticket{ticket.id:03d}")
            for guild in bot.guilds:
                for target_name in candidates:
                    ch = discord.utils.get(guild.text_channels, name=target_name)
                    if ch:
                        channel = ch
                        ticket.channel_id = ch.id
                        logger.info(f"Salon retrouvé par nom '{target_name}' : ID {ch.id}")
                        break
                if channel:
                    break

        transcript = ""
        if channel:
            try:
                import chat_exporter
                transcript = await chat_exporter.export(
                    channel,
                    limit=None,
                    tz_info="Europe/Paris",
                    military_time=True,
                    bot=bot
                )
            except Exception as e:
                logger.warning(f"Erreur export transcript lors de la fermeture API: {e}")

        ticket.status = "closed"
        ticket.updated_at = datetime.datetime.utcnow()
        ticket.closed_by_username = f"{actor_short} (Web)"
        if close_reason:
            ticket.close_reason = close_reason
        
        if transcript:
            res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
            archive = res_arc.scalar_one_or_none()
            if archive:
                archive.html_content = transcript # Écrase la retranscription précédente
            else:
                session.add(TicketArchive(ticket_id=ticket.id, html_content=transcript))

        await session.commit()

        if channel:
            # 1. Retirer TOUS les boutons 'Fermer' des messages précédents du salon
            try:
                from bot import remove_close_buttons_from_channel
                await remove_close_buttons_from_channel(channel)
            except Exception as e:
                logger.warning(f"Erreur lors du nettoyage des boutons 'Fermer' : {e}")

            # 2. Retirer l'accès au membre créateur
            try:
                member = channel.guild.get_member(ticket.creator_id)
                if not member:
                    member = await channel.guild.fetch_member(ticket.creator_id)
                if member:
                    await channel.set_permissions(member, overwrite=None)
            except Exception:
                pass

            # 3. Déplacer vers la catégorie archive si configurée (avec gestion du débordement archive (2), etc.)
            if panel and panel.archive_category_id:
                try:
                    from bot import move_channel_with_overflow
                    await move_channel_with_overflow(channel, panel.archive_category_id, bot)
                except Exception as e:
                    logger.warning(f"Erreur déplacement archive: {e}")

            # 3b. Rééquilibrer et nettoyer la catégorie active (supprime ticket (2) si on repasse <= 50 salons)
            if panel and panel.active_category_id:
                try:
                    from bot import cleanup_and_rebalance_category_family
                    await cleanup_and_rebalance_category_family(channel.guild, panel.active_category_id, bot)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage catégorie active: {e}")

            # 4. Envoyer le message d'archive avec boutons Ré-ouvrir et Supprimer
            try:
                import io
                from bot import ArchivedTicketView, send_dm_transcript_to_creator
                desc_lines = [
                    "Le ticket a été fermé depuis le **Dashboard Web**.",
                    "La conversation a été archivée."
                ]
                if close_reason:
                    desc_lines.append(f"\n📝 **Raison de la fermeture :**\n> {close_reason}")
                embed = discord.Embed(
                    title="🔒 Ticket Fermé",
                    description="\n".join(desc_lines),
                    color=discord.Color.red()
                )
                if transcript:
                    file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{ticket.id}.html")
                    await channel.send(embed=embed, file=file, view=ArchivedTicketView(panel))
                else:
                    await channel.send(embed=embed, view=ArchivedTicketView(panel))

                # 5. Envoyer le résumé + transcript en MP au créateur si activé sur le panel
                if panel is None or getattr(panel, "dm_transcript_on_close", True):
                    await send_dm_transcript_to_creator(
                        bot_client=bot,
                        ticket=ticket,
                        panel=panel,
                        closed_by_display=f"{actor_short} (Interface Web)",
                        close_reason=close_reason,
                        transcript_html=transcript
                    )
            except Exception as e:
                logger.warning(f"Erreur envoi message archive: {e}")

        # 6. Envoi du log de fermeture dans le salon de logs si activé
        try:
            await send_ticket_log(
                bot_client=bot,
                action="close",
                ticket=ticket,
                panel=panel,
                source="web",
                actor_display=actor_display,
                close_reason=close_reason,
                channel_id=channel.id if channel else None,
                transcript_html=transcript,
            )
        except Exception as log_err:
            logger.warning(f"Erreur envoi log fermeture web: {log_err}")

        return ticket_to_dict(ticket)


@app.post("/api/tickets/{ticket_id}/reopen", dependencies=[Depends(get_api_key)])
async def reopen_ticket(ticket_id: int, request: Request):
    """
    Ré-ouvre un ticket depuis l'interface Web.
    - Si le salon Discord existe encore (en archive), restaure les permissions du créateur et le déplace vers la catégorie active (ou ticket (2)).
    - Si le salon Discord a été supprimé, le recrée au bon endroit (catégorie active ou ticket (2)) avec exactement tous les bons droits,
      reposte l'embed récapitulatif du ticket avec les boutons Fermer / Fermer avec raison, et joint l'archive précédente si disponible.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    actor_display, actor_short = extract_web_actor(request, body)
    import io
    from bot import (
        TicketView,
        remove_archive_buttons_from_channel,
        move_channel_with_overflow,
        cleanup_and_rebalance_category_family,
        get_base_category,
        build_ticket_channel_overwrites,
        create_ticket_channel_with_overflow,
        build_ticket_welcome_embed_and_pings,
        resolve_guild_for_panel,
    )

    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(404, detail="Ticket introuvable.")

        panel = await session.get(Panel, ticket.panel_id) if ticket.panel_id else None

        # 1. Vérifier si le salon Discord existe encore
        channel = None
        if ticket.channel_id:
            try:
                channel = bot.get_channel(int(ticket.channel_id)) or await bot.fetch_channel(int(ticket.channel_id))
            except Exception:
                channel = None

        # Fallback recherche par nom si l'ID n'est plus valide mais que le salon existe
        if not channel:
            candidates = []
            if getattr(ticket, "ticket_name", None):
                candidates.append(ticket.ticket_name)
            candidates.append(f"ticket{ticket.id:03d}")
            for g in bot.guilds:
                for target_name in candidates:
                    ch = discord.utils.get(g.text_channels, name=target_name)
                    if ch:
                        channel = ch
                        ticket.channel_id = ch.id
                        break
                if channel:
                    break

        recreated = False

        if channel:
            # --- CAS A : Le salon existe encore sur Discord ---
            guild = channel.guild
            # Restaurer les permissions du membre créateur
            if ticket.creator_id:
                try:
                    member = guild.get_member(int(ticket.creator_id))
                    if not member:
                        member = await guild.fetch_member(int(ticket.creator_id))
                    if member:
                        await channel.set_permissions(
                            member,
                            read_messages=True,
                            send_messages=True,
                            attach_files=True,
                            embed_links=True,
                        )
                except Exception as perm_err:
                    logger.warning(f"Impossible de restaurer les permissions du créateur {ticket.creator_id}: {perm_err}")

            # Déplacer vers la catégorie active (avec débordement ticket (2) si pleine)
            if panel and panel.active_category_id:
                try:
                    await move_channel_with_overflow(channel, panel.active_category_id, bot)
                except Exception as move_err:
                    logger.warning(f"Erreur déplacement vers catégorie active lors de la réouverture: {move_err}")

            # Rééquilibrer et nettoyer la catégorie d'archive (supprime archive (2) si on repasse <= 50 salons)
            if panel and panel.archive_category_id:
                try:
                    await cleanup_and_rebalance_category_family(guild, panel.archive_category_id, bot)
                except Exception as clean_err:
                    logger.warning(f"Erreur nettoyage catégorie archive lors de la réouverture: {clean_err}")

            # Retirer les boutons d'archive (Ré-ouvrir / Supprimer)
            try:
                await remove_archive_buttons_from_channel(channel)
            except Exception:
                pass

            # Envoyer le message de réouverture avec les boutons Fermer / Fermer avec raison
            creator_mention = f" (<@{ticket.creator_id}>)" if ticket.creator_id else ""
            await channel.send(
                f"🔓 Ticket ré-ouvert depuis le **Dashboard Web**{creator_mention}.",
                view=TicketView(panel),
            )
        else:
            # --- CAS B : Le salon avait été supprimé de Discord -> On le recrée au bon endroit avec tous ses droits ! ---
            guild = resolve_guild_for_panel(bot, panel)
            if not guild:
                raise HTTPException(500, detail="Aucun serveur Discord disponible pour recréer le salon.")

            base_cat = await get_base_category(guild, panel.active_category_id if panel else None, bot)
            overwrites, _ = await build_ticket_channel_overwrites(guild, base_cat, ticket.creator_id, panel)

            ticket_name = getattr(ticket, "ticket_name", None) or f"ticket{ticket.id:03d}"

            try:
                channel = await create_ticket_channel_with_overflow(
                    guild=guild,
                    channel_name=ticket_name,
                    base_category_id=panel.active_category_id if panel else None,
                    overwrites=overwrites,
                    bot_client=bot,
                )
                recreated = True
                ticket.channel_id = channel.id
            except Exception as create_err:
                logger.error(f"Erreur lors de la recréation du salon de ticket #{ticket.id}: {create_err}")
                raise HTTPException(500, detail=f"Impossible de recréer le salon Discord : {create_err}")

            # Poster l'Embed d'accueil d'origine avec toutes les réponses et les boutons Fermer / Fermer avec raison
            welcome_embed, pings_content = build_ticket_welcome_embed_and_pings(
                panel=panel,
                ticket_id=ticket.id,
                ticket_name=ticket_name,
                creator_id=ticket.creator_id,
                answers_dict=ticket.form_answers if isinstance(ticket.form_answers, dict) else {},
            )
            await channel.send(content=pings_content, embed=welcome_embed, view=TicketView(panel))

            # Joindre l'historique HTML précédent si une archive existait
            res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
            archive = res_arc.scalar_one_or_none()
            reopen_embed = discord.Embed(
                title="🔓 Ticket Ré-ouvert & Salon Restauré",
                description=(
                    "Ce ticket avait été supprimé de Discord et vient d'être **ré-ouvert depuis le Dashboard Web**.\n"
                    "Le salon a été recréé dans la catégorie active avec l'intégralité de ses permissions d'origine."
                ),
                color=discord.Color.green(),
            )
            if archive and archive.html_content:
                reopen_embed.add_field(
                    name="📜 Historique précédent",
                    value="La retranscription des échanges précédents est jointe ci-dessous et reste consultable sur le Dashboard Web.",
                    inline=False,
                )
                hist_file = discord.File(
                    io.BytesIO(archive.html_content.encode()),
                    filename=f"transcript-historique-{ticket.id}.html",
                )
                await channel.send(embed=reopen_embed, file=hist_file)
            else:
                await channel.send(embed=reopen_embed)

        ticket.status = "open"
        ticket.close_reason = None
        ticket.closed_by_username = None
        ticket.updated_at = datetime.datetime.utcnow()
        await session.commit()

        # Envoi du log de ré-ouverture dans le salon de logs si activé
        try:
            await send_ticket_log(
                bot_client=bot,
                action="reopen",
                ticket=ticket,
                panel=panel,
                source="web",
                actor_display=actor_display,
                recreated=recreated,
                channel_id=channel.id if channel else None,
            )
        except Exception as log_err:
            logger.warning(f"Erreur envoi log ré-ouverture web: {log_err}")

        result = ticket_to_dict(ticket)
        result["recreated"] = recreated
        return result


@app.post("/api/tickets/{ticket_id}/delete-channel", dependencies=[Depends(get_api_key)])
@app.delete("/api/tickets/{ticket_id}/channel", dependencies=[Depends(get_api_key)])
async def delete_ticket_channel(ticket_id: int, request: Request):
    """
    Supprime le salon Discord d'un ticket depuis l'interface Web (tout en conservant le ticket et son archive sur le Web),
    puis rééquilibre et supprime les éventuelles catégories en double (`archive (2)`, `ticket (2)`) si on repasse <= 50 salons.
    """
    from bot import cleanup_and_rebalance_category_family

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    actor_display, actor_short = extract_web_actor(request, body)

    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(404, detail="Ticket introuvable.")

        panel = await session.get(Panel, ticket.panel_id) if ticket.panel_id else None

        channel = None
        if ticket.channel_id:
            try:
                channel = bot.get_channel(int(ticket.channel_id)) or await bot.fetch_channel(int(ticket.channel_id))
            except Exception:
                channel = None

        guild = channel.guild if channel else None
        deleted_channel_id = channel.id if channel else ticket.channel_id

        if channel:
            _explicitly_deleted_channel_ids.add(channel.id)
            # Sauvegarder une dernière fois le transcript HTML si aucune archive n'existe encore
            try:
                res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
                archive = res_arc.scalar_one_or_none()
                if not archive or not archive.html_content:
                    import chat_exporter
                    transcript = await chat_exporter.export(
                        channel,
                        limit=None,
                        tz_info="Europe/Paris",
                        military_time=True,
                        bot=bot,
                    )
                    if transcript:
                        if archive:
                            archive.html_content = transcript
                        else:
                            session.add(TicketArchive(ticket_id=ticket.id, html_content=transcript))
            except Exception as arc_err:
                logger.warning(f"Impossible d'exporter le transcript avant suppression du salon #{ticket.id}: {arc_err}")

            try:
                await channel.delete(reason=f"Salon de ticket supprimé depuis le Dashboard Web par {actor_short}")
            except Exception as del_err:
                logger.warning(f"Erreur lors de la suppression du salon Discord pour le ticket #{ticket.id}: {del_err}")

        ticket.channel_id = None
        ticket.status = "closed"
        ticket.updated_at = datetime.datetime.utcnow()
        await session.commit()

        # Envoi du log de suppression dans le salon de logs si activé
        try:
            await send_ticket_log(
                bot_client=bot,
                action="delete",
                ticket=ticket,
                panel=panel,
                source="web",
                actor_display=actor_display,
                channel_id=deleted_channel_id,
            )
        except Exception as log_err:
            logger.warning(f"Erreur envoi log suppression web: {log_err}")

        # Rééquilibrer et supprimer les catégories en double (`archive (2)`, `ticket (2)`) si on repasse <= 50 salons
        if guild and panel:
            if panel.archive_category_id:
                try:
                    await cleanup_and_rebalance_category_family(guild, panel.archive_category_id, bot)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage catégorie archive après suppression web: {e}")
            if panel.active_category_id:
                try:
                    await cleanup_and_rebalance_category_family(guild, panel.active_category_id, bot)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage catégorie active après suppression web: {e}")

        return ticket_to_dict(ticket)

@app.get("/api/tickets/{ticket_id}/archive", dependencies=[Depends(get_api_key)])
async def get_archive(ticket_id: int):
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(404, detail="Ticket introuvable.")

        # Si le salon existe encore sur Discord, on peut exporter en direct la version la plus fraîche
        if ticket.channel_id:
            try:
                channel = bot.get_channel(ticket.channel_id) or await bot.fetch_channel(ticket.channel_id)
                if channel:
                    import chat_exporter
                    live_transcript = await chat_exporter.export(
                        channel,
                        limit=None,
                        tz_info="Europe/Paris",
                        military_time=True,
                        bot=bot
                    )
                    # Mise à jour du cache dans la base
                    res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
                    archive = res_arc.scalar_one_or_none()
                    if archive:
                        archive.html_content = live_transcript
                    else:
                        session.add(TicketArchive(ticket_id=ticket.id, html_content=live_transcript))
                    await session.commit()
                    return {"html_content": live_transcript}
            except Exception as e:
                logger.warning(f"Impossible de générer le live transcript pour ticket {ticket_id}: {e}")

        # Sinon, renvoie l'archive stockée en base de données
        res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
        archive = res_arc.scalar_one_or_none()
        if archive and archive.html_content:
            return {"html_content": archive.html_content}
        raise HTTPException(404, detail="Aucune archive disponible.")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, reload_excludes=[".git", ".git/*", "*.log"])


