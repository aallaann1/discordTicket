# 📚 Documentation API Complète : Bot Ticketing Discord

Cette documentation est destinée au développeur Frontend. Elle détaille l'intégralité du fonctionnement, des modèles de données et des routes REST nécessaires pour configurer et piloter le Bot Discord depuis une interface web.

---

## 🔐 1. Sécurité et Point d'accès

L'API est strictement privée. L'authentification se fait via un header HTTP obligatoire pour **chaque** requête.

*   **Header d'authentification** : `X-API-Key`
*   **Valeur** : La clé secrète définie dans le fichier `.env` du serveur.
*   **URL de base locale** : `http://localhost:8010/api` *(Attention, le port est 8010 en local).*
*   **Swagger (Test interactif)** : `http://localhost:8010/docs`

> ⚠️ *Si le Header est manquant ou invalide, l'API retourne systématiquement une erreur `403 Forbidden`.*

---

## 🛠️ 2. Workflow Général (Comment concevoir le Front)

Pour que l'utilisateur crée un bouton de Ticket fonctionnel sur Discord, le Frontend doit suivre ces étapes :

1.  **Récupérer le contexte Discord** : Appeler `/api/discord/channels` pour lister les serveurs, salons, et (idéalement) rôles et catégories. Cela te permettra de remplir les listes déroulantes de ton formulaire web.
2.  **Créer le Panel** : Envoyer un `POST /api/panels` avec les options graphiques du bouton et de l'embed.
3.  **Ajouter les Questions** : Envoyer des `POST /api/panels/{id}/fields` pour lier des questions à ce bouton. **⚠️ LIMITÉ À 5 QUESTIONS MAXIMALES (Restriction stricte de Discord).**
4.  **Déployer sur Discord** : Appeler `POST /api/panels/{id}/send?channel_id=...` pour ordonner au bot de publier le bouton final dans le salon Discord choisi.

---

## 📦 3. Modèles de Données (Dictionnaire)

### Objet `Panel` (La configuration visuelle et logique)
Représente le "Bouton" physique sur Discord et ce qui se passe quand on clique dessus.

```json
{
  "id": 1,
  "name": "WTB / Achats", 
  
  // Customisation de l'Embed (Le message au-dessus du bouton)
  "embed_title": "Vendre vos items",
  "embed_description": "Cliquez ci-dessous pour proposer vos items.",
  "embed_color": "#2ecc71", // Obligatoire : Code Hexadécimal
  
  // Customisation du Bouton
  "button_text": "Proposer des articles",
  "button_emoji": "📦", // Optionnel : Un émoji standard
  "button_color": "success", // ENUM strict : "primary" (Bleu), "secondary" (Gris), "success" (Vert), "danger" (Rouge)
  
  // Logique métier (Où le ticket se crée et qui il notifie)
  "active_category_id": 123456789012345, // ID Discord de la catégorie "Ouvert"
  "archive_category_id": 98765432109876, // ID Discord de la catégorie "Fermé"
  "ping_role_id": 456789123456789, // ID du rôle Staff à mentionner (Optionnel)
  "intro_message": "Le staff arrive bientôt. Voici vos réponses :", // Message du bot dans le ticket ouvert
  "inactivity_days": 3 // Nombre de jours avant auto-fermeture
}
```

### Objet `FormField` (La question dans la pop-up)
Les questions qui s'affichent quand l'utilisateur clique sur le bouton.
> **Règle absolue : L'interface web DOIT empêcher l'ajout de plus de 5 champs pour un même `panel_id`.**

```json
{
  "id": 10,
  "panel_id": 1,
  "label": "Mode de livraison :", // Le texte de la question (Max 45 caractères)
  "is_paragraph": false, // False = Input court, True = Textarea multiline
  "required": true, // La réponse est-elle obligatoire ?
  "order": 1 // Ordre d'affichage dans la modale (0, 1, 2, 3, 4)
}
```

### Objet `Ticket` (L'historique et les datas utilisateur)
Récupéré via l'API pour afficher un Dashboard des tickets gérés.

```json
{
  "id": 142,
  "panel_id": 1,
  "channel_id": 888877776666,
  "creator_id": 111122223333,
  "status": "closed", // "open" ou "closed"
  "form_answers": {
    "Mode de livraison :": "Ship",
    "Vos items :": "Carte Dracaufeu Edition 1"
  },
  "created_at": "2024-10-10T12:00:00Z",
  "updated_at": "2024-10-10T12:30:00Z"
}
```

---

## 📡 4. Endpoints Principaux (API Routes)

### 🔌 Discord Data (Pour les menus déroulants du Front)

#### `GET /api/discord/channels`
Récupère l'arborescence des serveurs et salons textes où le bot est installé.
*   **Réponse** :
```json
[
  {
    "guild_id": "123456789",
    "guild_name": "Mon Super Serveur",
    "channels": [
      {"id": "987654321", "name": "général"},
      {"id": "112233445", "name": "support"}
    ]
  }
]
```

### 🏷️ Panels CRUD (Configuration des boutons)

*   `GET /api/panels` : Retourne un tableau d'objets `Panel`.
*   `POST /api/panels` : Crée un nouveau `Panel` (envoyer l'objet JSON sans l'ID).
*   `PUT /api/panels/{panel_id}` : Met à jour un `Panel` spécifique.
*   `DELETE /api/panels/{panel_id}` : Supprime un `Panel` et ses questions associées en cascade.

### 📝 FormFields CRUD (Configuration des questions)

*   `GET /api/panels/{panel_id}/fields` : Retourne le tableau des questions pour un panel.
*   `POST /api/panels/{panel_id}/fields` : Ajoute une question (Max 5).
*   `PUT /api/fields/{field_id}` : Modifie une question.
*   `DELETE /api/fields/{field_id}` : Supprime une question.

### 🚀 Déploiement sur Discord (L'Action)

#### `POST /api/panels/{panel_id}/send?channel_id={discord_channel_id}`
*   **Description** : C'est le bouton "Déployer" de ton Frontend. Il indique au bot d'envoyer physiquement le message et le bouton dans le salon Discord sélectionné par l'utilisateur.
*   **Paramètres** : `channel_id` est obligatoire en paramètre d'URL.
*   **Réponse** : `{"success": true, "message": "Bouton envoyé dans le salon !"}`

### 🎫 Consultation et Archives (Pour le Dashboard)

*   `GET /api/tickets` : Liste tous les tickets (Supporte des filtres via Query: `?status=open&panel_id=1`).
*   `GET /api/tickets/{ticket_id}/archive` :
    *   **Description** : Retourne l'historique visuel complet d'un ticket **fermé**.
    *   **Réponse** : `{"ticket_id": 142, "html_content": "<!DOCTYPE html><html><body>...</body></html>"}`
    *   **Usage Front** : Idéal à injecter dans une `<iframe>` ou via `dangerouslySetInnerHTML` en React pour re-créer une interface chat identique à Discord sur ton interface web.
