import asyncio
import os
import logging
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager
import uvicorn
from dotenv import load_dotenv

from database import engine, Base, AsyncSessionLocal, Panel
from bot import bot, PanelView
from sqlalchemy.future import select

load_dotenv()
logging.basicConfig(level=logging.INFO)

# --- SÉCURITÉ DE L'API ---
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Security(api_key_header)):
    expected_key = os.getenv("API_KEY")
    if not expected_key:
        logging.warning("⚠️ API_KEY n'est pas définie dans le .env ! L'API bloque toutes les requêtes.")
        raise HTTPException(status_code=500, detail="Configuration serveur manquante (API_KEY).")
    if api_key == expected_key:
        return api_key
    raise HTTPException(status_code=403, detail="Accès non autorisé : Clé API invalide.")
# -------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    token = os.getenv("DISCORD_BOT_TOKEN")
    if token:
        asyncio.create_task(bot.start(token))
    else:
        logging.error("DISCORD_BOT_TOKEN manquant dans le .env")
    
    yield

app = FastAPI(title="Discord Ticket API", lifespan=lifespan)

# --- ROUTES API (Sécurisées) ---

@app.get("/api/stats", dependencies=[Depends(get_api_key)])
async def get_stats():
    # Exemple de stat rapide : On pourrait compter en base le nombre de tickets ouverts
    return {"status": "ok", "message": "API Sécurisée."}

@app.get("/api/panels", dependencies=[Depends(get_api_key)])
async def get_panels():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Panel))
        panels = res.scalars().all()
        return panels

@app.post("/api/panels/{panel_id}/send", dependencies=[Depends(get_api_key)])
async def send_panel_to_discord(panel_id: int, channel_id: int):
    """Demande au bot d'envoyer le bouton d'ouverture de ticket dans un salon spécifique"""
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if not panel:
            raise HTTPException(status_code=404, detail="Panel introuvable.")
        
        channel = bot.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Salon Discord introuvable ou le bot n'y a pas accès.")
        
        import discord
        embed = discord.Embed(
            title=panel.name, 
            description="Cliquez sur le bouton ci-dessous pour nous contacter.", 
            color=discord.Color.green()
        )
        await channel.send(embed=embed, view=PanelView(panel_id, panel.button_text))
        
        return {"success": True, "message": "Bouton envoyé avec succès."}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
