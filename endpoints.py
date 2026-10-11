from fastapi import Request
from sqlalchemy import func
from database import FormField, Ticket, TicketArchive
from schemas import PanelCreate, FieldCreate

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
            inactivity_days=panel_data.inactivity_days
        )
        if panel_data.active_category_id:
            panel.active_category_id = int(panel_data.active_category_id)
        if panel_data.archive_category_id:
            panel.archive_category_id = int(panel_data.archive_category_id)
        if panel_data.ping_role_id:
            panel.ping_role_id = int(panel_data.ping_role_id)
        session.add(panel)
        await session.commit()
        return panel

@app.put("/api/panels/{panel_id}", dependencies=[Depends(get_api_key)])
async def update_panel(panel_id: int, panel_data: PanelCreate):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if not panel:
            raise HTTPException(status_code=404)
        for key, value in panel_data.dict().items():
            if key in ["active_category_id", "archive_category_id", "ping_role_id"] and value:
                setattr(panel, key, int(value))
            else:
                setattr(panel, key, value)
        await session.commit()
        return panel

@app.delete("/api/panels/{panel_id}", dependencies=[Depends(get_api_key)])
async def delete_panel(panel_id: int):
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if panel:
            await session.delete(panel)
            await session.commit()
        return {"success": True}

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
async def get_tickets():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Ticket).order_by(Ticket.created_at.desc()))
        return res.scalars().all()

@app.post("/api/tickets/{ticket_id}/close", dependencies=[Depends(get_api_key)])
async def close_ticket(ticket_id: int):
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket:
            ticket.status = "closed"
            await session.commit()
            
            # Si le bot a une mthode close_ticket
            try:
                # bot logic here to close channel... we'll try to find the channel
                channel = bot.get_channel(ticket.channel_id)
                if channel:
                    await channel.delete()
            except:
                pass
            
            return ticket
        raise HTTPException(404)

@app.get("/api/tickets/{ticket_id}/archive", dependencies=[Depends(get_api_key)])
async def get_archive(ticket_id: int):
    async with AsyncSessionLocal() as session:
        archive = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket_id))
        res = archive.scalars().first()
        if res:
            return {"html_content": res.html_content}
        raise HTTPException(404)
