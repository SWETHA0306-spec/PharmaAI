"""
ocr.py — Drug Label OCR via OpenAI-compatible API
==================================================
Works with OpenRouter, Groq, Gemini, OpenAI, Claude, or any
OpenAI-compatible endpoint. Change the 4 lines in CONFIG and done.

NOTE: This module is OCR-only. It extracts raw text from a medicine
strip/label image and does not guess, infer, or fill in fields that
are not visibly printed. Sarvam AI is NOT used here — Sarvam AI is
used later in the pipeline (api_bridge.py) only to turn ALREADY
VERIFIED medicine data into a patient-friendly explanation, and to
translate/voice that explanation. See api_bridge.py for that flow.

Install:  pip install pillow requests opencv-python
"""

# ══════════════════════════════════════════════════════════════
#  CONFIG — only edit this block
# ══════════════════════════════════════════════════════════════

IMAGE_PATH = r"C:\Users\SWETHA P\Downloads\files\zipppp\drug_ai\new!.jpeg"

# API_KEY is read from the OCR_API_KEY environment variable (see .env.example).
# No key is hardcoded here so a key never ends up committed to source control.
API_URL   = "https://openrouter.ai/api/v1/chat/completions"
API_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"

# Two-pass mode: runs a second strict pass to verify/correct the first result.
# Costs one extra API call but significantly improves accuracy on hard images.
# Set False to disable if your API has tight rate limits.
TWO_PASS = True

# ══════════════════════════════════════════════════════════════
#  END CONFIG
# ══════════════════════════════════════════════════════════════

import os, re, sys, base64, json, io

# Read from environment (see .env.example). A missing key fails loudly with
# a clear error instead of silently using a shared/expired key baked into
# source.
API_KEY = os.environ.get("OCR_API_KEY", "")

import urllib.parse
_provider = urllib.parse.urlparse(API_URL).netloc
print(f"[PharmaAI] OCR Provider: {_provider}")
print(f"[PharmaAI] OCR Key Loaded: {bool(API_KEY)}")

try:    from PIL import Image as PILImage, ImageOps, ImageEnhance
except: print("pip install pillow"); sys.exit(1)
try:    import requests
except: print("pip install requests"); sys.exit(1)

CV2_OK = False
try:    import cv2, numpy as np; CV2_OK = True
except: pass


# ══════════════════════════════════════════════════════════════
#  PROMPTS
# ══════════════════════════════════════════════════════════════

# Pass 1 — full structured extraction
PROMPT_EXTRACT = """You are a pharmaceutical label OCR specialist. Study every part of this medicine packaging image carefully, including text that is rotated, sideways, upside-down, or printed at an angle.

Your task: extract every field listed below by reading the ACTUAL text in the image.

RULES — breaking any rule makes your answer wrong:
- Copy text CHARACTER BY CHARACTER exactly as printed. No spelling corrections.
- Numbers (batch no, dates, MRP, licence): copy digit by digit. Never round or invent.
- The brand name is usually the LARGEST text on the label. Do not substitute another drug name.
- If a field is genuinely not visible, return null. Never guess or fill in from memory.
- Return ONLY a valid JSON object. No markdown, no explanation, no extra text.

JSON to return:
{
  "brand_name":   "trade/brand name — largest text on label",
  "generic_name": "INN/chemical name exactly as printed",
  "dosage":       "strength exactly as printed e.g. 500 mg",
  "dosage_form":  "Tablets / Capsules / Syrup / Injection etc.",
  "batch_no":     "batch or lot number exactly as printed",
  "mfg_date":     "manufacturing date exactly as printed",
  "exp_date":     "expiry date exactly as printed",
  "manufacturer": "full company name and address as printed",
  "license_no":   "drug / manufacturing licence number",
  "mrp":          "MRP or price exactly as printed",
  "pack_size":    "pack quantity e.g. 10 Tablets, 2×10",
  "storage":      "storage instructions",
  "raw_text":     "ALL visible text on the label, verbatim, one line per entry"
}"""

