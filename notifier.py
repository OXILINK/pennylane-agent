"""
notifier.py — Envoi email (Resend) + WhatsApp (Twilio)
"""
import os
import requests
from twilio.rest import Client as TwilioClient


def _resend_send(subject: str, html: str) -> bool:
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                 "Content-Type": "application/json"},
        json={
            "from": os.environ.get("FROM_EMAIL", "agent@oxilink.fr"),
            "to": [os.environ["NOTIFY_EMAIL"]],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    return resp.status_code in (200, 201)


def _whatsapp_send(body: str) -> bool:
    try:
        client = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        client.messages.create(
            from_=os.environ["TWILIO_WHATSAPP_FROM"],
            to=os.environ["NOTIFY_WHATSAPP"],
            body=body,
        )
        return True
    except Exception as e:
        print(f"[WARN] WhatsApp échoué: {e}")
        return False


def notify(subject: str, html_body: str, whatsapp_text: str) -> None:
    """Envoie email + WhatsApp."""
    email_ok = _resend_send(subject, html_body)
    wa_ok = _whatsapp_send(whatsapp_text)
    print(f"[NOTIFY] Email: {'✓' if email_ok else '✗'} | WhatsApp: {'✓' if wa_ok else '✗'} | {subject}")


# ─── Templates de messages ────────────────────────────────────────────────────

def msg_new_supplier(label: str, amount: float, date: str, token: str) -> tuple[str, str, str]:
    subject = f"🔍 Nouveau fournisseur à qualifier : {label}"
    html = f"""
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#7c3aed">Nouveau fournisseur détecté</h2>
  <p>J'ai vu une transaction que je ne sais pas rapprocher :</p>
  <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:6px;font-weight:bold">Libellé banque</td><td style="padding:6px">{label}</td></tr>
    <tr style="background:#f5f3ff"><td style="padding:6px;font-weight:bold">Montant</td><td style="padding:6px">{amount}€</td></tr>
    <tr><td style="padding:6px;font-weight:bold">Date</td><td style="padding:6px">{date}</td></tr>
  </table>
  <p style="margin-top:20px"><strong>Comment dois-je traiter ce fournisseur ?</strong></p>
  <p style="color:#666;font-size:14px">Répondez simplement à cet email en décrivant la règle :<br>
  <em>Exemple : "Abonnement SaaS mensuel, CB différée, environ 50€/mois"<br>
  ou : "Prélèvement SEPA, peut arriver jusqu'à 16 jours après la facture"<br>
  ou : "Ignorer, ce n'est pas une facture fournisseur"</em></p>
  <p style="color:#999;font-size:12px">Token de référence : <code>{token}</code></p>
</div>
"""
    whatsapp = (
        f"🔍 *Nouveau fournisseur* : {label}\n"
        f"Montant : {amount}€ | Date : {date}\n\n"
        f"Comment dois-je le rapprocher ?\n"
        f"Répondez ici ou par email.\n_(ref: {token})_"
    )
    return subject, html, whatsapp


def msg_low_confidence(label: str, amount: float, invoice_ref: str,
                       confidence: int, reason: str, token: str) -> tuple[str, str, str]:
    subject = f"⚠️ Validation requise ({confidence}%) : {label}"
    html = f"""
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#d97706">Rapprochement incertain</h2>
  <p>J'ai trouvé un rapprochement possible mais ma confiance est de <strong>{confidence}%</strong> 
  (seuil : {os.environ.get('CONFIDENCE_THRESHOLD','85')}%).</p>
  <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:6px;font-weight:bold">Transaction</td><td style="padding:6px">{label} — {amount}€</td></tr>
    <tr style="background:#fffbeb"><td style="padding:6px;font-weight:bold">Facture</td><td style="padding:6px">{invoice_ref}</td></tr>
    <tr><td style="padding:6px;font-weight:bold">Raison</td><td style="padding:6px">{reason}</td></tr>
  </table>
  <p style="margin-top:20px">Répondez <strong>OUI</strong> pour confirmer ou <strong>NON</strong> pour rejeter.<br>
  <span style="color:#999;font-size:12px">_(ref: {token})_</span></p>
</div>
"""
    whatsapp = (
        f"⚠️ *Rapprochement incertain* ({confidence}%)\n"
        f"Transaction : {label} ({amount}€)\n"
        f"Facture : {invoice_ref}\n"
        f"Raison : {reason}\n\n"
        f"Répondez OUI ou NON _(ref: {token})_"
    )
    return subject, html, whatsapp


def msg_missing_invoices(month: str) -> tuple[str, str, str]:
    subject = f"📋 Aucune facture client détectée — {month}"
    html = f"""
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#dc2626">Facturation manquante</h2>
  <p>Nous sommes le 6 du mois et je n'ai trouvé <strong>aucune facture client</strong> 
  pour <strong>{month}</strong>.</p>
  <p>Pensez à créer vos factures clients dans Pennylane si ce n'est pas encore fait.</p>
</div>
"""
    whatsapp = (
        f"📋 *Alerte facturation* : aucune facture client pour {month}.\n"
        f"Pensez à facturer vos clients dans Pennylane !"
    )
    return subject, html, whatsapp


def msg_sepa_overdue(supplier: str, days: int, amount: float) -> tuple[str, str, str]:
    subject = f"⏰ Prélèvement SEPA en retard : {supplier} ({days}j)"
    html = f"""
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#dc2626">SEPA en retard</h2>
  <p>Le prélèvement SEPA de <strong>{supplier}</strong> est attendu depuis <strong>{days} jours</strong> 
  ({amount}€) — au-delà de la tolérance de 17 jours.</p>
  <p>Vérifiez si le prélèvement a bien été effectué ou si la facture est en suspens.</p>
</div>
"""
    whatsapp = (
        f"⏰ *SEPA retard* : {supplier}\n"
        f"Attendu depuis {days} jours | {amount}€\n"
        f"Vérifiez dans Pennylane."
    )
    return subject, html, whatsapp


def msg_daily_summary(matched: int, low_conf: int, unknown: int) -> tuple[str, str, str]:
    subject = f"✅ Bilan rapprochement : {matched} OK, {low_conf} à valider, {unknown} inconnus"
    html = f"""
<div style="font-family:sans-serif;max-width:600px;padding:20px">
  <h2 style="color:#059669">Bilan quotidien</h2>
  <table style="border-collapse:collapse;width:100%">
    <tr style="background:#ecfdf5"><td style="padding:8px">✅ Rapprochés automatiquement</td>
        <td style="padding:8px;font-weight:bold">{matched}</td></tr>
    <tr><td style="padding:8px">⚠️ À valider manuellement</td>
        <td style="padding:8px;font-weight:bold">{low_conf}</td></tr>
    <tr style="background:#fef3c7"><td style="padding:8px">🔍 Fournisseurs inconnus</td>
        <td style="padding:8px;font-weight:bold">{unknown}</td></tr>
  </table>
</div>
"""
    whatsapp = (
        f"✅ *Bilan rapprochement*\n"
        f"Rapprochés auto : {matched}\n"
        f"À valider : {low_conf}\n"
        f"Fournisseurs inconnus : {unknown}"
    )
    return subject, html, whatsapp
