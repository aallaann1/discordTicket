# 📚 Documentation API Complète : Discord Ticketing Bot

Cette documentation décrit exhaustivement l'API REST permettant à ton Frontend de gérer la configuration du bot Discord, de consulter les statistiques et d'afficher les archives de tickets.

---

## 🔐 1. Sécurité et Authentification

L'API est strictement privée. Toute requête doit comporter l'en-tête suivant :
```json
{
  "X-API-Key": "ta_cle_secrete_definie_dans_le_env"
}
```
*Toutes les routes retournent une erreur `403 Forbidden` si cet en-tête est manquant ou invalide.*

---

## 🌐 2. Documentation Interactive (Swagger)

FastAPI génère automatiquement une documentation interactive que ton développeur Front peut utiliser pour tester l'API en direct.
Une fois l'application lancée, rends-toi sur : **`http://localhost:8000/docs`**

---

## 📋 3. Endpoints (Routes API)

L'URL de base pour toutes les requêtes est : `http://<IP_DU_SERVEUR>:8000/api`

### 📊 Statistiques

#### `GET /stats`
Récupère les statistiques globales pour ton tableau de bord.
* **Réponse attendue (200 OK)** :
  ```json
  {
    "total_tickets": 152,
    "open_tickets": 12,
    "closed_tickets": 140
  }
  ```

---

### 🏷️ Gestion des Panels (Les boutons sur Discord)

Les "Panels" sont les configurations des boutons d'ouverture de tickets (ex: Panel "Support", Panel "Plainte").

#### `GET /panels`
Liste tous les panels configurés.
* **Réponse attendue (200 OK)** :
  ```json
  [
    {
      "id": 1,
      "name": "Support Technique",
      "button_text": "Ouvrir un ticket",
      "active_category_id": 123456789,
      "archive_category_id": 987654321,
      "ping_role_id": 456789123,
      "intro_message": "Bonjour, décrivez votre problème :",
      "inactivity_days": 3
    }
  ]
  ```

#### `POST /panels`
Créer une nouvelle configuration de panel.
* **Payload JSON attendu** : (Le même format que le GET au-dessus, sans l'`id`).
* **Réponse (200 OK)** : L'objet Panel créé avec son nouvel `id`.

#### `PUT /panels/{panel_id}`
Modifier un panel existant (changer le texte du bouton, la catégorie, etc.).
* **Payload JSON attendu** : Champs à mettre à jour.

#### `DELETE /panels/{panel_id}`
Supprimer un panel.

#### `POST /panels/{panel_id}/send?channel_id={discord_channel_id}` 🚀 **[ROUTE D'ACTION DISCORD]**
Demande au Bot Discord de poster le bouton d'ouverture de ce panel dans le salon Discord ciblé. C'est comme ça que tu déploies le système depuis ton Frontend.
* **Query Params** : `channel_id` (L'ID du salon textuel sur Discord).
* **Réponse attendue (200 OK)** : `{"success": true, "message": "Bouton envoyé avec succès."}`

---

### 📝 Gestion du Formulaire (Modale Discord)

Ce sont les questions qui s'affichent quand l'utilisateur clique sur le bouton d'un Panel. *(Rappel : Discord limite à 5 questions maximum par formulaire).*

#### `GET /panels/{panel_id}/fields`
Récupérer les questions configurées pour un panel spécifique.
* **Réponse attendue (200 OK)** :
  ```json
  [
    {
      "id": 1,
      "panel_id": 1,
      "label": "Quel est votre nom en jeu ?",
      "is_paragraph": false,
      "required": true,
      "order": 1
    },
    {
      "id": 2,
      "panel_id": 1,
      "label": "Décrivez le bug rencontré :",
      "is_paragraph": true,
      "required": true,
      "order": 2
    }
  ]
  ```

#### `POST /panels/{panel_id}/fields`
Ajouter une question au formulaire d'un panel.
* **Payload JSON attendu** :
  ```json
  {
    "label": "Votre question",
    "is_paragraph": true,
    "required": true,
    "order": 3
  }
  ```

#### `PUT /fields/{field_id}`
Modifier une question existante.

#### `DELETE /fields/{field_id}`
Supprimer une question.

---

### 🎫 Consultation des Tickets et Archives

#### `GET /tickets`
Lister les tickets. Idéal pour un tableau de bord des tickets en cours.
* **Query Params (Optionnels)** : `status=open`, `panel_id=1`, `creator_id=123456789`
* **Réponse attendue (200 OK)** :
  ```json
  [
    {
      "id": 42,
      "panel_id": 1,
      "channel_id": 1122334455,
      "creator_id": 9988776655,
      "status": "open",
      "created_at": "2024-01-01T12:00:00",
      "updated_at": "2024-01-01T12:05:00"
    }
  ]
  ```

#### `GET /tickets/{ticket_id}/archive`
Récupérer le transcript d'un ticket fermé pour l'afficher visuellement sur le Frontend.
* **Réponse attendue (200 OK)** :
  ```json
  {
    "ticket_id": 42,
    "html_content": "<!DOCTYPE html><html><body>...<div class=\"discord-messages\">...</body></html>"
  }
  ```
*(Le Frontend pourra utiliser ce `html_content` dans une `<iframe>` ou l'injecter dynamiquement via un shadow DOM ou `dangerouslySetInnerHTML` pour afficher la conversation exactement comme sur Discord).*
