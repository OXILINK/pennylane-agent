"""
matching_engine.py — Logique de rapprochement factures ↔ transactions
Gère : MAIF, SEPA, CB différée, standard, règles dynamiques apprises
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

RULES_PATH = os.path.join(os.path.dirname(__file__), "data", "rules.json")


@dataclass
class MatchResult:
    matched: bool
    confidence: int          # 0-100
    invoice_id: Optional[int]
    transaction_id: Optional[str]
    rule_id: Optional[str]
    reason: str


def _load_rules() -> dict:
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _label_similarity(a: str, b: str) -> float:
    a, b = a.upper().strip(), b.upper().strip()
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _parse_date(d) -> Optional[datetime]:
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(d)[:10], fmt[:10])
        except ValueError:
            continue
    return None


def _amount(x) -> float:
    try:
        return abs(float(str(x).replace(",", ".")))
    except (ValueError, TypeError):
        return 0.0


# ─── Règles métier spécifiques ──────────────────────────────────────────────

def _match_maif(tx: dict, inv: dict, rule: dict) -> Optional[MatchResult]:
    label = tx.get("label", "")
    if "MAIF" not in label.upper():
        return None

    inv_amount = _amount(inv.get("currency_amount", 0))
    tx_amount = _amount(tx.get("amount", 0))
    inv_date = _parse_date(inv.get("date") or inv.get("deadline"))
    tx_date = _parse_date(tx.get("date"))

    # Montant attendu = montant facture + frais 2.39%
    expected = round(inv_amount * (1 + rule.get("fee_percent", 2.39) / 100), 2)
    amount_ok = abs(tx_amount - expected) < 0.05

    day_ok = True
    if inv_date and tx_date:
        day_ok = abs((tx_date - inv_date).days) <= rule.get("day_tolerance", 3)

    if amount_ok and day_ok:
        return MatchResult(True, 97, inv["id"], tx["id"], rule["id"],
                           f"MAIF: {tx_amount}€ ≈ {expected}€ (facture {inv_amount}€ + frais)")
    if amount_ok:
        return MatchResult(True, 80, inv["id"], tx["id"], rule["id"],
                           f"MAIF: montant OK mais date hors tolérance")
    return None


def _match_sepa(tx: dict, inv: dict, rule: dict) -> Optional[MatchResult]:
    pattern = rule.get("supplier_pattern", "")
    label = tx.get("label", "")
    if _label_similarity(pattern, label) < 0.6:
        return None

    tx_amount = _amount(tx.get("amount", 0))
    inv_amount = _amount(inv.get("currency_amount", 0))
    inv_date = _parse_date(inv.get("date") or inv.get("deadline"))
    tx_date = _parse_date(tx.get("date"))

    tol_pct = rule.get("amount_tolerance_pct", 0)
    if tol_pct == 0:
        amount_ok = abs(tx_amount - inv_amount) < 0.02
    else:
        amount_ok = abs(tx_amount - inv_amount) / max(inv_amount, 0.01) * 100 <= tol_pct

    day_tol = rule.get("day_tolerance", 16)
    day_ok = True
    days_diff = 0
    if inv_date and tx_date:
        days_diff = abs((tx_date - inv_date).days)
        day_ok = days_diff <= day_tol

    if amount_ok and day_ok:
        similarity = _label_similarity(pattern, label)
        confidence = int(70 + similarity * 25)
        return MatchResult(True, min(confidence, 99), inv["id"], tx["id"], rule["id"],
                           f"SEPA {pattern}: écart {days_diff}j, montant {tx_amount}€")
    return None


def _match_deferred_cb(transactions: list, invoices: list, rule: dict) -> list[MatchResult]:
    """CB différée : compare somme mensuelle transactions vs somme factures."""
    pattern = rule.get("supplier_pattern", "")
    results = []

    # Grouper par mois
    monthly_tx: dict[str, list] = {}
    for tx in transactions:
        label = tx.get("label", "")
        if _label_similarity(pattern, label) < 0.55:
            continue
        d = _parse_date(tx.get("date"))
        if not d:
            continue
        key = f"{d.year}-{d.month:02d}"
        monthly_tx.setdefault(key, []).append(tx)

    monthly_inv: dict[str, list] = {}
    for inv in invoices:
        supplier = (inv.get("supplier", {}) or {}).get("name", "")
        if _label_similarity(pattern, supplier) < 0.55:
            continue
        d = _parse_date(inv.get("date") or inv.get("deadline"))
        if not d:
            continue
        key = f"{d.year}-{d.month:02d}"
        monthly_inv.setdefault(key, []).append(inv)

    tol_pct = rule.get("amount_tolerance_pct", 5)
    for month_key, month_txs in monthly_tx.items():
        month_invs = monthly_inv.get(month_key, [])
        if not month_invs:
            continue
        total_tx = sum(_amount(t.get("amount", 0)) for t in month_txs)
        total_inv = sum(_amount(i.get("currency_amount", 0)) for i in month_invs)
        if total_inv == 0:
            continue
        ecart_pct = abs(total_tx - total_inv) / total_inv * 100
        if ecart_pct <= tol_pct:
            for tx in month_txs:
                for inv in month_invs:
                    results.append(MatchResult(
                        True, 88, inv["id"], tx["id"], rule["id"],
                        f"CB diff {pattern} {month_key}: {total_tx}€ vs {total_inv}€ ({ecart_pct:.1f}%)"
                    ))
    return results


def _match_standard(tx: dict, inv: dict) -> Optional[MatchResult]:
    """Règle standard : ±2€, ±7 jours, libellé similaire."""
    tx_amount = _amount(tx.get("amount", 0))
    inv_amount = _amount(inv.get("currency_amount", 0))
    if abs(tx_amount - inv_amount) > 2.0:
        return None

    inv_date = _parse_date(inv.get("date") or inv.get("deadline"))
    tx_date = _parse_date(tx.get("date"))
    if inv_date and tx_date and abs((tx_date - inv_date).days) > 7:
        return None

    supplier_name = (inv.get("supplier", {}) or {}).get("name", "")
    label = tx.get("label", "")
    sim = _label_similarity(supplier_name, label)
    if sim < 0.4:
        return None

    confidence = int(50 + sim * 35 + (1 - abs(tx_amount - inv_amount) / 2) * 15)
    return MatchResult(True, min(confidence, 84), inv["id"], tx["id"], "standard",
                       f"Standard: {tx_amount}€≈{inv_amount}€, similarité={sim:.0%}")


# ─── Détection nouveaux fournisseurs ─────────────────────────────────────────

def find_unknown_suppliers(transactions: list) -> list[dict]:
    """Retourne les transactions dont le fournisseur n'est dans aucune règle."""
    rules_data = _load_rules()
    known_patterns = [r["supplier_pattern"].upper() for r in rules_data["rules"]]
    pending = rules_data.get("pending_confirmations", {})

    unknown = []
    seen_labels = set()
    for tx in transactions:
        label = tx.get("label", "").upper().strip()
        if not label or label in seen_labels:
            continue
        # Déjà en attente de confirmation ?
        if label in [k.upper() for k in pending.keys()]:
            continue
        # Matcher contre patterns connus
        matched_known = any(_label_similarity(p, label) >= 0.6 for p in known_patterns)
        if not matched_known:
            seen_labels.add(label)
            unknown.append({
                "label": tx.get("label"),
                "amount": tx.get("amount"),
                "date": tx.get("date"),
                "transaction_id": tx.get("id"),
            })
    return unknown


