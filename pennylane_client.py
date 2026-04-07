"""
pennylane_client.py — Wrapper API Pennylane V2
"""
import os
import requests
from datetime import datetime, timedelta
from typing import Optional

BASE_URL = "https://app.pennylane.com/api/external/v2"

def _headers():
    return {
        "Authorization": f"Bearer {os.environ['PENNYLANE_TOKEN']}",
        "Content-Type": "application/json",
    }

def get_transactions(days_back: int = 60) -> list[dict]:
    """Récupère les transactions bancaires des N derniers jours."""
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    results = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/bank_transactions",
            headers=_headers(),
            params={"filter[min_date]": since, "page": page, "per_page": 100},
            timeout=30,
        )
        if resp.status_code in (404, 400):
            print(f"[INFO] Transactions bancaires : {resp.status_code} — liste vide retournée")
            break
        if resp.status_code == 401:
            print("[ERROR] Token Pennylane invalide ou expiré")
            break
        resp.raise_for_status()
        data = resp.json()
        items = data.get("bank_transactions", data.get("data", []))
        if not items:
            break
        results.extend(items)
        if len(items) < 100:
            break
        page += 1
    return results

def get_supplier_invoices(days_back: int = 60) -> list[dict]:
    """Récupère les factures fournisseurs des N derniers jours."""
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    results = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/supplier_invoices",
            headers=_headers(),
            params={
                "filter[min_date]": since,
                "page": page,
                "per_page": 100,
            },
            timeout=30,
        )
        if resp.status_code in (404, 400):
            print(f"[INFO] Factures fournisseurs : {resp.status_code} — liste vide retournée")
            break
        if resp.status_code == 401:
            print("[ERROR] Token Pennylane invalide ou expiré")
            break
        resp.raise_for_status()
        data = resp.json()
        items = data.get("supplier_invoices", data.get("data", []))
        if not items:
            break
        results.extend(items)
        if len(items) < 100:
            break
        page += 1
    return results

def get_customer_invoices(month: Optional[int] = None, year: Optional[int] = None) -> list[dict]:
    """Récupère les factures clients d'un mois donné."""
    now = datetime.now()
    m = month or now.month
    y = year or now.year
    first_day = f"{y}-{m:02d}-01"
    last_day = (datetime(y, m, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    resp = requests.get(
        f"{BASE_URL}/customer_invoices",
        headers=_headers(),
        params={
            "filter[min_date]": first_day,
            "filter[max_date]": last_day.strftime("%Y-%m-%d"),
        },
        timeout=30,
    )
    if resp.status_code in (404, 400):
        print(f"[INFO] Factures clients : {resp.status_code} — liste vide retournée")
        return []
    if resp.status_code == 401:
        print("[ERROR] Token Pennylane invalide ou expiré")
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("customer_invoices", data.get("data", []))

def match_transaction_to_invoice(supplier_invoice_id: int, transaction_id: str) -> bool:
    """Rapproche une transaction à une facture fournisseur."""
    resp = requests.post(
        f"{BASE_URL}/supplier_invoices/{supplier_invoice_id}/matched_transactions",
        headers=_headers(),
        json={"id": transaction_id},
        timeout=30,
    )
    if resp.status_code in (200, 201):
        return True
    print(f"[WARN] Match échoué {supplier_invoice_id} ↔ {transaction_id}: {resp.text}")
    return False
