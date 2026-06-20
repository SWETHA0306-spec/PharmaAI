"""
api_bridge.py v7 — mirrors EXACTLY what the CLI does
=====================================================
CLI flow for "Warfarin + Aspirin":
  mock_ocr = {brand_name:"Warfarin", generic_name:"Warfarin", ...}
  identity = resolve_drug(mock_ocr)          → {canonical_name:"warfarin", ...}
  intr     = check_interactions(identity, ["Aspirin"])
  report   = build_report(identity, intr, source_mode="text")

This bridge does EXACTLY that for every pair.
"""

import os, sys, uuid, shutil, traceback, itertools
from datetime import datetime, timedelta
from pathlib  import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security        import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles     import StaticFiles
from pydantic import BaseModel

import bcrypt
from jose           import jwt, JWTError
from pymongo        import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from bson           import ObjectId

def _find_core() -> Path:
    for p in [Path(__file__).parent/"core",
              Path(__file__).parent.parent/"core",
              Path.cwd()/"core"]:
        if p.is_dir() and (p/"resolver.py").exists():
            return p
    raise RuntimeError("Cannot find core/. Run uvicorn from the folder containing core/")

CORE_DIR = _find_core()
print(f"[PharmaAI] core/ → {CORE_DIR}")
sys.path.insert(0, str(CORE_DIR))

from resolver     import resolve_drug
from interactions import check_interactions
from report       import build_report
print("[PharmaAI] Pipeline OK")

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
sarvam_client = None
if SARVAM_API_KEY:
    try:
        from sarvamai import SarvamAI
        sarvam_client = SarvamAI(api_subscription_key=SARVAM_API_KEY)
        print("[PharmaAI] Sarvam AI Client initialized.")
    except Exception as e:
        print(f"[PharmaAI] Error initializing Sarvam AI: {e}")
else:
    print("[PharmaAI] WARNING: SARVAM_API_KEY not set. The patient-friendly "
          "explanation, translation, and voice (TTS) features will be "
          "unavailable; verified medicine information will still be shown "
          "as plain text. Set SARVAM_API_KEY as an environment variable to "
          "enable Sarvam AI features.")