# ─── Point d'entrée principal ─────────────────────────────────────────────────

def run_matching(transactions: list, invoices: list) -> list[MatchResult]:
    """Lance tous les moteurs de matching et retourne la liste des résultats."""
    rules_data = _load_rules()
    results: list[MatchResult] = []
    matched_tx_ids = set()
    matched_inv_ids = set()

    # 1. CB différée (matching par lot mensuel)
    for rule in rules_data["rules"]:
        if rule["match_type"] == "deferred_cb":
            cb_results = _match_deferred_cb(transactions, invoices, rule)
            for r in cb_results:
                if r.transaction_id not in matched_tx_ids:
                    results.append(r)
                    matched_tx_ids.add(r.transaction_id)
                    matched_inv_ids.add(r.invoice_id)

    # 2. Règles MAIF, SEPA, dynamiques (matching 1-à-1)
    for inv in invoices:
        if inv["id"] in matched_inv_ids:
            continue
        for tx in transactions:
            if tx["id"] in matched_tx_ids:
                continue
            best: Optional[MatchResult] = None
            for rule in rules_data["rules"]:
                mtype = rule["match_type"]
                r = None
                if mtype == "insurance_schedule":
                    r = _match_maif(tx, inv, rule)
                elif mtype == "sepa":
                    r = _match_sepa(tx, inv, rule)
                elif mtype in ("dynamic", "standard_custom"):
                    r = _match_sepa(tx, inv, rule)  # même logique
                if r and (best is None or r.confidence > best.confidence):
                    best = r

            # Fallback règle standard
            if best is None or best.confidence < 50:
                std = _match_standard(tx, inv)
                if std and (best is None or std.confidence > best.confidence):
                    best = std

            if best and best.confidence > 0:
                results.append(best)
                if best.confidence >= int(os.getenv("CONFIDENCE_THRESHOLD", "85")):
                    matched_tx_ids.add(tx["id"])
                    matched_inv_ids.add(inv["id"])

    return results
