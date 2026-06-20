"""
resolver.py — Drug Name → Canonical Identity
=============================================
Resolves brand/generic names (from OCR or text input) to:
  • Canonical generic name
  • RxCUI  (RxNorm standard ID)
  • CID    (PubChem Compound ID)
  • SMILES (molecular structure string)
  • NDC    (National Drug Code, via OpenFDA)

Sources (all free, no key required):
  1. RxNorm REST API  — rxnav.nlm.nih.gov
  2. PubChem REST API — pubchem.ncbi.nlm.nih.gov
  3. OpenFDA API      — api.fda.gov

Install:  pip install requests
"""

import re
import sys
import time
import json

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

RXNORM_BASE  = "https://rxnav.nlm.nih.gov/REST"
PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
OPENFDA_BASE = "https://api.fda.gov/drug"

TIMEOUT = 10   # seconds per request
RETRIES = 2    # retries on timeout/5xx

# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _get(url: str, params: dict = None) -> dict | list | None:
    """GET with retries; returns parsed JSON or None on failure."""
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            if attempt < RETRIES:
                time.sleep(1.5)
        except Exception:
            break
    return None


def _clean_name(name: str) -> str:
    """
    Strip common suffixes that confuse drug databases:
      ®  ™  IP  BP  USP  (India)  (tablet)  0.05% etc.
    """
    name = re.sub(r'[®™©]', '', name)
    name = re.sub(r'\b(IP|BP|USP|EP|NF|Ph\.Eur)\b', '', name, flags=re.I)
    name = re.sub(r'\b(tablet|capsule|syrup|injection|ointment|cream|gel|drops?|solution)\b',
                  '', name, flags=re.I)
    name = re.sub(r'\d+(\.\d+)?\s*(%|mg|mcg|ml|g|iu)\b.*', '', name, flags=re.I)
    return name.strip(' ,.-')


# ══════════════════════════════════════════════════════════════
#  STEP 1 — RxNorm: name → RxCUI + canonical generic name
# ══════════════════════════════════════════════════════════════

def _rxnorm_lookup(name: str) -> dict:
    """
    Try approximate-match then exact-match on RxNorm.
    Returns dict with rxcui, canonical_name, tty (term type).
    """
    result = {"rxcui": None, "canonical_name": None, "tty": None}

    # Approximate match (handles misspellings, brand names)
    data = _get(f"{RXNORM_BASE}/approximateTerm.json",
                params={"term": name, "maxEntries": 5})
    if data:
        candidates = (data.get("approximateGroup") or {}).get("candidate") or []
        for c in candidates:
            if c.get("rxcui"):
                result["rxcui"] = c["rxcui"]
                break

    # If approximate match found, get full concept details
    if result["rxcui"]:
        detail = _get(f"{RXNORM_BASE}/rxcui/{result['rxcui']}/properties.json")
        if detail:
            props = (detail.get("properties") or {})
            result["canonical_name"] = props.get("name")
            result["tty"]            = props.get("tty")

    return result


def _rxnorm_ingredient(rxcui: str) -> str | None:
    """
    Walk RxNorm relationships to find the base ingredient RxCUI.
    e.g. "Tylenol 500mg tablet" → "acetaminophen" RxCUI
    """
    data = _get(f"{RXNORM_BASE}/rxcui/{rxcui}/related.json",
                params={"tty": "IN+PIN+MIN"})
    if not data:
        return None
    groups = (data.get("relatedGroup") or {}).get("conceptGroup") or []
    for g in groups:
        props = g.get("conceptProperties") or []
        if props:
            return props[0].get("rxcui")
    return None


# ══════════════════════════════════════════════════════════════
#  STEP 2 — PubChem: name → CID + SMILES
# ══════════════════════════════════════════════════════════════

def _pubchem_lookup(name: str) -> dict:
    """
    Query PubChem by name. Returns cid + canonical SMILES.
    Falls back to synonym search if direct name fails.
    """
    result = {"cid": None, "smiles": None, "iupac_name": None,
              "molecular_formula": None, "molecular_weight": None}

    # Direct name lookup
    data = _get(f"{PUBCHEM_BASE}/compound/name/{requests.utils.quote(name)}"
                f"/property/CanonicalSMILES,IUPACName,MolecularFormula,"
                f"MolecularWeight/JSON")
    if data:
        props = (data.get("PropertyTable") or {}).get("Properties") or []
        if props:
            p = props[0]
            result["cid"]               = p.get("CID")
            result["smiles"]            = p.get("CanonicalSMILES")
            result["iupac_name"]        = p.get("IUPACName")
            result["molecular_formula"] = p.get("MolecularFormula")
            result["molecular_weight"]  = p.get("MolecularWeight")
            return result

    # Synonym search fallback
    syn_data = _get(f"{PUBCHEM_BASE}/compound/name/{requests.utils.quote(name)}"
                    f"/cids/JSON", params={"name_type": "word"})
    if syn_data:
        cids = (syn_data.get("IdentifierList") or {}).get("CID") or []
        if cids:
            cid = cids[0]
            prop_data = _get(f"{PUBCHEM_BASE}/compound/cid/{cid}"
                             f"/property/CanonicalSMILES,IUPACName,"
                             f"MolecularFormula,MolecularWeight/JSON")
            if prop_data:
                props = (prop_data.get("PropertyTable") or {}).get("Properties") or []
                if props:
                    p = props[0]
                    result["cid"]               = cid
                    result["smiles"]            = p.get("CanonicalSMILES")
                    result["iupac_name"]        = p.get("IUPACName")
                    result["molecular_formula