"""
main.py — Orchestrateur principal
Tâches planifiées :
  - Quotidien 08h00 : matching auto + alertes SEPA
  - J6 du mois     : alerte facturation client manquante
  - J10 du mois    : traitement CB différée
  - Webhook HTTP   : réception des réponses email/WhatsApp (via Railway)
"""
import json
import logging
import os
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from flask import Flask, request, jsonify

import ai_agent
import matching_engine
import notifier
import pennylane_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─── Tâche quotidienne ────────────────────────────────────────────────────────

def job_daily_matching():
    log.info("=== Démarrage matching quotidien ===")
    try:
        transactions = pennylane_client.get_transactions(days_back=45)
        invoices = pennylane_client.get_supplier_invoices(days_back=45)
        log.info(f"Récupéré : {len(transactions)} transactions, {len(invoices)} factures")

        results = matching_engine.run_matching(transactions, invoices)

        threshold = int(os.getenv("CONFIDENCE_THRESHOLD", "85"))
        auto_matched = [r for r in results if r.matched and r.confidence >= threshold]
        low_conf = [r for r in results if r.matched and 0 < r.confidence < threshold]

        # Appliquer les matchings automatiques dans Pennylane
        applied = 0
        for r in auto_matched:
            if r.invoice_id and r.transaction_id and r.rule_id != "ignore":
                ok = pennylane_client.match_transaction_to_invoice(r.invoice_id, r.transaction_id)
                if ok:
                    applied += 1
                    log.info(f"✓ Match appliqué : inv={r.invoice_id} ↔ tx={r.transaction_id} ({r.confidence}%) — {r.reason}")

        # Notifier les rapprochements à faible confiance
        for r in low_conf:
            inv_ref = f"#{r.invoice_id}"
            tx_label = next((t.get("label","") for t in transactions if t.get("id") == r.transaction_id), "")
            tx_amount = next((t.get("amount", 0) for t in transactions if t.get("id") == r.transaction_id), 0)
            token = ai_agent.add_pending_confirmation(tx_label, {"transaction_id": r.transaction_id})
            subj, html, wa = notifier.msg_low_confidence(
                tx_label, tx_amount, inv_ref, r.confidence, r.reason, token
            )
            notifier.notify(subj, html, wa)

        # Détecter nouveaux fournisseurs
        unknown = matching_engine.find_unknown_suppliers(transactions)
        for u in unknown:
            label = u["label"]
            amount = u["amount"]
            date = u["date"]
            log.info(f"Nouveau fournisseur détecté : {label}")
            token = ai_agent.add_pending_confirmation(label, u)
            question_text = ai_agent.generate_supplier_question(label, amount, date)
            subj, html, wa = notifier.msg_new_supplier(label, amount, date, token)
            # Enrichir l'email avec la question de Claude
            html = html.replace(
                "<p style=\"color:#666",
                f"<p style=\"color:#333;margin-bottom:12px\">{question_text}</p><p style=\"color:#666"
            )
            notifier.notify(subj, html, wa)

        # Alertes SEPA en retard
        _check_sepa_overdue(invoices)

        # Bilan
        subj, html, wa = notifier.msg_daily_summary(applied, len(low_conf), len(unknown))
        if applied > 0 or low_conf or unknown:
            notifier.notify(subj, html, wa)

        log.info(f"=== Matching terminé : {applied} appliqués, {len(low_conf)} à valider, {len(unknown)} inconnus ===")

    except Exception as e:
        log.exception(f"Erreur job_daily_matching : {e}")


