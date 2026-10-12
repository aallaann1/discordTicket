import asyncio
import os
import logging
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
from schemas import PanelCreate, FieldCreate
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
        "reopen_button_text": p.reopen_button_text or "Ré-ouvrir",
        "reopen_button_color": p.reopen_button_color or "success",
        "reopen_button_emoji": p.reopen_button_emoji or "🔓",
        "delete_button_text": p.delete_button_text or "Supprimer",
        "delete_button_color": p.delete_button_color or "danger",
        "delete_button_emoji": p.delete_button_emoji or "🗑️",
    }

def ticket_to_dict(t: Ticket) -> dict:
    return {
        "id": t.id,
        "panel_id": t.panel_id,
        "channel_id": str(t.channel_id) if t.channel_id else None,
        "creator_id": str(t.creator_id) if t.creator_id else None,
        "creator_username": getattr(t, "creator_username", None),
        "status": t.status,
        "form_answers": t.form_answers,
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
            reopen_button_text=panel_data.reopen_button_text,
            reopen_button_color=panel_data.reopen_button_color,
            reopen_button_emoji=panel_data.reopen_button_emoji,
            delete_button_text=panel_data.delete_button_text,
            delete_button_color=panel_data.delete_button_color,
            delete_button_emoji=panel_data.delete_button_emoji,
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

@app.get("/api/panels/{panel_id}/fields", dependencies=[Depends(get_api_key)])
async def get_fields(panel_id: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(FormField).where(FormField.panel_id == panel_id))
        return res.scalars().all()

@app.post("/api/panels/{panel_id}/fields", dependencies=[Depends(get_api_key)])
async def create_field(panel_id: int, field_data: FieldCreate):
    async with AsyncSessionLocal() as session:
        field = FormField(panel_id=panel_id, **field_data.dict())
        session.add(field)
        await session.commit()
        return field

@app.put("/api/fields/{field_id}", dependencies=[Depends(get_api_key)])
async def update_field(field_id: int, field_data: FieldCreate):
    async with AsyncSessionLocal() as session:
        field = await session.get(FormField, field_id)
        if field:
            for k, v in field_data.dict().items():
                setattr(field, k, v)
            await session.commit()
            return field
        raise HTTPException(404)

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
async def close_ticket(ticket_id: int):
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
        
        if transcript:
            res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
            archive = res_arc.scalar_one_or_none()
            if archive:
                archive.html_content = transcript # Écrase la retranscription précédente
            else:
                session.add(TicketArchive(ticket_id=ticket.id, html_content=transcript))

        await session.commit()

        if channel:
            # Retirer l'accès au membre créateur
            try:
                member = channel.guild.get_member(ticket.creator_id)
                if not member:
                    member = await channel.guild.fetch_member(ticket.creator_id)
                if member:
                    await channel.set_permissions(member, overwrite=None)
            except Exception:
                pass

            # Déplacer vers la catégorie archive si configurée
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

            # Envoyer le message d'archive avec boutons Ré-ouvrir et Supprimer
            try:
                import io
                from bot import ArchivedTicketView
                embed = discord.Embed(
                    title="🔒 Ticket Fermé",
                    description="Le ticket a été fermé depuis le Dashboard Web.\nLa conversation a été archivée.",
                    color=discord.Color.red()
                )
                if transcript:
                    file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{ticket.id}.html")
                    await channel.send(embed=embed, file=file, view=ArchivedTicketView(panel))
                else:
                    await channel.send(embed=embed, view=ArchivedTicketView(panel))
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


