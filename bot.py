import discord
from discord.ext import commands, tasks
import chat_exporter
import io
import datetime
import logging
from sqlalchemy.future import select
from database import AsyncSessionLocal, Panel, FormField, Ticket, TicketArchive

logger = logging.getLogger(__name__)

# --- VUE POUR L'API (Envoi du panneau dans un salon) ---
class PanelView(discord.ui.View):
    def __init__(self, panel_id: int, button_text: str):
        super().__init__(timeout=None)
        # Custom ID dynamique basé sur le Panel ID pour le Multi-Panel
        button = discord.ui.Button(label=button_text, style=discord.ButtonStyle.primary, custom_id=f"panel_{panel_id}", emoji="🎫")
        self.add_item(button)
# -------------------------------------------------------

class FormModal(discord.ui.Modal):
    def __init__(self, panel: Panel, fields: list):
        super().__init__(title=f"{panel.name[:45]}")
        self.panel = panel
        for f in sorted(fields, key=lambda x: x.order):
            style = discord.TextStyle.paragraph if f.is_paragraph else discord.TextStyle.short
            self.add_item(discord.ui.TextInput(
                label=f.label[:45], style=style, required=f.required, custom_id=f"field_{f.id}"
            ))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Ticket).where(Ticket.creator_id == interaction.user.id))
            user_tickets = len(result.scalars().all())

        category = guild.get_channel(self.panel.active_category_id) if self.panel.active_category_id else None
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}", category=category, overwrites=overwrites
        )

        async with AsyncSessionLocal() as session:
            ticket = Ticket(panel_id=self.panel.id, channel_id=channel.id, creator_id=interaction.user.id)
            session.add(ticket)
            await session.commit()

        answers = "\n".join([f"**{child.label}**\n{child.value}" for child in self.children])
        embed = discord.Embed(title=f"Ticket : {self.panel.name}", description=self.panel.intro_message, color=discord.Color.blue())
        embed.add_field(name="Informations fournies", value=answers[:1024], inline=False)
        embed.add_field(name="Historique", value=f"⚠️ *Cet utilisateur a déjà ouvert {user_tickets} tickets.*", inline=False)
        
        ping = f"<@&{self.panel.ping_role_id}> " if self.panel.ping_role_id else ""
        await channel.send(content=f"{interaction.user.mention} {ping}", embed=embed, view=TicketView())
        await interaction.followup.send(f"Ticket ouvert : {channel.mention}", ephemeral=True)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer le Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        channel = interaction.channel
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
            ticket = result.scalar_one_or_none()
            if not ticket:
                return await interaction.followup.send("Ticket introuvable.", ephemeral=True)
            
            panel = await session.get(Panel, ticket.panel_id)
            transcript = await chat_exporter.export(channel)
            
            ticket.status = "closed"
            res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
            archive = res_arc.scalar_one_or_none()
            if archive:
                archive.html_content = transcript
            else:
                session.add(TicketArchive(ticket_id=ticket.id, html_content=transcript))
            await session.commit()

            member = interaction.guild.get_member(ticket.creator_id)
            if member:
                await channel.set_permissions(member, overwrite=None)
            
            if panel and panel.archive_category_id:
                cat = interaction.guild.get_channel(panel.archive_category_id)
                if cat:
                    await channel.edit(category=cat)

            file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{ticket.id}.html")
            embed = discord.Embed(title="Ticket Fermé", color=discord.Color.red())
            await channel.send(embed=embed, file=file, view=ArchivedTicketView())


class ArchivedTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ré-ouvrir", style=discord.ButtonStyle.green, custom_id="reopen_ticket")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        channel = interaction.channel
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
            ticket = res.scalar_one_or_none()
            
            if ticket:
                panel = await session.get(Panel, ticket.panel_id)
                ticket.status = "open"
                ticket.updated_at = datetime.datetime.utcnow()
                await session.commit()
                
                member = interaction.guild.get_member(ticket.creator_id)
                if member:
                    await channel.set_permissions(member, read_messages=True, send_messages=True)
                
                if panel and panel.active_category_id:
                    cat = interaction.guild.get_channel(panel.active_category_id)
                    if cat:
                        await channel.edit(category=cat)

        await channel.send(f"Ticket réouvert par {interaction.user.mention}", view=TicketView())

    @discord.ui.button(label="Supprimer", style=discord.ButtonStyle.danger, custom_id="delete_ticket")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.channel.delete()


class TicketingBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        self.add_view(TicketView())
        self.add_view(ArchivedTicketView())
        self.auto_close_task.start()

    @tasks.loop(hours=12)
    async def auto_close_task(self):
        logger.info("Vérification des tickets inactifs...")
        async with AsyncSessionLocal() as session:
            panels_res = await session.execute(select(Panel).where(Panel.inactivity_days > 0))
            panels = panels_res.scalars().all()
            
            for panel in panels:
                limit_date = datetime.datetime.utcnow() - datetime.timedelta(days=panel.inactivity_days)
                res = await session.execute(
                    select(Ticket)
                    .where(Ticket.panel_id == panel.id)
                    .where(Ticket.status == "open")
                    .where(Ticket.updated_at < limit_date)
                )
                tickets = res.scalars().all()
                for ticket in tickets:
                    channel = self.get_channel(ticket.channel_id)
                    if channel:
                        await channel.send("Ticket fermé automatiquement pour inactivité.")
                        ticket.status = "closed"
            await session.commit()

    @auto_close_task.before_loop
    async def before_auto_close(self):
        await self.wait_until_ready()

bot = TicketingBot()

@bot.listen("on_interaction")
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id', '')
        if custom_id.startswith("panel_"):
            panel_id = int(custom_id.split("_")[1])
            async with AsyncSessionLocal() as session:
                panel = await session.get(Panel, panel_id)
                if not panel:
                    return await interaction.response.send_message("Panneau introuvable.", ephemeral=True)
                
                res = await session.execute(select(FormField).where(FormField.panel_id == panel_id))
                fields = res.scalars().all()
                
                if not fields:
                    return await interaction.response.send_message("Aucune question configurée.", ephemeral=True)
                
                await interaction.response.send_modal(FormModal(panel, fields))