def _check_sepa_overdue(invoices: list):
    """Alerte si un prélèvement SEPA attendu dépasse 17 jours."""
    rules_path = os.path.join(os.path.dirname(__file__), "data", "rules.json")
    with open(rules_path) as f:
        rules_data = json.load(f)
    sepa_rules = [r for r in rules_data["rules"] if r["match_type"] == "sepa"]

    today = datetime.now()
    for inv in invoices:
        inv_date_str = inv.get("date") or inv.get("deadline")
        if not inv_date_str:
            continue
        try:
            inv_date = datetime.strptime(inv_date_str[:10], "%Y-%m-%d")
        except ValueError:
            continue
        days_elapsed = (today - inv_date).days
        if days_elapsed <= 17:
            continue
        supplier = (inv.get("supplier") or {}).get("name", "")
        for rule in sepa_rules:
            if matching_engine._label_similarity(rule["supplier_pattern"], supplier) >= 0.6:
                amount = float(inv.get("currency_amount", 0))
                subj, html, wa = notifier.msg_sepa_overdue(supplier, days_elapsed, amount)
                notifier.notify(subj, html, wa)
                break


# ─── Tâche J6 : alerte facturation manquante ─────────────────────────────────

def job_check_client_invoices():
    log.info("=== Vérification facturation client (J6) ===")
    try:
        now = datetime.now()
        invoices = pennylane_client.get_customer_invoices(month=now.month, year=now.year)
        month_label = now.strftime("%B %Y")
        if not invoices:
            subj, html, wa = notifier.msg_missing_invoices(month_label)
            notifier.notify(subj, html, wa)
            log.info(f"Alerte facturation envoyée pour {month_label}")
        else:
            log.info(f"{len(invoices)} facture(s) client trouvée(s) pour {month_label} — OK")
    except Exception as e:
        log.exception(f"Erreur job_check_client_invoices : {e}")


# ─── Tâche J10 : CB différée ──────────────────────────────────────────────────

def job_cb_differee():
    log.info("=== Traitement CB différée (J10) ===")
    try:
        transactions = pennylane_client.get_transactions(days_back=45)
        invoices = pennylane_client.get_supplier_invoices(days_back=45)
        results = matching_engine.run_matching(transactions, invoices)
        cb_results = [r for r in results if r.rule_id and "cb" in r.rule_id.lower()]
        applied = 0
        for r in cb_results:
            if r.confidence >= int(os.getenv("CONFIDENCE_THRESHOLD", "85")):
                ok = pennylane_client.match_transaction_to_invoice(r.invoice_id, r.transaction_id)
                if ok:
                    applied += 1
        log.info(f"CB différée : {applied} rapprochements appliqués")
    except Exception as e:
        log.exception(f"Erreur job_cb_differee : {e}")


# ─── Webhook : réception des réponses utilisateur ────────────────────────────

@app.route("/webhook/reply", methods=["POST"])
def webhook_reply():
    """
    Reçoit la réponse de l'utilisateur (email parsé ou WhatsApp).
    Attendu : { "token": "abc123", "reply": "Prélèvement SEPA, ±16 jours" }
    """
    data = request.get_json(force=True)
    token = data.get("token", "").strip()
    user_reply = data.get("reply", "").strip()

    if not token or not user_reply:
        return jsonify({"error": "token et reply requis"}), 400

    supplier_label, pending_info = ai_agent.get_pending_by_token(token)
    if not supplier_label:
        return jsonify({"error": "token inconnu ou déjà traité"}), 404

    log.info(f"Réponse reçue pour {supplier_label} : {user_reply[:80]}")

    try:
        parsed = ai_agent.parse_user_reply(user_reply, supplier_label)
        confirmation_msg = ai_agent.generate_confirmation(supplier_label, parsed)
        ai_agent.save_new_rule(supplier_label, parsed)

        # Envoyer la confirmation
        subject = f"✅ Règle enregistrée : {supplier_label}"
        html = f"""
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#059669">Règle enregistrée</h2>
  <pre style="background:#f0fdf4;padding:12px;border-radius:6px;white-space:pre-wrap">{confirmation_msg}</pre>
</div>
"""
        notifier.notify(subject, html, confirmation_msg)
        return jsonify({"status": "ok", "confirmation": confirmation_msg})

    except json.JSONDecodeError as e:
        log.error(f"Claude n'a pas retourné du JSON valide : {e}")
        return jsonify({"error": "parsing IA échoué"}), 500
    except Exception as e:
        log.exception(f"Erreur webhook_reply : {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/webhook/twilio", methods=["POST"])
