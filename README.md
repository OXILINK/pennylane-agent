# Agent Pennylane — Guide de déploiement Railway

## Ce que fait cet agent

- **Tous les jours à 08h** : récupère transactions + factures Pennylane, rapproche automatiquement, vous alerte sur les cas incertains
- **J6 du mois** : alerte si aucune facture client n'a été créée
- **J10 du mois** : traitement des CB différées (PENNYLANE, YOUSIGN, Canva...)
- **Nouveaux fournisseurs** : vous pose la question par email + WhatsApp, enregistre votre réponse comme règle permanente

---

## Déploiement en 4 étapes

### Étape 1 — Créer un compte Railway (gratuit)

1. Allez sur https://railway.app
2. Créez un compte (GitHub recommandé)
3. Cliquez "New Project" → "Deploy from GitHub repo"

### Étape 2 — Pousser le code sur GitHub

```bash
# Dans le dossier pennylane-agent/
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/VOTRE_USER/pennylane-agent.git
git push -u origin main
```

### Étape 3 — Configurer les variables d'environnement sur Railway

Dans Railway > votre projet > Variables, ajoutez :

| Variable | Valeur |
|----------|--------|
| `PENNYLANE_TOKEN` | Votre token API Pennylane (Connectivité > API) |
| `PENNYLANE_COMPANY_ID` | Votre ID entreprise Pennylane |
| `ANTHROPIC_API_KEY` | Votre clé Anthropic |
| `RESEND_API_KEY` | Votre clé Resend |
| `FROM_EMAIL` | agent@votredomaine.fr (domaine vérifié dans Resend) |
| `NOTIFY_EMAIL` | jc.behar@oxilink.fr |
| `TWILIO_ACCOUNT_SID` | Votre SID Twilio |
| `TWILIO_AUTH_TOKEN` | Votre token Twilio |
| `TWILIO_WHATSAPP_FROM` | whatsapp:+14155238886 (sandbox) |
| `NOTIFY_WHATSAPP` | whatsapp:+33XXXXXXXXX (votre numéro) |
| `CONFIDENCE_THRESHOLD` | 85 |

### Étape 4 — Configurer le webhook Twilio pour WhatsApp

1. Dans Twilio Console > Messaging > Try it out > Send a WhatsApp message
2. Dans "Sandbox Configuration", renseignez :
   - **When a message comes in** : `https://VOTRE-APP.railway.app/webhook/twilio`
   - Méthode : POST

---

## Comment répondre à l'agent

### Par email
Répondez simplement au mail reçu. L'agent lit votre réponse via le webhook `/webhook/reply`.

Pour envoyer manuellement une réponse :
```bash
curl -X POST https://VOTRE-APP.railway.app/webhook/reply \
  -H "Content-Type: application/json" \
  -d '{"token": "TOKEN_DU_MAIL", "reply": "Prélèvement SEPA, tolérance 16 jours"}'
```

### Par WhatsApp
Répondez au message WhatsApp reçu. Incluez la référence `(ref: TOKEN)` si possible.

---

## Ajouter/modifier une règle manuellement

Éditez `data/rules.json` et ajoutez un objet dans le tableau `rules` :

```json
{
  "id": "mon_fournisseur",
  "supplier_pattern": "MON FOURNISSEUR",
  "match_type": "sepa",
  "day_tolerance": 10,
  "amount_tolerance_pct": 0,
  "notes": "Prélèvement mensuel",
  "created_at": "2025-04-05",
  "created_by": "manuel"
}
```

Types disponibles : `sepa`, `deferred_cb`, `insurance_schedule`, `standard`, `ignore`

---

## Déclencher un matching manuel

```bash
curl -X POST https://VOTRE-APP.railway.app/run-now
```

---

## Vérifier que tout fonctionne

```bash
curl https://VOTRE-APP.railway.app/health
# → {"status": "ok", "time": "2025-04-05T08:00:00"}
```

---

## Coûts estimés

| Service | Coût mensuel |
|---------|-------------|
| Railway.app (Hobby) | 5€ |
| Anthropic Claude API (~50 tx/mois) | ~2-3€ |
| Twilio WhatsApp (~20 messages) | ~1€ |
| Resend email (< 100/mois) | 0€ |
| **Total** | **~8-9€/mois** |
