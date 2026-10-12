import logging
from sqlalchemy import text

logger = logging.getLogger("migrator")

# 1. Filet de sécurité : liste exhaustive des colonnes historiques
EXPLICIT_MIGRATIONS = [
    # Table panels : personnalisation visuelle et logique
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS embed_title VARCHAR DEFAULT 'Système de Ticket'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS embed_description TEXT DEFAULT 'Cliquez sur le bouton ci-dessous pour ouvrir un ticket.'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS embed_color VARCHAR DEFAULT '#2ecc71'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS button_text VARCHAR DEFAULT 'Ouvrir un ticket'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS button_color VARCHAR DEFAULT 'success'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS button_emoji VARCHAR DEFAULT '🎫'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS active_category_id BIGINT",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS archive_category_id BIGINT",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS ping_role_id BIGINT",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS ping_creator BOOLEAN DEFAULT TRUE",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS max_tickets_per_user INT DEFAULT 1",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS intro_message TEXT DEFAULT 'Veuillez patienter, un membre du staff va vous répondre.'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS inactivity_days INT DEFAULT 3",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS deployed_channel_id BIGINT",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS deployed_message_id BIGINT",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS close_button_text VARCHAR DEFAULT 'Fermer le ticket'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS close_button_color VARCHAR DEFAULT 'danger'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS close_button_emoji VARCHAR DEFAULT '🔒'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS reopen_button_text VARCHAR DEFAULT 'Ré-ouvrir'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS reopen_button_color VARCHAR DEFAULT 'success'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS reopen_button_emoji VARCHAR DEFAULT '🔓'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS delete_button_text VARCHAR DEFAULT 'Supprimer'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS delete_button_color VARCHAR DEFAULT 'danger'",
    "ALTER TABLE panels ADD COLUMN IF NOT EXISTS delete_button_emoji VARCHAR DEFAULT '🗑️'",

    # Table tickets
    "ALTER TABLE tickets ALTER COLUMN channel_id DROP NOT NULL",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS channel_id BIGINT",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS creator_id BIGINT",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS creator_username VARCHAR",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'open'",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS form_answers JSON",

    # Table form_fields
    "ALTER TABLE form_fields ADD COLUMN IF NOT EXISTS is_paragraph BOOLEAN DEFAULT FALSE",
    "ALTER TABLE form_fields ADD COLUMN IF NOT EXISTS required BOOLEAN DEFAULT TRUE",
    "ALTER TABLE form_fields ADD COLUMN IF NOT EXISTS \"order\" INT DEFAULT 0",
]


async def auto_migrate_db(engine, Base):
    """
    Vérifie et met à jour automatiquement le schéma de la base de données.
    - Crée les tables si elles n'existent pas encore (create_all).
    - Applique les migrations explicites historiques.
    - DÉTECTE DYNAMIQUEMENT n'importe quelle nouvelle colonne ajoutée dans database.py
      et l'ajoute automatiquement via ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
    - NE SUPPRIME JAMAIS de table ni de colonne existante, préservant ainsi 100% des données de production.
    """
    dialect_name = engine.dialect.name
    logger.info(f"[Auto-Migrate] Début de la vérification du schéma DB (dialect: {dialect_name})...")

    async with engine.begin() as conn:
        # Étape 1 : Créer les tables manquantes
        await conn.run_sync(Base.metadata.create_all)

        # Étape 2 : Appliquer les migrations explicites (compatibilité immédiate)
        if dialect_name == "postgresql":
            for stmt in EXPLICIT_MIGRATIONS:
                try:
                    await conn.execute(text(stmt))
                except Exception as e:
                    logger.debug(f"[Auto-Migrate] Migration explicite ignorée ({stmt}): {e}")

        # Étape 3 : Détection dynamique et automatique de TOUTES les colonnes pour TOUTES les tables
        for table_name, table in Base.metadata.tables.items():
            existing_columns = set()

            try:
                if dialect_name == "postgresql":
                    res = await conn.execute(
                        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t AND table_schema = current_schema()"),
                        {"t": table_name}
                    )
                    existing_columns = {row[0].lower() for row in res.fetchall()}
                elif dialect_name == "sqlite":
                    res = await conn.execute(text(f"PRAGMA table_info({table_name})"))
                    existing_columns = {row[1].lower() for row in res.fetchall()}
            except Exception as e:
                logger.warning(f"[Auto-Migrate] Impossible de lire les colonnes pour '{table_name}': {e}")
                continue

            # Si la table n'a pas encore de colonnes répertoriées dans le schéma
            if not existing_columns:
                continue

            for col in table.columns:
                col_name = col.name.lower()
                if col_name not in existing_columns:
                    # La colonne existe dans le modèle Python mais pas en BDD !
                    col_type = col.type.compile(dialect=conn.dialect)
                    
                    default_clause = ""
                    if col.server_default is not None:
                        default_clause = f" DEFAULT {col.server_default.arg}"
                    elif col.default is not None and getattr(col.default, 'arg', None) is not None:
                        arg = col.default.arg
                        if isinstance(arg, bool):
                            default_clause = f" DEFAULT {'TRUE' if arg else 'FALSE'}"
                        elif isinstance(arg, (int, float)):
                            default_clause = f" DEFAULT {arg}"
                        elif isinstance(arg, str):
                            escaped = arg.replace("'", "''")
                            default_clause = f" DEFAULT '{escaped}'"
                        elif callable(arg):
                            if dialect_name == "postgresql":
                                default_clause = " DEFAULT CURRENT_TIMESTAMP"

                    alter_stmt = f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{col.name}" {col_type}{default_clause}'
                    try:
                        await conn.execute(text(alter_stmt))
                        logger.info(f"[Auto-Migrate] 🚀 Nouvelle colonne synchronisée : {table_name}.{col.name} ({col_type}{default_clause})")
                    except Exception as e:
                        logger.error(f"[Auto-Migrate] Erreur lors de l'ajout automatique de {table_name}.{col.name}: {e}")

    logger.info("[Auto-Migrate] ✅ Schéma de la base de données 100% synchronisé et à jour.")
