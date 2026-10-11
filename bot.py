import discord
from discord.ext import commands, tasks
import chat_exporter
import io
import datetime
import logging
import asyncio
from sqlalchemy.future import select
from sqlalchemy import func
from database import AsyncSessionLocal, Panel, FormField, Ticket, TicketArchive

logger = logging.getLogger(__name__)

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

        # 1. Résolution de la catégorie active
        category = None
        if self.panel.active_category_id:
            try:
                cat_id = int(str(self.panel.active_category_id).strip())
                if cat_id > 0:
                    category = guild.get_channel(cat_id) or discord.utils.get(guild.categories, id=cat_id)
                    if not category:
                        try:
                            category = await interaction.client.fetch_channel(cat_id)
                        except Exception as e:
                            logger.warning(f"Impossible de fetch la catégorie {cat_id}: {e}")
            except Exception as e:
                logger.error(f"Erreur avec active_category_id: {e}")

        # Permissions : Héritage des permissions de la catégorie (pour que le Staff y ait accès quoi qu'il arrive)
        overwrites = {}
        if category and category.overwrites:
            for target, ow in category.overwrites.items():
                overwrites[target] = ow

        # @everyone n'a JAMAIS accès au ticket
        overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False)
        # Seul le membre créateur a accès
        overwrites[interaction.user] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True, embed_links=True
        )
        # Le bot conserve les droits complets
        overwrites[guild.me] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True, manage_channels=True, manage_permissions=True
        )

        # Si un rôle staff est configuré sur le panel, on lui garantit explicitement l'accès
        if self.panel.ping_role_id:
            try:
                r_id = int(str(self.panel.ping_role_id).strip())
                role = guild.get_role(r_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        read_messages=True, send_messages=True, attach_files=True, embed_links=True
                    )
            except Exception:
                pass

        # 2. Réponses du formulaire
        answers_dict = {child.label: child.value for child in self.children}

        # 3. Création du ticket en BDD pour obtenir son ID unique incrémenté
        async with AsyncSessionLocal() as session:
            ticket = Ticket(
                panel_id=self.panel.id, 
                channel_id=None, 
                creator_id=interaction.user.id,
                form_answers=answers_dict,
                status="open"
            )
            session.add(ticket)
            await session.flush()
            ticket_id = ticket.id
            ticket_name = f"ticket{ticket_id:03d}"

            # 4. Création du salon textuel avec le nom incrémenté
            try:
                channel = await guild.create_text_channel(
                    name=ticket_name, category=category, overwrites=overwrites
                )
                ticket.channel_id = channel.id
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Erreur création salon ticket: {e}")
                return await interaction.followup.send(f"❌ Erreur lors de la création du salon : {e}", ephemeral=True)

        # 5. Construction de l'Embed de bienvenue
        color_int = int(self.panel.embed_color.lstrip('#'), 16) if self.panel.embed_color else 0x2ecc71
        embed = discord.Embed(
            description=self.panel.intro_message or "Bienvenue dans votre ticket !", 
            color=discord.Color(color_int)
        )
        
        for label, value in answers_dict.items():
            embed.add_field(name=label, value=value, inline=False)
            
        embed.set_footer(text=f"Système de Ticketing - {ticket_name}")
        
        # 6. Pings paramétrables (créateur et/ou rôle staff)
        pings = []
        if getattr(self.panel, "ping_creator", True):
            pings.append(interaction.user.mention)
        if self.panel.ping_role_id:
            try:
                r_id = int(str(self.panel.ping_role_id).strip())
                if r_id > 0:
                    pings.append(f"<@&{r_id}>")
            except Exception:
                pass
        
        content = " ".join(pings) if pings else None
        
        # 7. Envoi du message initial avec uniquement le bouton de fermeture
        await channel.send(content=content, embed=embed, view=TicketView(self.panel))
        await interaction.followup.send(f"✅ Ticket ouvert : {channel.mention}", ephemeral=True)


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
            emoji=btn_emoji
        )
        button.callback = self.close_ticket
        self.add_item(button)

    async def close_ticket(self, interaction: discord.Interaction):
        await interaction.response.defer()
        channel = interaction.channel
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
            ticket = result.scalar_one_or_none()
            if not ticket:
                return await interaction.followup.send("❌ Ticket introuvable.", ephemeral=True)
            
            panel = await session.get(Panel, ticket.panel_id)

            # SÉCURITÉ : Seuls le créateur du ticket et le staff peuvent fermer
            is_creator = (interaction.user.id == ticket.creator_id)
            staff_member = is_staff(interaction.user, panel)
            if not (is_creator or staff_member):
                return await interaction.followup.send(
                    "❌ Seuls le créateur du ticket et les membres du staff peuvent fermer ce ticket.", 
                    ephemeral=True
                )
            
            transcript = await chat_exporter.export(
                channel,
                limit=None,
                tz_info="Europe/Paris",
                military_time=True,
                bot=interaction.client
            )
            
            ticket.status = "closed"
            ticket.updated_at = datetime.datetime.utcnow()
            res_arc = await session.execute(select(TicketArchive).where(TicketArchive.ticket_id == ticket.id))
            archive = res_arc.scalar_one_or_none()
            if archive:
                archive.html_content = transcript # Écrase l'ancienne retranscription
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
                await channel.set_permissions(member, overwrite=None)
            
            # Déplacer vers la catégorie d'archive si configurée
            if panel and panel.archive_category_id:
                try:
                    arch_id = int(str(panel.archive_category_id).strip())
                    cat = interaction.guild.get_channel(arch_id) or discord.utils.get(interaction.guild.categories, id=arch_id)
                    if not cat:
                        try:
                            cat = await interaction.client.fetch_channel(arch_id)
                        except Exception:
                            pass
                    if cat:
                        await channel.edit(category=cat)
                except Exception as e:
                    logger.warning(f"Erreur déplacement archive: {e}")

            # 1. Enlever le bouton close du message actuel
            try:
                await interaction.message.edit(view=None)
            except Exception as e:
                logger.warning(f"Impossible d'éditer le message original: {e}")

            # 2. Envoyer l'archive avec la vue contenant UNIQUEMENT Ré-ouvrir et Supprimer
            file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{ticket.id}.html")
            embed = discord.Embed(
                title="🔒 Ticket Fermé", 
                description=f"Le ticket a été fermé par {interaction.user.mention}.\nLa conversation a été archivée.", 
                color=discord.Color.red()
            )
            await channel.send(embed=embed, file=file, view=ArchivedTicketView(panel))


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
            res = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
            ticket = res.scalar_one_or_none()
            
            if not ticket:
                return await interaction.followup.send("❌ Ticket introuvable.", ephemeral=True)

            panel = await session.get(Panel, ticket.panel_id)

            # SÉCURITÉ : Seuls les membres du staff peuvent ré-ouvrir
            if not is_staff(interaction.user, panel):
                return await interaction.followup.send(
                    "❌ Seuls les membres du staff peuvent ré-ouvrir ce ticket.", 
                    ephemeral=True
                )

            ticket.status = "open"
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
            
            # Déplacer vers la catégorie active si configurée
            if panel and panel.active_category_id:
                try:
                    act_id = int(str(panel.active_category_id).strip())
                    cat = interaction.guild.get_channel(act_id) or discord.utils.get(interaction.guild.categories, id=act_id)
                    if not cat:
                        try:
                            cat = await interaction.client.fetch_channel(act_id)
                        except Exception:
                            pass
                    if cat:
                        await channel.edit(category=cat)
                except Exception as e:
                    logger.warning(f"Erreur déplacement catégorie active: {e}")

        # Enlever les boutons Ré-ouvrir et Supprimer du message
        try:
            await interaction.message.edit(view=None)
        except Exception as e:
            logger.warning(f"Impossible d'éditer le message archivé: {e}")

        # Envoyer le message de réouverture avec SEULEMENT le bouton Fermer
        await channel.send(f"🔓 Ticket réouvert par {interaction.user.mention}", view=TicketView(panel))

    async def delete(self, interaction: discord.Interaction):
        await interaction.response.defer()
        channel = interaction.channel

        # SÉCURITÉ : Seuls les membres du staff peuvent supprimer définitivement
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Ticket).where(Ticket.channel_id == channel.id))
            ticket = res.scalar_one_or_none()
            panel = await session.get(Panel, ticket.panel_id) if ticket else None

        if not is_staff(interaction.user, panel):
            return await interaction.followup.send(
                "❌ Seuls les membres du staff peuvent supprimer définitivement ce ticket.", 
                ephemeral=True
            )

        embed = discord.Embed(
            description=f"🗑️ Ticket supprimé par {interaction.user.mention}. Fermeture dans 3 secondes...",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        await asyncio.sleep(3)
        await channel.delete()


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
                    return await interaction.response.send_message("❌ Panneau introuvable.", ephemeral=True)
                
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

                res = await session.execute(select(FormField).where(FormField.panel_id == panel_id))
                fields = res.scalars().all()
                
                if not fields:
                    return await interaction.response.send_message("❌ Aucune question configurée pour ce panneau.", ephemeral=True)
                
                await interaction.response.send_modal(FormModal(panel, fields))