import secrets
SECRET_KEY = os.environ.get("JWT_SECRET", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("[PharmaAI] WARNING: JWT_SECRET not set. Generated a temporary random "
          "secret for this run — existing login sessions will be invalidated "
          "on every restart. Set JWT_SECRET as an environment variable for "
          "stable, secure sessions in production.")
ALGORITHM     = "HS256"
ACCESS_EXPIRE = 120

BASE_DIR   = Path(__file__).parent
IS_VERCEL  = os.environ.get("VERCEL", "0") == "1"

UPLOAD_DIR = "/tmp/uploads" if IS_VERCEL else str(BASE_DIR / "uploads")
MONGO_URI  = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB   = "pharma_ai"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs("/tmp/reports" if IS_VERCEL else str(BASE_DIR / "reports"), exist_ok=True)

AUDIO_CACHE_DIR  = "/tmp/audio_cache" if IS_VERCEL else str(BASE_DIR / "audio_cache")
AUDIO_CACHE_MAX_BYTES = int(os.environ.get("AUDIO_CACHE_MAX_MB", "200")) * 1024 * 1024
os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

try:
    _mc = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    _mc.admin.command("ping")
    print("[PharmaAI] MongoDB OK")
except Exception as e:
    raise RuntimeError(f"MongoDB: {e}")

mdb        = _mc[MONGO_DB]
users_col  = mdb["users"]
checks_col = mdb["drug_checks"]
fraud_col  = mdb["fraud_log"]
ip_col     = mdb["ip_counters"]

users_col.create_index("email", unique=True)
checks_col.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
ip_col.create_index([("ip_address", ASCENDING), ("window_start", ASCENDING)], unique=True)

_apw = bcrypt.hashpw(b"Admin@123", bcrypt.gensalt()).decode()
users_col.update_one({"email": "admin@pharma.ai"},
    {"$setOnInsert": {"full_name":"Admin","email":"admin@pharma.ai",
                      "password_hash":_apw,"role":"admin",
                      "is_active":True,"created_at":datetime.utcnow()}}, upsert=True)

def _make_mock_ocr(name: str) -> dict:
    safe = (name or "").strip() or "Unknown"
    return {
        "success":       True,
        "_source_mode":  "text",
        "brand_name":    safe,
        "generic_name":  safe,
        "dosage":        None,
        "dosage_form":   None,
        "batch_no":      None,
        "mfg_date":      None,
        "exp_date":      None,
        "manufacturer":  None,
        "license_no":    None,
        "storage":       None,
        "raw_text":      safe,
    }

def _clean_verdict(raw: str) -> str:
    s = str(raw).replace("✅","").replace("🚨","").replace("⚪","").replace("⚠️","").replace("⚠","").strip().upper()
    if "AUTHENTIC" in s:  return "AUTHENTIC"
    if "WARN" in s:       return "WARN"
    if "HIGH" in s:       return "SUSPICIOUS"
    if "MODERATE" in s:   return "WARN"
    return s or "UNKNOWN"

def _rec(level: str) -> str:
    return {
        "CRITICAL": "🚫 CRITICAL: Do NOT use this combination. Consult a physician immediately.",
        "HIGH":     "🔴 HIGH RISK: Consult a pharmacist or physician before taking this combination.",
        "MODERATE": "🟡 MODERATE RISK: Use with caution. Monitor for adverse effects.",
        "LOW":      "🟢 LOW RISK: Minor concerns noted. Follow prescribing instructions.",
        "NONE":     "✅ No significant interactions detected.",
    }.get(level.upper(), "Consult a healthcare professional.")

def _shape_drug(identity: dict, label: str, report: dict, ocr: dict = None) -> dict:
    auth = report.get("authenticity", {})
    di   = report.get("drug_identity", {})
    ocr  = ocr or {}
    
    auth_score = auth.get("score")
    if auth_score is None and "suspicion_score" in identity:
        auth_score = 1.0 - float(identity["suspicion_score"])
        
    return {
        "label":           label,
        "brand_name":      di.get("brand_name")   or identity.get("brand_name")  or label,
        "generic_name":    di.get("generic_name")  or identity.get("canonical_name") or identity.get("generic_name") or "",
        "canonical_name":  identity.get("canonical_name") or "",
        "rxcui":           identity.get("rxcui")   or "",
        "drug_class":      identity.get("drug_class") or "",
        "dosage_form":     di.get("dosage_form")   or "",
        "dosage":          di.get("dosage")        or "",
        "route":           identity.get("route")   or "",
        "formula":         identity.get("mol_formula") or identity.get("smiles") or "",
        "mol_weight":      str(identity.get("mol_weight") or ""),
        "pubchem_cid":     str(identity.get("pubchem_cid") or ""),
        "manufacturer":    identity.get("manufacturer_fda") or di.get("manufacturer") or "",
        "auth_score":      auth_score if auth_score is not None else 0.0,
        "auth_level":      auth.get("level") or identity.get("suspicion_level") or "UNKNOWN",
        "verdict":         _clean_verdict(auth.get("verdict") or identity.get("authenticity_verdict") or "UNKNOWN"),
        "suspicion_flags": auth.get("flags") or identity.get("suspicion_flags") or [],
        "detected_language": identity.get("detected_language") or ocr.get("detected_language"),
        "ocr_confidence":    identity.get("ocr_confidence") or ocr.get("ocr_confidence"),
        "ocr_fields": {
            "batch_no":     ocr.get("batch_no") or identity.get("ocr_batch_no"),
            "exp_date":     ocr.get("exp_date") or identity.get("ocr_exp_date"),
            "mfg_date":     ocr.get("mfg_date") or identity.get("ocr_mfg_date"),
            "mrp":          ocr.get("mrp") or identity.get("ocr_mrp"),
            "composition":  ocr.get("composition") or identity.get("composition"),
        }
    }

def _shape_pair(intr_result: dict, a_label: str, b_label: str) -> dict:
    raw_list = intr_result.get("interactions", [])
    items = []
    for x in raw_list:
        if not isinstance(x, dict):
            continue
        items.append({
            "drug_a":      x.get("drug_a", a_label),
            "drug_b":      x.get("drug_b", b_label),
            "severity":    x.get("severity", "unknown"),
            "description": x.get("description", ""),
            "source":      ", ".join(x.get("all_sources", [x.get("source", "")])),
        })

    return {
        "drug_a":       a_label,
        "drug_b":       b_label,
        "overall_risk": intr_result.get("overall_risk", "NONE"),
        "summary":      intr_result.get("summary", {}),
        "interactions": items,
        "drugs_checked":intr_result.get("drugs_checked", []),
        "unresolved":   intr_result.get("drugs_unresolved", []),
    }

app = FastAPI(title="PharmaAI", version="7.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
bearer_scheme = HTTPBearer(auto_error=False)

@app.get("/api/debug")
def debug():
    try:
        ocr_w = _make_mock_ocr("Warfarin")
        ocr_a = _make_mock_ocr("Aspirin")
        id_w  = resolve_drug(ocr_w)
        id_a  = resolve_drug(ocr_a)
        intr  = check_interactions(id_w, ["Aspirin"])
        rep   = build_report(id_w, intr, source_mode="text")
        return {
            "status":             "OK",
            "identity_keys":      list(id_w.keys()),
            "report_keys":        list(rep.keys()),
            "interaction_count":  len(intr.get("interactions", [])),
            "overall_risk":       intr.get("overall_risk"),
            "first_interaction":  intr.get("interactions", [None])[0],
            "identity_warfarin":  id_w,
            "report_structure":   {k: list(v.keys()) if isinstance(v,dict) else type(v).__name__
                                   for k,v in rep.items()},
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "trace": traceback.format_exc()}

def _make_token(data, exp): return jwt.encode({**data,"exp":datetime.utcnow()+exp}, SECRET_KEY, algorithm=ALGORITHM)
def _decode(tok):
    try:    return jwt.decode(tok, SECRET_KEY, algorithms=[ALGORITHM])
    except: raise HTTPException(401, "Invalid or expired token")
def get_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not creds: raise HTTPException(401, "Not authenticated")
    p = _decode(creds.credentials)
    return {"id":p["sub"],"email":p["email"],"role":p["role"]}

class RegBody(BaseModel): full_name:str; email:str; password:str
class LoginBody(BaseModel): email:str; password:str

@app.post("/api/auth/register")
def register(b: RegBody):
    pw = bcrypt.hashpw(b.password.encode(), bcrypt.gensalt()).decode()
    try:
        r = users_col.insert_one({"full_name":b.full_name,"email":b.email.lower().strip(),
            "password_hash":pw,"role":"user","is_active":True,"created_at":datetime.utcnow()})
    except DuplicateKeyError: raise HTTPException(409,"Email already registered")
    return {"access_token":_make_token({"sub":str(r.inserted_id),"email":b.email,"role":"user"},
             timedelta(minutes=ACCESS_EXPIRE)), "role":"user","full_name":b.full_name}

@app.post("/api/auth/login")
def login(b: LoginBody):
    u = users_col.find_one({"email":b.email.lower().strip(),"is_active":True})
    if not u or not bcrypt.checkpw(b.password.encode(), u["password_hash"].encode()):
        raise HTTPException(401,"Invalid credentials")
    return {"access_token":_make_token({"sub":str(u["_id"]),"email":u["email"],"role":u["role"]},
             timedelta(minutes=ACCESS_EXPIRE)), "role":u["role"],"full_name":u["full_name"]}

@app.get("/api/auth/me")
def me(user=Depends(get_user)): return user

def generate_patient_explanation(drugs: list, pairs: list, combined: dict) -> str:
    # ── RULE-BASED EXPLANATION (Replacing Sarvam AI) ──
    try:
        parts = []
        parts.append(f"You have scanned {len(drugs)} medicine(s).")
        
        risk = combined.get("overall_interaction_risk", "NONE")
        if risk == "NONE" or risk == "UNKNOWN":
            parts.append("No significant interactions were detected.")
        else:
            parts.append(f"Overall interaction risk is {risk}.")
            rec = combined.get("recommendation", "")
            if rec: parts.append(rec)
            
        if combined.get("any_auth_issue"):
            parts.append("Note: One or more medicines showed a warning during verification. Please check the authenticity score.")

        parts.append("\nHere is what you are taking:")
        for d in drugs:
            bn = d.get("brand_name") or d.get("label") or "Medicine"
            gn = d.get("generic_name")
            dc = d.get("drug_class")
            desc = f"- {bn}"
            if gn and gn != bn: desc += f" contains {gn}"
            if dc: desc += f", which is a {dc}"
            parts.append(desc + ".")
            
        if pairs:
            has_interactions = any(len(p.get("interactions", [])) > 0 for p in pairs)
            if has_interactions:
                parts.append("\nInteraction Details:")
                for p in pairs:
                    if p.get("interactions"):
                        parts.append(f"When taking {p.get('drug_a')} and {p.get('drug_b')} together, the risk is {p.get('overall_risk', 'UNKNOWN')}.")
                        for i, intr in enumerate(p.get("interactions")[:2]):
                            parts.append(f"  > {intr.get('description', '')}")

        return " ".join(parts)
    except Exception as e:
        print(f"[PharmaAI] Rule-based explanation error: {e}")
        return "Explanation could not be generated. Please review the verified information below."

    # ── ORIGINAL SARVAM AI CODE (ISOLATED/DISABLED) ──
    """
    import json
    if not sarvam_client:
        return ("Patient-friendly AI explanation is unavailable right now (Sarvam AI not "
                "configured). The verified medicine information above is unaffected — "
                "please review it directly, or consult a pharmacist or doctor.")

    system_prompt = \"\"\"You are a helpful and caring clinical pharmacist assistant. Your job is to translate complex drug interaction reports and authenticity checks into simple, patient-friendly, and easy-to-understand explanations.
    
    Create a summary that has:
    1. A patient-friendly explanation of findings.
    2. An easy-to-understand summary.
    3. Non-technical descriptions of any risks and drug-drug interactions.
    4. Plain language safety recommendations.
    
    Rules:
    - Avoid medical jargon. Use simple words (e.g., instead of "Moderate interaction detected", explain what that means in daily life).
    - Be concise, direct, and supportive.
    - Focus on patient safety.
    - You are explaining data that has ALREADY been verified and decided by the
      system (drug identity, authenticity, and interaction severity). Do not
      diagnose any condition, do not recommend or prescribe any medicine, and
      do not decide or override the interaction severity or risk level — only
      explain, in plain language, the verified findings given to you below.
    - If something is unclear or missing from the data, say so rather than
      guessing or filling it in.
    \"\"\"
    
    user_prompt = f\"\"\"
    Drugs analysed:
    {json.dumps(drugs, indent=2)}
    
    Pairwise Interactions:
    {json.dumps(pairs, indent=2)}
    
    Combined Analysis:
    {json.dumps(combined, indent=2)}
    \"\"\"
    
    try:
        response = sarvam_client.chat.completions(
            model="sarvam-105b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[PharmaAI] Error generating explanation: {e}")
        return ("Patient-friendly explanation could not be generated right now "
                "(Sarvam AI request failed). The verified medicine information "
                "above is unaffected — please review it directly, or consult a "
                "pharmacist or doctor.")
    """

@app.post("/api/analyze")
async def analyze(
    request:    Request,
    images:     list[UploadFile] = File(default=[]),
    drug_names: str = Form(default=""),
    user = Depends(get_user)
):
    print(f"\n[PharmaAI] /api/analyze — drug_names={repr(drug_names)}")

    real_images = [img for img in images if img.filename]
    name_list   = [n.strip() for n in drug_names.replace(",","\n").split("\n") if n.strip()]

    print(f"[PharmaAI] images={len(real_images)}  text_names={name_list}")

    if not real_images and not name_list:
        raise HTTPException(400, "Provide at least one drug name or image")

    resolved = []
    errors   = []

    if real_images:
        try:
            from ocr_fixed import run_ocr as _run_ocr_raw
        except ImportError:
            from ocr import run_ocr as _run_ocr_raw

        def safe_ocr(path, filename):
            try:
                # Forcefully overwrite the cached API_KEY just to be absolutely sure
                import os
                import ocr_fixed
                ocr_fixed.API_KEY = os.environ.get("OCR_API_KEY", "")
                
                result = _run_ocr_raw(path)
            except AttributeError as e:
                if "strip" in str(e) or "NoneType" in str(e):
                    return {"success": False,
                            "error": "OpenRouter API returned null content — the free model is "
                                     "rate-limited. Wait 30 sec and retry, or check your API key."}
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": str(e)}

            if not isinstance(result, dict):
                return {"success": False, "error": "OCR returned unexpected type"}

            err_str = str(result.get("error", ""))
            if not result.get("success") and ("NoneType" in err_str or "'strip'" in err_str):
                return {"success": False,
                        "error": "OpenRouter returned null — model rate-limited. "
                                 "Wait 30 sec and retry, or use a clearer image."}

            if not result.get("success"):
                return result

            stem = Path(filename).stem or "drug"
            bn = result.get("brand_name") or result.get("generic_name") or stem
            gn = result.get("generic_name") or bn
            result["brand_name"]   = str(bn).strip() if bn else stem
            result["generic_name"] = str(gn).strip() if gn else stem
            result["raw_text"]     = result.get("raw_text") or stem
            result["_source_mode"] = "image"
            return result

        for img_file in real_images:
            ext  = Path(img_file.filename).suffix or ".jpg"
            dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
            with open(dest, "wb") as fh:
                shutil.copyfileobj(img_file.file, fh)
            try:
                ocr = safe_ocr(dest, img_file.filename)
                print(f"[PharmaAI] FULL OCR RESULT for {img_file.filename}:")
                import json
                print(json.dumps(ocr, indent=2))

                if not ocr.get("success"):
                    errors.append({"label": img_file.filename, "error": ocr.get("error", "OCR failed")})
                    continue
                identity = resolve_drug(ocr)
                print(f"[PharmaAI] Resolved image '{img_file.filename}' → '{identity.get('canonical_name')}'")
                resolved.append({"label": ocr["brand_name"], "identity": identity, "ocr": ocr})
            except Exception as e:
                tb = traceback.format_exc()
                print(f"[PharmaAI] Image error: {e}\n{tb}")
                errors.append({"label": img_file.filename, "error": str(e)})

    for name in name_list:
        try:
            mock_ocr = _make_mock_ocr(name)
            identity = resolve_drug(mock_ocr)
            print(f"[PharmaAI] Resolved '{name}' → canonical='{identity.get('canonical_name')}'  rxcui={identity.get('rxcui')}")
            resolved.append({"label": name, "identity": identity, "ocr": mock_ocr})
        except Exception as e:
            errors.append({"label":name,"error":str(e),"trace":traceback.format_exc()})

    if not resolved:
        return {"drugs":[],"pairs":[],"combined":{},"errors":errors,
                "debug":"All drugs failed to resolve — check uvicorn log"}

    RANK = {"CRITICAL":4,"HIGH":3,"MODERATE":2,"LOW":1,"NONE":0,"UNKNOWN":0}
    pairs       = []
    worst_risk  = "NONE"
    total_iact  = 0

    for i, j in itertools.combinations(range(len(resolved)), 2):
        a = resolved[i]
        b = resolved[j]
        a_label = a["label"]
        b_label = b["label"]

        print(f"[PharmaAI] Checking pair: '{a_label}' ↔ '{b_label}'")
        try:
            intr_result = check_interactions(a["identity"], [b_label])
            print(f"[PharmaAI] → {len(intr_result.get('interactions',[]))} interactions, risk={intr_result.get('overall_risk')}")
            pair = _shape_pair(intr_result, a_label, b_label)
            pairs.append(pair)
            total_iact += len(pair["interactions"])
            if RANK.get(pair["overall_risk"],0) > RANK.get(worst_risk,0):
                worst_risk = pair["overall_risk"]
        except Exception as e:
            print(f"[PharmaAI] Pair error: {e}")
            pairs.append({"drug_a":a_label,"drug_b":b_label,
                           "overall_risk":"UNKNOWN","interactions":[],
                           "error":str(e),"trace":traceback.format_exc()})

    shaped_drugs = []
    for entry in resolved:
        try:
            empty_intr = {"interactions":[],"summary":{},"overall_risk":"NONE",
                          "drugs_checked":[],"drugs_unresolved":[]}
            solo_report = build_report(entry["identity"], empty_intr, source_mode="text")
            shaped_drugs.append(_shape_drug(entry["identity"], entry["label"], solo_report, entry.get("ocr")))
        except Exception as e:
            print(f"[PharmaAI] shape drug error for {entry['label']}: {e}")
            shaped_drugs.append({"label":entry["label"],"brand_name":entry["label"],
                                  "error":str(e)})

    combined = {
        "overall_interaction_risk": worst_risk,
        "total_interactions":       total_iact,
        "drug_count":               len(shaped_drugs),
        "pair_count":               len(pairs),
        "recommendation":           _rec(worst_risk),
        "any_auth_issue":           any(d.get("verdict") not in ("AUTHENTIC","UNKNOWN") for d in shaped_drugs),
    }

    print(f"[PharmaAI] Done — worst_risk={worst_risk}  total_interactions={total_iact}")

    try:
        drug_str = ", ".join(d["label"] for d in shaped_drugs)
        checks_col.insert_one({
            "user_id":user["id"],"input_mode":"multi","drug_name":drug_str,
            "drug_count":len(shaped_drugs),"interaction_count":total_iact,
            "risk_level":worst_risk,"verdict":"SUSPICIOUS" if combined["any_auth_issue"] else "OK",
            "ip_address":request.client.host,"created_at":datetime.utcnow()})
        _fraud(user["id"], request.client.host, drug_str)
    except Exception as e:
        print(f"[PharmaAI] DB save warning: {e}")

    patient_explanation = generate_patient_explanation(shaped_drugs, pairs, combined)

    final_response = {
        "drugs":    shaped_drugs,
        "pairs":    pairs,
        "combined": combined,
        "errors":   errors,
        "patient_explanation": patient_explanation,
    }

    import json
    print("\n[PharmaAI] FINAL JSON RETURNED BY /api/analyze:")
    print(json.dumps(final_response, indent=2, default=str))

    return final_response

def _fraud(uid, ip, drug):
    try:
        w = datetime.utcnow().replace(minute=0,second=0,microsecond=0)
        k = {"ip_address":ip,"window_start":w}
        ex = ip_col.find_one(k)
        if ex:
            ds = set(ex.get("drug_set",[]))
            ds.add(drug or "")
            ip_col.update_one(k,{"$set":{"drug_set":list(ds)},"$inc":{"check_count":1}})
            if len(ds)>5:
                fraud_col.update_one({"ip_address":ip,"window_start":w},
                    {"$setOnInsert":{"user_id":uid,"ip_address":ip,"window_start":w,
                        "event_type":"burst","details":{"drugs":list(ds)},
                        "flagged_at":datetime.utcnow(),"reviewed":False}},upsert=True)
        else:
            ip_col.insert_one({**k,"drug_set":[drug or ""],"check_count":1})
    except: pass

@app.get("/api/history")
def history(limit:int=50, user=Depends(get_user)):
    rows = list(checks_col.find({"user_id":user["id"]}).sort("created_at",DESCENDING).limit(limit))
    for r in rows:
        r["id"]=str(r.pop("_id"))
        r["created_at"]=r["created_at"].isoformat() if r.get("created_at") else None
    return {"history":rows}

@app.get("/api/stats")
def stats(user=Depends(get_user)):
    uid=user["id"]
    def agg(grp,proj): return list(checks_col.aggregate([{"$match":{"user_id":uid}},{"$group":grp},{"$project":proj}]))
    verdicts = agg({"_id":"$verdict","count":{"$sum":1}},{"verdict":"$_id","count":1,"_id":0})
    risks    = agg({"_id":"$risk_level","count":{"$sum":1}},{"risk_level":"$_id","count":1,"_id":0})
    since    = datetime.utcnow()-timedelta(days=14)
    timeline = list(checks_col.aggregate([
        {"$match":{"user_id":uid,"created_at":{"$gte":since}}},
        {"$group":{"_id":{"$dateToString":{"format":"%Y-%m-%d","date":"$created_at"}},"count":{"$sum":1}}},
        {"$sort":{"_id":1}},{"$project":{"day":"$_id","count":1,"_id":0}}]))
    tp = list(checks_col.aggregate([{"$match":{"user_id":uid}},
        {"$group":{"_id":None,"total":{"$sum":1},"total_interactions":{"$sum":"$interaction_count"}}}]))
    totals = tp[0] if tp else {"total":0,"total_interactions":0}
    totals.pop("_id",None)
    return {"verdicts":verdicts,"risks":risks,"timeline":timeline,"totals":totals}

@app.get("/api/admin/fraud")
def fraud_log(user=Depends(get_user)):
    if user["role"]!="admin": raise HTTPException(403,"Admin only")
    rows=list(fraud_col.find().sort("flagged_at",DESCENDING).limit(100))
    for r in rows:
        r["id"]=str(r.pop("_id"))
        try:
            u=users_col.find_one({"_id":ObjectId(r["user_id"])},{"email":1})
            r["email"]=u["email"] if u else "—"
        except: r["email"]="—"
        r["flagged_at"]=r["flagged_at"].isoformat() if r.get("flagged_at") else None
        r["window_start"]=r["window_start"].isoformat() if r.get("window_start") else None
    return {"fraud_events":rows}

TRANSLATE_CHUNK_LIMIT = 950
TTS_CHUNK_LIMIT = 2200

def _chunk_text(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    import re
    sentences = re.split(r'(?<=[.!?])\s+|\n+', text)

    chunks = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(sentence) <= limit:
                current = sentence
            else:
                words = sentence.split(" ")
                piece = ""
                for word in words:
                    if len(word) > limit:
                        if piece:
                            chunks.append(piece)
                            piece = ""
                        for i in range(0, len(word), limit):
                            sub = word[i:i + limit]
                            if len(sub) == limit:
                                chunks.append(sub)
                            else:
                                piece = sub
                        continue
                    piece_candidate = f"{piece} {word}".strip() if piece else word
                    if len(piece_candidate) <= limit:
                        piece = piece_candidate
                    else:
                        if piece:
                            chunks.append(piece)
                        piece = word
                current = piece
    if current:
        chunks.append(current)
    return chunks


class TranslateRequest(BaseModel):
    text: str
    target_language: str

SUPPORTED_LANGUAGES = {"en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "bn-IN"}

@app.post("/api/sarvam/translate")
def translate_text(req: TranslateRequest, user = Depends(get_user)):
    if req.target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {req.target_language}")
    
    # ── DEEP-TRANSLATOR FALLBACK (Replacing Sarvam AI) ──
    try:
        # Map e.g. "hi-IN" to "hi"
        lang_code = req.target_language.split("-")[0]
        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source='auto', target=lang_code)
            chunks = _chunk_text(req.text, 4000) # deep-translator chunk limit
            if not chunks:
                return {"translated_text": ""}
            
            translated_parts = []
            for chunk in chunks:
                translated_parts.append(translator.translate(chunk))
                
            return {"translated_text": " ".join(translated_parts), "chunked": len(chunks) > 1}
        except ImportError:
            print("[PharmaAI] deep-translator not installed. Returning English fallback.")
            return {"translated_text": req.text, "chunked": False, "fallback_english": True}
    except Exception as e:
        print(f"[PharmaAI] Translation error: {e}")
        return {"translated_text": req.text, "chunked": False, "fallback_english": True}

    # ── ORIGINAL SARVAM AI CODE (ISOLATED/DISABLED) ──
    """
    if not sarvam_client:
        raise HTTPException(status_code=500, detail="Sarvam AI client not initialized. Check SARVAM_API_KEY.")
    try:
        chunks = _chunk_text(req.text, TRANSLATE_CHUNK_LIMIT)
        if not chunks:
            return {"translated_text": ""}

        translated_parts = []
        for chunk in chunks:
            res = sarvam_client.text.translate(
                input=chunk,
                source_language_code="en-IN",
                target_language_code=req.target_language
            )
            translated_parts.append(res.translated_text)

        return {"translated_text": " ".join(translated_parts), "chunked": len(chunks) > 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sarvam translation unavailable: {e}")
    """

class TTSRequest(BaseModel):
    text: str
    language: str
    medicine_id: str | None = None

import re as _re_cache

def _safe_cache_key(raw: str) -> str:
    key = (raw or "").strip().lower()
    key = _re_cache.sub(r"[^a-z0-9_-]+", "_", key).strip("_")
    return key or "unknown_medicine"

def _audio_cache_path(medicine_key: str, language: str) -> Path:
    return Path(AUDIO_CACHE_DIR) / medicine_key / f"{language}.wav"

def _audio_cache_get(medicine_key: str, language: str) -> str | None:
    path = _audio_cache_path(medicine_key, language)
    if not path.exists():
        return None
    try:
        data = path.read_bytes()
        os.utime(path, None)
        import base64 as _b64
        return _b64.b64encode(data).decode("utf-8")
    except Exception as e:
        print(f"[PharmaAI] Audio cache read warning ({path}): {e}")
        return None

def _audio_cache_put(medicine_key: str, language: str, audio_b64: str) -> None:
    path = _audio_cache_path(medicine_key, language)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import base64 as _b64
        path.write_bytes(_b64.b64decode(audio_b64))
        _audio_cache_evict_if_over_limit()
    except Exception as e:
        print(f"[PharmaAI] Audio cache write warning ({path}): {e}")

def _audio_cache_evict_if_over_limit() -> None:
    try:
        files = []
        total = 0
        for root, _dirs, names in os.walk(AUDIO_CACHE_DIR):
            for name in names:
                fp = Path(root) / name
                try:
                    st = fp.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, fp))
                total += st.st_size

        if total <= AUDIO_CACHE_MAX_BYTES:
            return

        files.sort(key=lambda f: f[0])
        for _mtime, size, fp in files:
            if total <= AUDIO_CACHE_MAX_BYTES:
                break
            try:
                fp.unlink()
                total -= size
                print(f"[PharmaAI] Audio cache evicted (LRU, over {AUDIO_CACHE_MAX_BYTES // (1024*1024)}MB cap): {fp}")
            except OSError:
                pass

        for root, dirs, names in os.walk(AUDIO_CACHE_DIR, topdown=False):
            if root == AUDIO_CACHE_DIR:
                continue
            if not names and not dirs:
                try:
                    os.rmdir(root)
                except OSError:
                    pass
    except Exception as e:
        print(f"[PharmaAI] Audio cache eviction warning: {e}")

@app.post("/api/sarvam/tts")
def text_to_speech(req: TTSRequest, user = Depends(get_user)):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="No text provided for TTS")
    if req.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {req.language}")

    if req.medicine_id and req.medicine_id.strip():
        cache_key = _safe_cache_key(req.medicine_id)
    else:
        import hashlib
        cache_key = "text_" + hashlib.sha1(req.text.strip().encode("utf-8")).hexdigest()[:16]

    cached = _audio_cache_get(cache_key, req.language)
    if cached:
        return {"audio": cached, "chunked": False, "cached": True}

    # ── BROWSER TTS FALLBACK (Replacing Sarvam AI) ──
    return {
        "audio": None,
        "tts_fallback": True,
        "fallback_reason": "quota_exhausted",
        "message": "Voice explanation is using browser fallback."
    }

    # ── ORIGINAL SARVAM AI CODE (ISOLATED/DISABLED) ──
    """
    if not sarvam_client:
        raise HTTPException(status_code=500, detail="Sarvam AI client not initialized. Check SARVAM_API_KEY.")

    try:
        speaker_map = {
            "en-IN": "priya",
            "hi-IN": "priya",
            "ta-IN": "kavitha",
            "te-IN": "suhani",
            "kn-IN": "suhani",
            "ml-IN": "suhani",
            "mr-IN": "priya",
            "bn-IN": "priya"
        }
        speaker = speaker_map.get(req.language, "priya")

        chunks = _chunk_text(req.text, TTS_CHUNK_LIMIT)
        if not chunks:
            raise ValueError("No text provided for TTS")

        audio_clips_b64 = []
        for chunk in chunks:
            res = sarvam_client.text_to_speech.convert(
                text=chunk,
                target_language_code=req.language,
                speaker=speaker,
                model="bulbul:v3",
                output_audio_codec="wav"
            )
            if not res.audios:
                raise ValueError("No audio returned from Sarvam TTS")
            audio_clips_b64.append(res.audios[0])

        final_b64 = audio_clips_b64[0] if len(audio_clips_b64) == 1 else _merge_wav_clips_b64(audio_clips_b64)
        _audio_cache_put(cache_key, req.language, final_b64)

        return {"audio": final_b64, "chunked": len(audio_clips_b64) > 1, "cached": False}
    except HTTPException:
        raise
    except Exception as e:
        # ── CHANGED: graceful fallback instead of raising 502 ──────────
        # Log the real error server-side; return a structured JSON response
        # so the frontend can silently switch to browser Web Speech API.
        # Verified medicine info was already returned by /api/analyze —
        # only voice is affected here.
        err_str = str(e)
        print(f"[PharmaAI] Sarvam TTS unavailable (will use browser fallback): {err_str}")

        if "insufficient_quota" in err_str or "402" in err_str:
            reason = "quota_exhausted"
        elif "401" in err_str or "invalid" in err_str.lower() and "key" in err_str.lower():
            reason = "invalid_key"
        elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
            reason = "timeout"
        else:
            reason = "network_error"

        return {
            "audio": None,
            "tts_fallback": True,
            "fallback_reason": reason,
            "message": "Voice explanation is temporarily unavailable. You can read the explanation below."
        }
    """


def _merge_wav_clips_b64(clips_b64: list[str]) -> str:
    import base64, io, wave

    if len(clips_b64) == 1:
        return clips_b64[0]

    frames = []
    params = None
    for clip_b64 in clips_b64:
        raw = base64.b64decode(clip_b64)
        with wave.open(io.BytesIO(raw), "rb") as wf:
            if params is None:
                params = wf.getparams()
            frames.append(wf.readframes(wf.getnframes()))

    out_buffer = io.BytesIO()
    with wave.open(out_buffer, "wb") as out_wf:
        out_wf.setparams(params)
        for f in frames:
            out_wf.writeframes(f)

    return base64.b64encode(out_buffer.getvalue()).decode("utf-8")

app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")