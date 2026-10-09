#!/bin/bash

echo "===================================================="
echo "   🚀 Démarrage du Bot Discord Ticketing (Local)    "
echo "===================================================="

# Vérification de la présence du fichier .env
if [ ! -f ".env" ]; then
    echo "⚠️ Le fichier .env est introuvable."
    echo "Création d'un fichier .env à partir de .env.example..."
    cp .env.example .env
    echo "❌ Veuillez remplir vos tokens dans le fichier .env avant de relancer !"
    exit 1
fi

echo "📦 Construction de l'image locale et lancement des conteneurs..."
# On utilise le fichier dev pour forcer le build en local au lieu de pull depuis github
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

echo ""
echo "✅ Démarrage terminé !"
echo "🌐 API accessible sur : http://localhost:8000/docs"
echo "📜 Pour afficher les logs en temps réel, utilisez la commande :"
echo "   docker compose logs -f bot"
echo "===================================================="