def webhook_twilio():
    """
    Webhook Twilio WhatsApp — reçoit les réponses WhatsApp.
    Format attendu : Body contient "TOKEN:xxxxx RÉPONSE..."
    """
    body = request.form.get("Body", "")
    log.info(f"WhatsApp reçu : {body[:100]}")

    # Parser le token et la réponse
    token = None
    reply = body
    if body.startswith("TOKEN:") or "ref:" in body.lower():
        import re
        m = re.search(r'[Rr]ef[:\s]+([a-f0-9]{8})', body)
        if m:
            token = m.group(1)
            reply = re.sub(r'\(ref:?\s*[a-f0-9]{8}\)', '', body).strip()

    if not token:
        # Chercher le dernier pending et l'associer
        rules_path = os.path.join(os.path.dirname(__file__), "data", "rules.json")
        with open(rules_path) as f:
            rules_data = json.load(f)
        pending = rules_data.get("pending_confirmations", {})
        if pending:
            last_label = list(pending.keys())[-1]
            token = pending[last_label].get("token")

    if token:
        import requests as req
        req.post("http://localhost:5000/webhook/reply",
                 json={"token": token, "reply": reply}, timeout=10)

    # Twilio attend du TwiML
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/run-now", methods=["POST"])
def run_now():
    """Déclenche le matching manuellement (pour tests)."""
    job_daily_matching()
    return jsonify({"status": "done"})


# ─── Lancement ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("🚀 Agent Pennylane démarré")

    scheduler = BlockingScheduler(timezone="Europe/Paris")

    # Quotidien à 08h00
    scheduler.add_job(job_daily_matching, CronTrigger(hour=8, minute=0),
                      id="daily_matching", name="Matching quotidien")

    # J6 du mois à 09h00
    scheduler.add_job(job_check_client_invoices, CronTrigger(day=6, hour=9, minute=0),
                      id="client_invoices", name="Alerte facturation J6")

    # J10 du mois à 10h00
    scheduler.add_job(job_cb_differee, CronTrigger(day=10, hour=10, minute=0),
                      id="cb_differee", name="CB différée J10")

    # Lancer Flask dans un thread séparé
    import threading
    port = int(os.environ.get("PORT", 5000))
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    log.info(f"Webhook disponible sur http://0.0.0.0:{port}/webhook/reply")

    # Lancer le scheduler (bloquant)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent arrêté.")


@app.route("/test-notify", methods=["POST"])
def test_notify():
    """Teste l'envoi email + WhatsApp sans avoir besoin de transactions."""
    import ai_agent as _ai
    subj = "🧪 Test agent Pennylane"
    html = """
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#7c3aed">Agent Pennylane opérationnel ✅</h2>
  <p>Votre agent tourne correctement sur Railway.</p>
  <p>Il rapprochera automatiquement vos factures chaque matin à 08h00.</p>
</div>
"""
    wa = "✅ *Agent Pennylane opérationnel*\nVotre agent tourne sur Railway et vous contactera dès qu'il aura besoin de vous."
    notifier.notify(subj, html, wa)
    return jsonify({"status": "ok", "message": "Email + WhatsApp envoyés"})


@app.route("/restore-backup", methods=["POST"])
def restore_backup():
    """Restaure rules.json depuis le backup en cas de problème."""
    ok = ai_agent.restore_backup()
    return jsonify({"status": "ok" if ok else "no_backup"})


@app.route("/rules", methods=["GET"])
def get_rules():
    """Affiche les règles actuelles (lecture seule)."""
    import json as _json
    rules_path = os.path.join(os.path.dirname(__file__), "data", "rules.json")
    with open(rules_path) as f:
        return _json.load(f)
