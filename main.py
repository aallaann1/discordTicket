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
from sqlalchemy import func, text

from database import engine, Base, AsyncSessionLocal, Panel, FormField, Ticket, TicketArchive
from bot import bot, PanelView
from schemas import PanelCreate, FieldCreate, BotProfileUpdate
from migrator import auto_migrate_db

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Security(api_key_header)):
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
        "embed_description": p.embed_description,
        "embed_color": p.embed_color,
        "button_text": p.button_text,
        "button_color": p.button_color,
        "button_emoji": p.button_emoji,
        "active_category_id": str(p.active_category_id) if p.active_category_id else None,
        "archive_category_id": str(p.archive_category_id) if p.archive_category_id else None,
        "ping_role_id": str(p.ping_role_id) if p.ping_role_id else None,
        "ping_creator": bool(p.ping_creator) if p.ping_creator is not None else True,
        "max_tickets_per_user": p.max_tickets_per_user if p.max_tickets_per_user is not None else 1,
        "intro_message": p.intro_message,
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
    return {
        "id": t.id,
        "panel_id": t.panel_id,
        "channel_id": str(t.channel_id) if t.channel_id else None,
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Synchronisation automatique et sécurisée du schéma de la BDD (aucune perte de données)
    await auto_migrate_db(engine, Base)
    
    # Création d'un Panel par défaut (WTB) si aucun n'existe
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
            
    token = os.getenv("DISCORD_BOT_TOKEN")
    if token:
        asyncio.create_task(bot.start(token))
    
    yield

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

@app.get("/api/panels", dependencies=[Depends(get_api_key)])
async def get_panels():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Panel))
        return [panel_to_dict(p) for p in res.scalars().all()]

@app.post("/api/panels", dependencies=[Depends(get_api_key)])
async def create_panel(panel_data: PanelCreate):
    async with AsyncSessionLocal() as session:
        panel = Panel(
            name=panel_data.name,
            embed_title=panel_data.embed_title,
            embed_description=panel_data.embed_description,
            embed_color=panel_data.embed_color,
            button_text=panel_data.button_text,
            button_color=panel_data.button_color,
            button_emoji=panel_data.button_emoji,
            intro_message=panel_data.intro_message,
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
                ch = bot.get_channel(panel.deployed_channel_id) or await bot.fetch_channel(panel.deployed_channel_id)
                if ch:
                    msg = await ch.fetch_message(panel.deployed_message_id)
                    if msg:
                        color_int = int(panel.embed_color.lstrip('#'), 16) if panel.embed_color else 0x2ecc71
                        embed = discord.Embed(
                            title=panel.embed_title,
                            description=panel.embed_description,
                            color=discord.Color(color_int)
                        )
                        embed.set_footer(text="Système de Ticketing")
                        await msg.edit(embed=embed, view=PanelView(panel))
                        logger.info(f"Panel {panel.id} auto-redéployé sur message {panel.deployed_message_id}")
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
                    ch = bot.get_channel(panel.deployed_channel_id) or await bot.fetch_channel(panel.deployed_channel_id)
                    msg = await ch.fetch_message(panel.deployed_message_id)
                    await msg.delete()
                except Exception as e:
                    logger.warning(f"Impossible de supprimer le message déployé du panel {panel_id}: {e}")
            await session.delete(panel)
            await session.commit()
        return {"success": True}

@app.post("/api/panels/{panel_id}/send", dependencies=[Depends(get_api_key)])
async def send_panel_to_discord(panel_id: int, channel_id: int):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if not panel:
            raise HTTPException(status_code=404, detail="Panel introuvable.")
        
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Salon Discord introuvable.")
        
        color_int = int(panel.embed_color.lstrip('#'), 16) if panel.embed_color else 0x2ecc71
        embed = discord.Embed(
            title=panel.embed_title, 
            description=panel.embed_description, 
            color=discord.Color(color_int)
        )
        embed.set_footer(text="Système de Ticketing")
        
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

@app.post("/api/tickets/{ticket_id}/close", dependencies=[Depends(get_api_key)])
async def close_ticket(ticket_id: int, request: Request):
    close_reason = None
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("reason"):
            close_reason = str(body.get("reason")).strip()
    except Exception:
        pass

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
        ticket.closed_by_username = "Dashboard Web"
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

            # 3. Déplacer vers la catégorie archive si configurée
            if panel and panel.archive_category_id:
                try:
                    arch_id = int(str(panel.archive_category_id).strip())
                    cat = channel.guild.get_channel(arch_id) or discord.utils.get(channel.guild.categories, id=arch_id)
                    if not cat:
                        cat = await bot.fetch_channel(arch_id)
                    if cat:
                        await channel.edit(category=cat)
                except Exception as e:
                    logger.warning(f"Erreur déplacement archive: {e}")

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
                        closed_by_display="le Staff (Dashboard Web)",
                        close_reason=close_reason,
                        transcript_html=transcript
                    )
            except Exception as e:
                logger.warning(f"Erreur envoi message archive: {e}")

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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


