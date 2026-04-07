```python
from flask import Flask, request, jsonify
import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# =========================
# 🔹 ROUTES (AVANT TOUT)
# =========================

@app.route("/", methods=["GET"])
def home():
    return "Agent OK"


@app.route("/test-notify", methods=["POST"])
def test_notify():
    data = request.json or {}

    # Simulation (remplace par ton vrai envoi email / WhatsApp)
    print("📩 Test notification reçue :", data)

    return jsonify({
        "status": "success",
        "message": "Notification envoyée (simulation)"
    })


@app.route("/rules", methods=["GET"])
def get_rules():
    # Exemple mock (remplace par ton vrai système)
    rules = {
        "Carrefour": "Alimentation",
        "EDF": "Électricité"
    }
    return jsonify(rules)


@app.route("/rules", methods=["POST"])
def add_rule():
    data = request.json

    if not data or "fournisseur" not in data or "categorie" not in data:
        return jsonify({"error": "Données invalides"}), 400

    fournisseur = data["fournisseur"]
    categorie = data["categorie"]

    # ⚠️ ici tu devrais écrire dans un fichier ou DB
    print(f"➕ Nouvelle règle : {fournisseur} → {categorie}")

    return jsonify({"status": "rule added"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})


# =========================
# 🔹 SCHEDULER (NON BLOQUANT)
# =========================

def scheduled_job():
    print("⏰ Job exécuté à", datetime.utcnow())


scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_job, "interval", minutes=60)


# =========================
# 🔹 START APP
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))

    scheduler.start()
    print("🚀 Server démarré sur le port", port)

    app.run(host="0.0.0.0", port=port)
```
