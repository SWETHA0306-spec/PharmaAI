"""
meditrust_db.py — MediTrust Secure Verification Database
============================================================
NEW MODULE — additive only. Replaces the CDSCO/DAVA government-API
attempt (which was never callable — no public third-party endpoint
exists) with a self-owned verification database that PharmaAI/MediTrust
controls directly.

Does NOT touch resolver.py, interactions.py, ocr_fixed.py, or
report.py — the existing counterfeit-detection + drug-interaction
modules remain exactly as they were. This module only ADDS a new
verification layer that can be used alongside them.

Architecture (per MediTrust spec):

    QR Scan
       |
       v
    MediTrust Verification Database
       |
       v
    Batch + QR + Scan History Check
       |
       v
    Result: Genuine / Suspicious / High Risk

Data source: data/MediTrust_demo_verification_dataset.csv
Table fields: QR_ID, Medicine_Name, Manufacturer, Batch_No,
              Manufacturing_Date, Expiry_Date, Verification_Status,
              Detection_Reason, Scan_Count, Last_Scan_Location

For a real deployment this CSV would be replaced by a real database
(Postgres/MongoDB/etc) behind the same lookup_qr() / record_scan()
interface — the rest of the system doesn't need to change.

Usage:
    from meditrust_db import MediTrustDB

    db = MediTrustDB()                       # loads CSV once
    result = db.verify_qr("MT100001", scan_location="Chennai")
    print(result["verdict"])                  # GENUINE | SUSPICIOUS | HIGH_RISK
"""

import csv
import os
import datetime
import json
from typing import Optional


DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "MediTrust_demo_verification_dataset.csv"
)

# Where new/duplicate scans get logged for this session (separate from
# the read-only demo CSV, so the original dataset is never overwritten)
DEFAULT_SCAN_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "meditrust_scan_log.json"
)


def _parse_date(date_str: Optional[str]):
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


