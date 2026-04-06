"""
ai_agent.py — Utilise Claude pour interpréter vos réponses en langage naturel
             et mettre à jour le fichier de règles automatiquement.
"""
import json
import os
import uuid
from datetime import datetime

import anthropic

RULES_PATH = os.path.join(os.path.dirname(__file__), "data", "rules.json")

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _load_rules() -> dict:
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_rules(data: dict) -> None:
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Génération de la question pour un fournisseur inconnu ────────────────────

def generate_supplier_question(label: str, amount: float, date: str) -> str:
    """Demande à Claude de formuler une question claire pour qualifier un fournisseur."""
    msg = _get_client().messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                f"Tu es un assistant comptable. Une transaction bancaire inconnue vient d'apparaître :\n"
                f"- Libellé : {label}\n"
                f"- Montant : {amount}€\n"
                f"- Date : {date}\n\n"
                f"Formule une question courte et claire en français pour demander à l'utilisateur "
                f"comment cette transaction doit être rapprochée. "
                f"Propose 3 exemples de réponses possibles (SEPA, CB différée, abonnement, à ignorer...). "
                f"Sois concis (5 lignes max)."
            )
        }]
    )
    return msg.content[0].text


def parse_user_reply(user_reply: str, supplier_label: str) -> dict:
    """
    Utilise Claude pour extraire une règle de matching depuis la réponse
    en langage naturel de l'utilisateur.
    Retourne un dict prêt à insérer dans rules.json.
    """
    system = """Tu es un assistant qui extrait des règles de rapprochement comptable depuis du texte libre.
Tu dois retourner UNIQUEMENT un objet JSON valide, sans texte autour, avec ces champs :
{
  "match_type": "sepa" | "deferred_cb" | "insurance_schedule" | "standard" | "ignore",
  "day_tolerance": <int, défaut 7>,
  "amount_tolerance_pct": <float, défaut 0>,
  "amount_tolerance_abs": <float, défaut 2.0>,
  "grouping": "monthly" | null,
  "notes": "<description courte>",
  "ignore": <bool, true si l'utilisateur dit d'ignorer>
}

Règles de mapping :
- "prélèvement SEPA", "SEPA", "virement automatique" → match_type="sepa", day_tolerance=16
- "CB différée", "carte différée", "regroupé en fin de mois", "SaaS", "abonnement" → match_type="deferred_cb", grouping="monthly"
- "mensuel fixe", "assurance", "échéancier" → match_type="standard"
- "ignorer", "pas une facture", "interne" → ignore=true
- Si l'utilisateur mentionne un délai spécifique (ex: "jusqu'à 20 jours"), utilise-le pour day_tolerance
- Si l'utilisateur mentionne un écart possible (ex: "±5€"), utilise-le pour amount_tolerance_abs"""

    msg = _get_client().messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Fournisseur : {supplier_label}\n"
                f"Réponse de l'utilisateur : {user_reply}"
            )
        }]
    )

    raw = msg.content[0].text.strip()
    # Nettoyer si Claude a ajouté des backticks
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def generate_confirmation(supplier_label: str, parsed_rule: dict) -> str:
    """Génère un message de confirmation clair avant de sauvegarder la règle."""
    mtype = parsed_rule.get("match_type", "standard")
    day_tol = parsed_rule.get("day_tolerance", 7)
    notes = parsed_rule.get("notes", "")

    type_labels = {
        "sepa": "Prélèvement SEPA",
        "deferred_cb": "CB différée (groupement mensuel)",
        "insurance_schedule": "Assurance/échéancier",
        "standard": "Standard",
        "ignore": "À ignorer",
    }
    label = type_labels.get(mtype, mtype)
    return (
        f"✅ Compris ! Voici la règle que je vais enregistrer pour *{supplier_label}* :\n"
        f"• Type : {label}\n"
        f"• Tolérance délai : {day_tol} jours\n"
        f"• Notes : {notes}\n\n"
        f"Cette règle s'appliquera automatiquement dès maintenant."
    )


# ─── Sauvegarde d'une nouvelle règle ─────────────────────────────────────────

def save_new_rule(supplier_label: str, parsed_rule: dict) -> bool:
    """Insère la règle dans rules.json. Retourne False si ignore=True."""
    if parsed_rule.get("ignore"):
        print(f"[AGENT] Fournisseur ignoré : {supplier_label}")
        # On l'ajoute quand même avec type ignore pour ne plus poser la question
        parsed_rule["match_type"] = "ignore"

    data = _load_rules()

    # Supprimer de pending si présent
    pending = data.get("pending_confirmations", {})
    pending.pop(supplier_label.upper(), None)
    data["pending_confirmations"] = pending

    # Éviter doublons
    existing_ids = {r["id"] for r in data["rules"]}
    rule_id = f"dynamic_{supplier_label.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}"
    while rule_id in existing_ids:
        rule_id = f"dynamic_{supplier_label.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}"

    new_rule = {
        "id": rule_id,
        "supplier_pattern": supplier_label.upper(),
        "match_type": parsed_rule.get("match_type", "standard"),
        "day_tolerance": parsed_rule.get("day_tolerance", 7),
        "amount_tolerance_pct": parsed_rule.get("amount_tolerance_pct", 0),
        "amount_tolerance_abs": parsed_rule.get("amount_tolerance_abs", 2.0),
        "grouping": parsed_rule.get("grouping"),
        "notes": parsed_rule.get("notes", "Règle créée automatiquement"),
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "created_by": "agent_ai",
    }

    data["rules"].append(new_rule)
    _save_rules(data)
    print(f"[AGENT] Nouvelle règle sauvegardée : {rule_id} pour {supplier_label}")
    return parsed_rule.get("match_type") != "ignore"


def add_pending_confirmation(supplier_label: str, tx_info: dict) -> str:
    """Marque un fournisseur comme 'en attente de réponse' et retourne le token."""
    data = _load_rules()
    token = uuid.uuid4().hex[:8]
    data.setdefault("pending_confirmations", {})[supplier_label.upper()] = {
        "token": token,
        "tx_info": tx_info,
        "asked_at": datetime.now().isoformat(),
    }
    _save_rules(data)
    return token


def get_pending_by_token(token: str) -> tuple[str, dict] | tuple[None, None]:
    """Retrouve un fournisseur en attente par son token."""
    data = _load_rules()
    for label, info in data.get("pending_confirmations", {}).items():
        if info.get("token") == token:
            return label, info
    return None, None
