import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
from dotenv import load_dotenv
import logging

# Chargement des variables d'environnement
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')
TICKET_CATEGORY_ID = os.getenv('TICKET_CATEGORY_ID')

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer le Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Le ticket va être fermé dans 5 secondes...", ephemeral=True)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Fermé par {interaction.user.name}")
        except Exception as e:
            logging.error(f"Erreur lors de la suppression du channel: {e}")

class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Créer un Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket_button", emoji="🎫")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category = None
        
        # Trouver la catégorie de tickets si l'ID est fourni
        if TICKET_CATEGORY_ID:
            try:
                category = discord.utils.get(guild.categories, id=int(TICKET_CATEGORY_ID))
            except ValueError:
                logging.warning("TICKET_CATEGORY_ID n'est pas un entier valide dans le .env")

        # Permissions du nouveau channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Format du nom du ticket
        import re
        safe_username = re.sub(r'[^a-zA-Z0-9-]', '', interaction.user.name).lower()
        channel_name = f"ticket-{safe_username}"

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Ticket créé par {interaction.user.name}"
            )
            
            await interaction.response.send_message(f"Ticket créé : {ticket_channel.mention}", ephemeral=True)
            
            # Message initial dans le ticket
            close_view = CloseTicketView()
            embed = discord.Embed(
                title="Nouveau Ticket",
                description=f"Bonjour {interaction.user.mention} ! \nUn modérateur va vous répondre sous peu. Vous pouvez expliquer votre problème ci-dessous.",
                color=discord.Color.blue()
            )
            await ticket_channel.send(embed=embed, view=close_view)
            
        except discord.Forbidden:
            await interaction.response.send_message("Je n'ai pas les permissions nécessaires pour créer un salon.", ephemeral=True)
        except Exception as e:
            logging.error(f"Erreur lors de la création du ticket: {e}")
            await interaction.response.send_message("Une erreur s'est produite lors de la création du ticket.", ephemeral=True)

class TicketingBot(commands.Bot):
    def __init__(self):
        # Intents par défaut, ajuster si nécessaire (par ex. intents.message_content = True)
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Enregistrer les vues pour qu'elles persistent après les redémarrages
        self.add_view(TicketButton())
        self.add_view(CloseTicketView())
        
        # Synchronisation des slash commands (commandes /)
        if GUILD_ID:
            try:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logging.info(f"Slash commands synchronisées pour la guilde {GUILD_ID}.")
            except Exception as e:
                logging.error(f"Erreur de synchronisation des commandes à la guilde: {e}")
        else:
            await self.tree.sync()
            logging.info("Slash commands synchronisées globalement.")

    async def on_ready(self):
        logging.info(f'Connecté en tant que {self.user} (ID: {self.user.id})')
        logging.info('------')

bot = TicketingBot()

@bot.tree.command(name="setup_tickets", description="Initialise le panneau de création de tickets dans le salon actuel.")
@app_commands.default_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    """Commande Slash administrateur pour générer le bouton de ticket."""
    view = TicketButton()
    embed = discord.Embed(
        title="Système de Tickets",
        description="Cliquez sur le bouton ci-dessous pour ouvrir un ticket et contacter l'équipe.",
        color=discord.Color.green()
    )
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Panneau de tickets initialisé.", ephemeral=True)

if __name__ == '__main__':
    if not TOKEN:
        logging.error("Aucun token fourni. Veuillez définir DISCORD_BOT_TOKEN dans le fichier .env.")
    else:
        bot.run(TOKEN)
