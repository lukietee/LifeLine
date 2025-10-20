# app.py
# Lifeline – FastAPI backend with Twilio voice flow and Bedrock (mockable)

import os, json, re
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import FastAPI, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import boto3

# ---------------- Config ----------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "anthropic.claude-3-haiku-20240307-v1:0")
MOCK_BEDROCK = os.getenv("MOCK_BEDROCK", "0") == "1"   # set to "1" to demo w/o AWS creds

# Only create Bedrock client when not mocking
bedrock = None
if not MOCK_BEDROCK:
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

# ---------------- App + CORS -------------
app = FastAPI(title="Lifeline API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten to your frontend domain when deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- In-memory stores -------
INCIDENTS: List[Dict] = []            # dashboard data
CALLS: Dict[str, Dict] = {}           # active Twilio call sessions (CallSid -> state)


# ---------------- Utilities --------------
def _extract_int_from_speech(s: str, default: int = 1) -> int:
    s = (s or "").lower()
    m = re.search(r"\b(\d+)\b", s)
    if m:
        return int(m.group(1))
    words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    for w, n in words.items():
        if w in s:
            return n
    return default


def summarize_with_bedrock(transcript: str) -> Dict:
    """
    Send transcript to Claude 3 Haiku on Bedrock and parse strict JSON.
    If MOCK_BEDROCK=1, return a heuristic mock (no AWS required).
    """
    if MOCK_BEDROCK:
        t = transcript.lower()
        etype = "other"
        if any(k in t for k in ["fire", "smoke", "burn"]): etype = "fire"
        elif any(k in t for k in ["crash", "accident", "car", "highway", "traffic"]): etype = "traffic"
        elif any(k in t for k in ["robbery", "theft", "gun", "assault"]): etype = "crime"
        elif any(k in t for k in ["hurt", "injur", "bleed", "ambulance", "medical"]): etype = "medical"

        return {
            "emergency_type": etype,
            "location": "unknown",
            "people_involved": _extract_int_from_speech(t, 1),
            "severity": "high" if any(k in t for k in ["fire", "gun", "bleeding", "unconscious", "not breathing"]) else "medium",
            "summary": (transcript[:180] + "...") if len(transcript) > 180 else transcript,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    prompt = f"""
You are a 911 emergency triage assistant. Extract key fields from the transcript.

Transcript:
\"\"\"{transcript}\"\"\"

Return STRICT JSON only:
{{
  "emergency_type": "fire|medical|crime|traffic|other",
  "location": "address/landmark or 'unknown'",
  "people_involved": <integer>,
  "severity": "low|medium|high",
  "summary": "<=25 words concise summary"
}}
"""
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "temperature": 0,
        "messages": [{"role":"user","content":[{"type":"text","text":prompt}]}],
    }
    resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(body))  # type: ignore[arg-type]
    payload = json.loads(resp["body"].read())
    text = payload["content"][0]["text"]

    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return {
            "emergency_type": "other",
            "location": "unknown",
            "people_involved": 0,
            "severity": "medium",
            "summary": text.strip()[:200],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    data = json.loads(m.group(0))
    data.setdefault("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return data


# -------------- REST: dashboard ----------
class AnalyzeReq(BaseModel):
    transcript: str

@app.get("/health")
def health(): return {"ok": True, "mode": "mock" if MOCK_BEDROCK else "bedrock"}

@app.get("/incidents")
def list_incidents(): return list(reversed(INCIDENTS))  # newest first

@app.post("/analyze")
def analyze(req: AnalyzeReq):
    res = summarize_with_bedrock(req.transcript)
    res["id"] = len(INCIDENTS) + 1
    INCIDENTS.append(res)
    return res


# -------------- Twilio Voice flow --------
@app.post("/voice", response_class=PlainTextResponse)
def voice_entry():
    """
    First webhook from Twilio. Starts scripted multi-turn collection.
    """
    from twilio.twiml.voice_response import VoiceResponse, Gather
    vr = VoiceResponse()
    g = Gather(input="speech", action="/gather", method="POST", timeout=7)
    g.say("This is Lifeline. I will collect details to help dispatch responders.")
    g.say("First, what is the address or nearest cross street?")
    vr.append(g)
    # If silence, re-ask:
    vr.redirect("/voice", method="POST")
    return str(vr)

@app.post("/gather", response_class=PlainTextResponse)
async def gather(
    request: Request,
    CallSid: str = Form(...),
    SpeechResult: Optional[str] = Form(None),
):
    """
    Handles each Gather response and advances the script.
    """
    from twilio.twiml.voice_response import VoiceResponse, Gather

    sess = CALLS.get(CallSid) or {"step": 0, "answers": {}, "transcript": []}
    CALLS[CallSid] = sess

    text = (SpeechResult or "").strip()
    if text:
        sess["transcript"].append(text)

    vr = VoiceResponse()

    # Step 0: location
    if sess["step"] == 0:
        if text:
            sess["answers"]["location"] = text
            sess["step"] = 1
        g = Gather(input="speech", action="/gather", method="POST", timeout=7)
        g.say("Briefly describe what happened.")
        vr.append(g)
        vr.redirect("/gather", method="POST")
        return str(vr)

    # Step 1: description
    if sess["step"] == 1:
        if text:
            sess["answers"]["description"] = text
            sess["step"] = 2
        g = Gather(input="speech", action="/gather", method="POST", timeout=7)
        g.say("How many people need help? Say a number.")
        vr.append(g)
        vr.redirect("/gather", method="POST")
        return str(vr)

    # Step 2: people
    if sess["step"] == 2:
        if text:
            sess["answers"]["people"] = _extract_int_from_speech(text, 1)
            sess["step"] = 3
        g = Gather(input="speech", action="/gather", method="POST", timeout=7)
        g.say("Is anyone in immediate danger? Please say yes or no.")
        vr.append(g)
        vr.redirect("/gather", method="POST")
        return str(vr)

    # Step 3: immediate danger -> finalize
    if sess["step"] == 3:
        danger = (text.lower() if text else "")
        sess["answers"]["danger"] = "yes" in danger
        sess["step"] = 4  # done

        # Build one transcript string for the model
        transcript = (
            f"Location: {sess['answers'].get('location','unknown')}. "
            f"Description: {sess['answers'].get('description','')}. "
            f"People: {sess['answers'].get('people',1)}. "
            f"Immediate danger: {'yes' if sess['answers'].get('danger') else 'no'}."
        )

        result = summarize_with_bedrock(transcript)
        result["id"] = len(INCIDENTS) + 1
        # Patch with explicit answers if model missed something
        result.setdefault("location", sess["answers"].get("location", "unknown"))
        result.setdefault("people_involved", sess["answers"].get("people", 1))
        if "severity" not in result:
            result["severity"] = "high" if sess["answers"].get("danger") else "medium"
        INCIDENTS.append(result)

        vr.say("Thank you. We have your location and details. Help is being dispatched now.")
        vr.hangup()

        CALLS.pop(CallSid, None)  # cleanup
        return str(vr)

    # Fallback
    vr.say("Sorry, I didn't catch that.")
    vr.redirect("/voice", method="POST")
    return str(vr)