class MediTrustDB:
    """
    Loads the MediTrust verification dataset and provides QR lookup,
    batch/expiry checks, and duplicate-scan / location-anomaly detection.

    Live scans (new QR sightings not in the original CSV, or repeat
    scans of an existing QR) are tracked in an in-memory + JSON-logged
    session store so "scan history" works across calls without ever
    mutating the original demo CSV.
    """

    def __init__(self, csv_path: str = DEFAULT_CSV_PATH,
                 scan_log_path: str = DEFAULT_SCAN_LOG_PATH):
        self.csv_path = csv_path
        self.scan_log_path = scan_log_path
        self._records = {}          # QR_ID -> dict (from CSV, read-only reference data)
        self._scan_log = {}         # QR_ID -> list of {"location": ..., "timestamp": ...}
        self._load_csv()
        self._load_scan_log()

    # ──────────────────────────────────────────────────────────
    #  LOADING
    # ──────────────────────────────────────────────────────────

    def _load_csv(self):
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(
                f"MediTrust dataset not found at: {self.csv_path}\n"
                f"Expected file: data/MediTrust_demo_verification_dataset.csv"
            )
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self._records[row["QR_ID"]] = row

    def _load_scan_log(self):
        """Load any prior session's scan history, if present."""
        if os.path.exists(self.scan_log_path):
            try:
                with open(self.scan_log_path, "r", encoding="utf-8") as f:
                    self._scan_log = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._scan_log = {}

    def _save_scan_log(self):
        os.makedirs(os.path.dirname(self.scan_log_path), exist_ok=True)
        with open(self.scan_log_path, "w", encoding="utf-8") as f:
            json.dump(self._scan_log, f, indent=2)

    def reload(self):
        """Re-read the CSV from disk (e.g. after it's updated)."""
        self._records = {}
        self._load_csv()

    # ──────────────────────────────────────────────────────────
    #  LOOKUP
    # ──────────────────────────────────────────────────────────

    def lookup_qr(self, qr_id: str) -> Optional[dict]:
        """Raw record lookup. Returns None if QR_ID not in database."""
        return self._records.get(qr_id)

    def _record_scan_event(self, qr_id: str, location: Optional[str]) -> list:
        """Append a scan event to this session's in-memory log, return full history."""
        if qr_id not in self._scan_log:
            self._scan_log[qr_id] = []
        self._scan_log[qr_id].append({
            "location":  location or "Unknown",
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self._save_scan_log()
        return self._scan_log[qr_id]

    # ──────────────────────────────────────────────────────────
    #  MAIN VERIFICATION ENTRY POINT
    # ──────────────────────────────────────────────────────────

    def verify_qr(self, qr_id: str, scan_location: Optional[str] = None) -> dict:
        """
        Full verification flow per the MediTrust spec:

          1. Does QR exist in the database?
          2. Is the batch/expiry/manufacturer data valid?
          3. Has this QR been scanned before? From where?
          4. Duplicate-QR / location-anomaly check.

        Returns a structured result with verdict GENUINE | SUSPICIOUS | HIGH_RISK.
        """
        record = self.lookup_qr(qr_id)
        flags = []
        score = 0.0  # 0 = clean, 1 = max risk

        if record is None:
            # QR not found at all — never registered as genuine stock
            session_history = self._record_scan_event(qr_id, scan_location)
            return {
                "qr_id": qr_id,
                "found_in_database": False,
                "medicine_name": None,
                "manufacturer": None,
                "batch_no": None,
                "expiry_status": "UNKNOWN",
                "scan_history": session_history,
                "scan_count_total": len(session_history),
                "flags": ["QR code not found in MediTrust database — not a registered product."],
                "verdict": "HIGH_RISK",
                "score": 1.0,
            }

        # ── Base verdict carried over from the dataset's own labelling ──
        base_status = record.get("Verification_Status", "VALID")
        if base_status == "HIGH_RISK":
            score += 0.6
            flags.append(f"Flagged in database: {record.get('Detection_Reason', 'Unknown reason')}")
        elif base_status == "SUSPICIOUS":
            score += 0.3
            flags.append(f"Flagged in database: {record.get('Detection_Reason', 'Unknown reason')}")

        # ── Expiry check ──
        exp = _parse_date(record.get("Expiry_Date"))
        expiry_status = "OK"
        if exp:
            today = datetime.date.today()
            if exp < today:
                expiry_status = "EXPIRED"
                flags.append(f"EXPIRED — expiry date {exp.isoformat()} has passed.")
                score += 0.4
            elif (exp - today).days <= 60:
                expiry_status = "EXPIRING_SOON"
        else:
            expiry_status = "UNKNOWN"
            flags.append("No valid expiry date on record.")
            score += 0.1

        # ── Manufacturer sanity check ──
        manufacturer = record.get("Manufacturer", "")
        if not manufacturer or manufacturer.strip().lower() in ("unknown manufacturer", "unknown", ""):
            flags.append("Manufacturer is unknown/unregistered.")
            score += 0.3

        # ── Duplicate-QR / scan-history anomaly detection ──
        # This is the core "scan history" check from the spec:
        #   First scan of a QR_ID -> normal
        #   Repeat scans from DIFFERENT locations -> suspicious
        #     (a single genuine pack shouldn't be scanned as new stock
        #      in two different cities)
        prior_scans = list(self._scan_log.get(qr_id, []))  # snapshot copy — must not alias the live list
        prior_scan_count_session = len(prior_scans)
        dataset_scan_count = int(record.get("Scan_Count", 0) or 0)

        session_history = self._record_scan_event(qr_id, scan_location)  # appends + returns full history
        all_locations = [s["location"] for s in session_history]
        distinct_locations = set(l for l in all_locations if l and l != "Unknown")

        had_prior_activity = dataset_scan_count > 0 or prior_scan_count_session > 0

        duplicate_flag = False
        if had_prior_activity:
            # This QR has been seen before (either pre-seeded scan count
            # in the dataset, or scanned earlier in this running session)
            if len(distinct_locations) > 1:
                duplicate_flag = True
                flags.append(
                    f"Possible counterfeit — same QR scanned from multiple locations: "
                    f"{', '.join(sorted(distinct_locations))}."
                )
                score += 0.5
            elif dataset_scan_count >= 3:
                duplicate_flag = True
                flags.append(
                    f"Unusually high scan count ({dataset_scan_count}) for a single unit — "
                    f"possible cloned QR."
                )
                score += 0.3

        score = min(score, 1.0)

        if score >= 0.5:
            verdict = "HIGH_RISK"
        elif score >= 0.2:
            verdict = "SUSPICIOUS"
        else:
            verdict = "GENUINE"

        return {
            "qr_id":              qr_id,
            "found_in_database":  True,
            "medicine_name":      record.get("Medicine_Name"),
            "manufacturer":       manufacturer,
            "batch_no":           record.get("Batch_No"),
            "manufacturing_date": record.get("Manufacturing_Date"),
            "expiry_date":        record.get("Expiry_Date"),
            "expiry_status":      expiry_status,
            "duplicate_qr_detected": duplicate_flag,
            "scan_history":       session_history,
            "scan_count_total":   dataset_scan_count + len(session_history),
            "distinct_scan_locations": sorted(distinct_locations),
            "flags":               flags,
            "verdict":             verdict,
            "score":               round(score, 3),
        }

    # ──────────────────────────────────────────────────────────
    #  STATS (useful for a dashboard / demo)
    # ──────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total = len(self._records)
        by_status = {}
        for r in self._records.values():
            s = r.get("Verification_Status", "UNKNOWN")
            by_status[s] = by_status.get(s, 0) + 1
        return {
            "total_registered_products": total,
            "by_status": by_status,
            "live_session_scans": sum(len(v) for v in self._scan_log.values()),
        }


# ══════════════════════════════════════════════════════════════
#  CONSOLE PRINTER (matches style of report.py / batch_verify.py)
# ══════════════════════════════════════════════════════════════

VERDICT_EMOJI = {
    "GENUINE":    "✅",
    "SUSPICIOUS": "⚠️ ",
    "HIGH_RISK":  "❌",
}


def print_meditrust_result(result: dict):
    SEP = "─" * 55
    emoji = VERDICT_EMOJI.get(result["verdict"], "⚪")

    print(f"\n  ┌─ MEDITRUST QR VERIFICATION {SEP[:25]}┐")
    print(f"  │  QR ID            : {result['qr_id']}")
    print(f"  │  Found in database: {'Yes' if result['found_in_database'] else 'No'}")

    if result["found_in_database"]:
        print(f"  │  Medicine         : {result.get('medicine_name') or '—'}")
        print(f"  │  Manufacturer     : {result.get('manufacturer') or '—'}")
        print(f"  │  Batch No         : {result.get('batch_no') or '—'}")
        print(f"  │  Expiry           : {result.get('expiry_date') or '—'}  "
              f"({result.get('expiry_status')})")
        print(f"  │  Total scans      : {result.get('scan_count_total')}")
        locs = result.get("distinct_scan_locations", [])
        if locs:
            print(f"  │  Scan locations   : {', '.join(locs)}")

    print(f"  │")
    print(f"  │  Verdict           : {emoji} {result['verdict']}")
    print(f"  │  Risk score        : {result['score']:.0%}")

    if result["flags"]:
        print(f"  │  Flags:")
        for f in result["flags"]:
            print(f"  │    ⚑  {f}")
    else:
        print(f"  │  Flags             : None")

    print(f"  └{'─'*53}┘")


# ══════════════════════════════════════════════════════════════
#  CLI — standalone test
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="MediTrust QR verification (standalone test)")
    parser.add_argument("--qr", required=False, help="QR_ID to verify, e.g. MT100001")
    parser.add_argument("--location", help="Scan location, e.g. Chennai")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    args = parser.parse_args()

    db = MediTrustDB()

    if args.stats:
        print(json.dumps(db.stats(), indent=2))

    if args.qr:
        result = db.verify_qr(args.qr, scan_location=args.location)
        print_meditrust_result(result)
    elif not args.stats:
        parser.print_help()