# Pass 2 — verification: given Pass 1 output, re-examine the image and correct errors
def _prompt_verify(pass1: dict) -> str:
    return f"""You are a pharmaceutical label OCR verifier. You are given a FIRST-PASS reading of a medicine label, and the original image.

Your job: look at the image again very carefully and correct any errors in the first-pass reading.
Pay special attention to:
- Text that is rotated, sideways, or upside-down
- Numbers that may have been misread (0 vs O, 1 vs I, 5 vs S)
- Brand name: must be the LARGEST text on label — not a guess
- Dates and batch numbers: copy digit by digit

First-pass reading (may contain errors):
{json.dumps(pass1, indent=2)}

Return a corrected JSON object using the exact same keys.
Fix any field that is wrong. If a field in the first pass looks correct, keep it unchanged.
Return ONLY valid JSON. No explanation, no markdown.
"""


# ══════════════════════════════════════════════════════════════
#  IMAGE PREPROCESSING
# ══════════════════════════════════════════════════════════════

def preprocess(image_path: str) -> str:
    img = PILImage.open(image_path)
    try:    img = ImageOps.exif_transpose(img)
    except: pass
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    if min(w, h) < 1400:
        s = 1400 / min(w, h)
        img = img.resize((int(w * s), int(h * s)), PILImage.LANCZOS)

    if CV2_OK:
        c = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 248, 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), iterations=2)
        c = cv2.inpaint(c, mask, 7, cv2.INPAINT_TELEA)
        lab = cv2.cvtColor(c, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(l)
        c = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        blur = cv2.GaussianBlur(c, (0, 0), 2)
        c = cv2.addWeighted(c, 1.5, blur, -0.5, 0)
        img = PILImage.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))
    else:
        img = ImageEnhance.Contrast(img).enhance(1.5)
        img = ImageEnhance.Sharpness(img).enhance(2.5)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode()


# ══════════════════════════════════════════════════════════════
#  API CALL
# ══════════════════════════════════════════════════════════════

def _call_api(messages: list) -> str:
    if "anthropic.com" in API_URL:
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         API_KEY,
            "anthropic-version": "2023-06-01",
        }
        anthropic_msgs = []
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                anthropic_msgs.append({"role": m["role"], "content": content})
            else:
                parts = []
                for part in content:
                    if part["type"] == "text":
                        parts.append({"type": "text", "text": part["text"]})
                    elif part["type"] == "image_url":
                        data = part["image_url"]["url"].split(",", 1)[1]
                        parts.append({"type": "image",
                                      "source": {"type": "base64",
                                                 "media_type": "image/jpeg",
                                                 "data": data}})
                anthropic_msgs.append({"role": m["role"], "content": parts})

        payload = {"model": API_MODEL, "max_tokens": 2048, "messages": anthropic_msgs}
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=90)
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    else:
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {API_KEY}",
        }
        payload = {"model": API_MODEL, "max_tokens": 2048, "messages": messages}
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=90)

        if resp.status_code == 401:
            raise ValueError("OCR authentication failed. Check OCR_API_KEY")
        if resp.status_code == 429:
            raise ValueError("OCR quota exhausted")
        if resp.status_code >= 500:
            raise ValueError("OCR service temporarily unavailable")
        if not resp.ok:
            raise ValueError(f"HTTP {resp.status_code}: {resp.text[:400]}")

        return resp.json()["choices"][0]["message"]["content"]


