# app.py
import os, json, re
from typing import List, Dict
from fastapi import FastAPI, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import boto3

# ------------- Config -------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "anthropic.claude-3-haiku-20240307-v1:0")

# If you haven't finished AWS activation yet and want to demo quickly,
# set MOCK_BEDROCK=1 in your env to bypass Bedrock and return a fake summary.
MOCK_BEDROCK = os.getenv("MOCK_BEDROCK", "0") == "1"

# ------------- Clients -------------
if not MOCK_BEDROCK:
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

# ------------- App + CORS -------------
app = FastAPI(title="Lifeline API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict to your Lovable app domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------- In-memory store (demo) -------------
INCIDENTS: List[Dict] = []


# ------------- Bedrock helper -------------
def summarize_with_bedrock(transcript: str) -> Dict:
    """
    Sends the transcript to Claude 3 Haiku on Bedrock and parses strict JSON back.
    If MOCK_BEDROCK=1, returns a deterministic mock response for demo.
    """
    if MOCK_BEDROCK:
        # Very simple heuristic to keep your demo unblocked
        t = transcript.lower()
        etype = "other"
        if any(w in t for w in ["fire", "smoke", "burn"]):
            etype = "fire"
        elif any(w in t for w in ["hurt", "injur", "bleed", "ambulance", "medical"]):
            etype = "medical"
        elif any(w in t for w in ["robbery", "theft", "gun", "assault", "crime"]):
            etype = "crime"
        elif any(w in t for w in ["crash", "accident", "car", "highway", "traffic"]):
            etype = "traffic"

        return {
            "emergency_type": etype,
            "location": "unknown",
            "people_involved": 1,
            "severity": "high" if "fire" in t or "gun" in t else "medium",
            "summary": transcript[:120]
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
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    text = payload["content"][0]["text"]

    # Extract the JSON block safely
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        # Fallback to a minimal shape if model didn't return strict JSON
        return {
            "emergency_type": "other",
            "location": "unknown",
            "people_involved": 0,
            "severity": "medium",
            "summary": text.strip()[:200]
        }
    return json.loads(m.group(0))


# ------------- API for your Lovable frontend -------------
@app.get("/incidents")
def list_incidents():
    """Return newest-first incidents for the dashboard."""
    return list(reversed(INCIDENTS))


class AnalyzeReq(BaseModel):
    transcript: str


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    """Directly analyze free-text (useful for testing without calls)."""
    result = summarize_with_bedrock(req.transcript)
    result["id"] = len(INCIDENTS) + 1
    INCIDENTS.append(result)
    return result


# ------------- Twilio Voice webhooks (phone flow) -------------
# IMPORTANT: You must install twilio (`pip install twilio`) and set your Twilio number's webhook.

@app.post("/voice")
def voice_entry():
    """
    Initial Twilio webhook. Plays a prompt and gathers speech.
    Configure your Twilio phone number Voice webhook to POST here.
    """
    from twilio.twiml.voice_response import VoiceResponse, Gather
    vr = VoiceResponse()
    vr.say("This is Lifeline. Please describe your emergency after the tone.")
    g = Gather(input="speech", action="/handoff", method="POST", timeout=6)
    vr.append(g)
    # If silence or no speech detected, reprompt:
    vr.redirect("/voice")
    return str(vr)


@app.post("/handoff")
async def handoff(request: Request, SpeechResult: str = Form(None)):
    """
    Twilio posts back recognized speech text to this endpoint.
    We call Bedrock, store the incident, and end the call.
    """
    transcript = SpeechResult or ""
    if transcript.strip():
        result = summarize_with_bedrock(transcript)
        result["id"] = len(INCIDENTS) + 1
        INCIDENTS.append(result)

    from twilio.twiml.voice_response import VoiceResponse
    vr = VoiceResponse()
    vr.say("Thank you. We have captured your information.")
    vr.hangup()
    return str(vr)
