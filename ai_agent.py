"""
ai_agent.py — Utilise Claude pour interpréter vos réponses en langage naturel
             et mettre à jour le fichier de règles automatiquement.
             Inclut : backup, validation, logs de modifications.
"""
import json
import os
import shutil
import uuid
from datetime import datetime

import anthropic

RULES_PATH = os.path.join(os.path.dirname(__file__), "data", "rules.json")
BACKUP_PATH = os.path.join(os.path.dirname(__file__), "data", "rules.backup.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "changes.log")

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
    # 1. Validation : ne jamais écraser toutes les règles existantes
    existing = _load_rules()
    if len(existing.get("rules", [])) > 0 and len(data.get("rules", [])) == 0:
        raise ValueError("Sécurité : impossible de supprimer toutes les règles")

    # 2. Backup automatique avant chaque écriture
    if os.path.exists(RULES_PATH):
        shutil.copy2(RULES_PATH, BACKUP_PATH)

    # 3. Écriture
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _log_change(action: str, supplier: str, rule: dict, user_reply: str = "") -> None:
    """Loggue chaque modification avec contexte complet."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "supplier": supplier,
        "user_reply": user_reply[:200] if user_reply else "",
        "rule_created": rule,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── Génération de la question pour un fournisseur inconnu ────────────────────

def generate_supplier_question(label: str, amount: float, date: str) -> str:
    """Demande à Claude de formuler une question claire pour qualifier un fournisseur."""
    msg = _get_client().messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"Tu es un assistant comptable français. Une transaction bancaire inconnue vient d'apparaître :\n"
                f"- Libellé : {label}\n"
                f"- Montant : {amount}€\n"
                f"- Date : {date}\n\n"
                f"Formule une question courte en français pour demander à l'utilisateur :\n"
                f"1. Quel fournisseur c'est\n"
                f"2. Comment il paie habituellement (carte CB différée, prélèvement SEPA, virement)\n"
                f"3. Si le montant est fixe ou variable\n\n"
                f"IMPORTANT : Ne devine jamais le mode de paiement, demande toujours.\n"
                f"Sois concis (5 lignes max). Propose 3 exemples de réponses possibles."
            )
        }]
    )
    return msg.content[0].text


def generate_payment_method_question(supplier: str, invoice_ref: str, amount: float) -> str:
    """Demande le mode de règlement pour une nouvelle facture."""
    msg = _get_client().messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"Formule une question très courte en français pour demander à l'utilisateur "
                f"comment la facture {invoice_ref} de {supplier} ({amount}€) sera réglée.\n"
                f"Propose ces options : CB différée / Prélèvement SEPA / Virement / "
                f"Note de frais (COMPTE COURANT BEHAR) / Autre.\n"
                f"Maximum 3 lignes."
            )
        }]
    )
    return msg.content[0].text


def parse_user_reply(user_reply: str, supplier_label: str) -> dict:
    """
    Utilise Claude pour extraire une règle de matching depuis la réponse
    en langage naturel de l'utilisateur.
    """
    system = """Tu es un assistant qui extrait des règles de rapprochement comptable depuis du texte libre.
Tu dois retourner UNIQUEMENT un objet JSON valide, sans texte autour, avec ces champs :
{
  "match_type": "sepa" | "deferred_cb" | "insurance_schedule" | "expense_report" | "standard" | "ignore",
  "day_tolerance": <int, défaut 7>,
  "amount_tolerance_pct": <float, défaut 0>,
  "amount_tolerance_abs": <float, défaut 2.0>,
  "grouping": "monthly" | null,
  "payment_account": <string | null>,
  "notes": "<description courte>",
  "ignore": <bool, true si l'utilisateur dit d'ignorer>
}

Règles de mapping :
- "prélèvement SEPA", "SEPA", "virement automatique" → match_type="sepa", day_tolerance=16
- "CB différée", "carte différée", "regroupé en fin de mois", "SaaS", "abonnement" → match_type="deferred_cb", grouping="monthly"
- "note de frais", "compte courant", "COMPTE COURANT BEHAR", "avance" → match_type="expense_report", payment_account="COMPTE COURANT BEHAR"
- "mensuel fixe", "assurance", "échéancier" → match_type="standard"
- "ignorer", "pas une facture", "interne" → ignore=true
- Si délai spécifique mentionné → day_tolerance
- Si écart montant mentionné → amount_tolerance_abs
- NE JAMAIS deviner le mode de paiement si l'utilisateur ne l'a pas précisé"""

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
    raw = raw.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(raw)

    # Sécurité : si mode de paiement non précisé, forcer "unknown"
    if parsed.get("match_type") == "standard" and not parsed.get("payment_account"):
        if not any(kw in user_reply.lower() for kw in
                   ["sepa", "cb", "carte", "virement", "note de frais", "compte courant", "prélèvement"]):
            parsed["match_type"] = "unknown_payment"
            parsed["notes"] = (parsed.get("notes", "") +
                               " — mode de paiement à confirmer").strip(" —")

    return parsed


def generate_confirmation(supplier_label: str, parsed_rule: dict) -> str:
    """Génère un message de confirmation clair avant de sauvegarder la règle."""
    mtype = parsed_rule.get("match_type", "standard")
    day_tol = parsed_rule.get("day_tolerance", 7)
    notes = parsed_rule.get("notes", "")
    account = parsed_rule.get("payment_account", "")

    type_labels = {
        "sepa": "Prélèvement SEPA",
        "deferred_cb": "CB différée (groupement mensuel)",
        "insurance_schedule": "Assurance/échéancier",
        "expense_report": "Note de frais",
        "standard": "Standard",
        "ignore": "À ignorer",
        "unknown_payment": "⚠️ Mode de paiement non précisé",
    }
    label = type_labels.get(mtype, mtype)
    account_line = f"\n• Compte : {account}" if account else ""
    return (
        f"✅ Compris ! Voici la règle pour *{supplier_label}* :\n"
        f"• Type : {label}\n"
        f"• Tolérance délai : {day_tol} jours\n"
        f"• Notes : {notes}{account_line}\n\n"
        f"Cette règle s'applique dès maintenant."
    )


# ─── Sauvegarde d'une nouvelle règle ─────────────────────────────────────────

def save_new_rule(supplier_label: str, parsed_rule: dict, user_reply: str = "") -> bool:
    """Insère la règle dans rules.json avec backup + log."""
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
        "payment_account": parsed_rule.get("payment_account"),
        "notes": parsed_rule.get("notes", "Règle créée automatiquement"),
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "created_by": "agent_ai",
    }

    data["rules"].append(new_rule)
    _save_rules(data)
    _log_change("rule_created", supplier_label, new_rule, user_reply)
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


def get_pending_by_token(token: str) -> tuple:
    """Retrouve un fournisseur en attente par son token."""
    try:
        data = _load_rules()
        for label, info in data.get("pending_confirmations", {}).items():
            if info.get("token") == token:
                return label, info
    except Exception:
        pass
    return None, None


def restore_backup() -> bool:
    """Restaure rules.json depuis le backup en cas de problème."""
    if os.path.exists(BACKUP_PATH):
        shutil.copy2(BACKUP_PATH, RULES_PATH)
        _log_change("backup_restored", "system", {}, "Restauration manuelle")
        print("[AGENT] rules.json restauré depuis le backup")
        return True
    print("[AGENT] Aucun backup disponible")
    return False