def _messages_for_image(prompt: str, b64: str) -> list:
    return [{"role": "user", "content": [
        {"type": "text",      "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}]


# ══════════════════════════════════════════════════════════════
#  JSON PARSING
# ══════════════════════════════════════════════════════════════

def _parse_json(text: str) -> dict | None:
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$',          '', text.strip(), flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:    return json.loads(m.group())
            except: pass
    return None


def _regex_fallback(text: str) -> dict:
    def find(*pats):
        for p in pats:
            m = re.search(p, text, re.I)
            if m: return m.group(1).strip()
        return None
    return {
        "brand_name":   find(r'brand\s*(?:name)?\s*[:\-]\s*(.+)'),
        "generic_name": find(r'generic\s*(?:name)?\s*[:\-]\s*(.+)'),
        "dosage":       find(r'(\d+\s*(?:mg|mcg|ml|g)\b)'),
        "dosage_form":  find(r'(tablets?|capsules?|syrup|injection)', ),
        "batch_no":     find(r'(?:batch|lot)\s*(?:no|#)?\s*[:\-]?\s*([A-Z0-9]{4,})'),
        "mfg_date":     find(r'mf[gd]\.?\s*(?:date)?\s*[:\-]?\s*(\d[\d./\-]{3,})'),
        "exp_date":     find(r'exp(?:iry)?\s*(?:date)?\s*[:\-]?\s*(\d[\d./\-]{3,})'),
        "manufacturer": None,
        "license_no":   None,
        "mrp":          find(r'mrp\s*[:\-]?\s*(rs\.?\s*[\d.,]+)'),
        "pack_size":    None,
        "storage":      None,
        "raw_text":     text,
    }


def _normalise_raw_text(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, list):
        return "\n".join(str(v) for v in val if v)
    return str(val)


def _to_result(parsed: dict, engine_label: str) -> dict:
    fields = ["brand_name","generic_name","dosage","dosage_form","batch_no",
              "mfg_date","exp_date","manufacturer","license_no","mrp","pack_size",
              "storage"]
    result = {"success": True, "engine": engine_label,
              **{f: parsed.get(f) for f in fields}}
    result["raw_text"] = _normalise_raw_text(parsed.get("raw_text"))
    return result


# ══════════════════════════════════════════════════════════════
#  MAIN OCR LOGIC
# ══════════════════════════════════════════════════════════════

def run_ocr(image_path: str) -> dict:
    print(f"\n  Image  : {image_path}")
    if not os.path.exists(image_path):
        return {"success": False, "error": f"File not found: {image_path}"}

    if not API_KEY:
        return {"success": False,
                "error": "OCR API key not configured"}

    print("  Step 1 : Preprocessing image...")
    try:
        b64 = preprocess(image_path)
        print("  ✓ Done.")
    except Exception as e:
        return {"success": False, "error": f"Image load failed: {e}"}

    print(f"\n  Step 2 : Pass 1 — extraction  [{API_MODEL}]")
    try:
        raw1  = _call_api(_messages_for_image(PROMPT_EXTRACT, b64))
        pass1 = _parse_json(raw1)
    except Exception as e:
        return {"success": False, "error": f"API error: {e}"}

    if not pass1:
        print("  ⚠ Pass 1 returned plain text — using regex fallback.")
        return _to_result(_regex_fallback(raw1), f"{API_MODEL} (regex-fallback)")

    print("  ✓ Pass 1 done.")

    if TWO_PASS:
        print(f"\n  Step 3 : Pass 2 — verification  [{API_MODEL}]")
        try:
            verify_msgs = _messages_for_image(_prompt_verify(pass1), b64)
            raw2  = _call_api(verify_msgs)
            pass2 = _parse_json(raw2)
            if pass2:
                for k, v in pass2.items():
                    if v and v != pass1.get(k):
                        print(f"    ↳ corrected '{k}': {pass1.get(k)!r}  →  {v!r}")
                        pass1[k] = v
                print("  ✓ Pass 2 done.")
            else:
                print("  ⚠ Pass 2 unparseable — keeping Pass 1 result.")
        except Exception as e:
            print(f"  ⚠ Pass 2 failed ({e}) — keeping Pass 1 result.")

    label = f"{API_MODEL}" + (" (2-pass)" if TWO_PASS else "")
    return _to_result(pass1, label)


# ══════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════

def print_result(r: dict, path: str):
    SEP = "=" * 55
    print(f"\n{SEP}\n  OCR RESULTS\n{SEP}")
    print(f"  Image   : {path}")
    if not r.get("success"):
        print(f"\n  ✗ FAILED: {r.get('error')}")
        return

    print(f"  Engine  : {r.get('engine')}\n")

    FIELDS = [
        ("brand_name",   "Brand name  "),
        ("generic_name", "Generic name"),
        ("dosage",       "Dosage      "),
        ("dosage_form",  "Dosage form "),
        ("batch_no",     "Batch No    "),
        ("mfg_date",     "Mfg date    "),
        ("exp_date",     "Exp date    "),
        ("manufacturer", "Manufacturer"),
        ("license_no",   "License No  "),
        ("mrp",          "MRP         "),
        ("pack_size",    "Pack size   "),
        ("storage",      "Storage     "),
    ]
    for key, label in FIELDS:
        print(f"  {label} : {r.get(key) or '—'}")

    raw = (r.get("raw_text") or "").strip()
    if raw:
        print(f"\n  Raw text:\n  {'─'*45}")
        for line in raw.split("\n"):
            if line.strip():
                print(f"  {line}")
        print(f"  {'─'*45}")

    name = r.get("brand_name") or r.get("generic_name")
    if name:
        print(f"\n  💡 Interactions check → enter: {name}")
    print()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else IMAGE_PATH
    result = run_ocr(path)
    print_result(result, path)
