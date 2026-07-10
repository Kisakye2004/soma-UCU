from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, Text, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from cryptography.fernet import Fernet
import os
import base64

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Groq AI client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:root@localhost:5432/soma_db")
engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

# Admin password
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123")

# Encryption
encryption_key_env = os.environ.get("ENCRYPTION_KEY")
if encryption_key_env:
    ENCRYPTION_KEY = base64.b64decode(encryption_key_env)
else:
    with open("encryption.key", "rb") as f:
        ENCRYPTION_KEY = f.read()
fernet = Fernet(ENCRYPTION_KEY)

def encrypt(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt(text: str) -> str:
    try:
        return fernet.decrypt(text.encode()).decode()
    except:
        return text

# Responses
FALLBACK_RESPONSE = (
    "I am sorry, I am temporarily unavailable right now. "
    "Please try again in a few minutes. If you need immediate support "
    "please contact UCU Counselling Department or call Butabika National "
    "Referral Hospital on 0414 305 000 or the Uganda Mental Health Support "
    "Helpline on 0800 990 000. You do not have to face this alone."
)

CRISIS_RESPONSE = (
    "I am very concerned about you right now. What you are feeling matters deeply. "
    "Please reach out to UCU Counselling Department immediately. "
    "You can also call Butabika National Referral Hospital on 0414 305 000 "
    "or the Uganda Mental Health Support Helpline on 0800 990 000. "
    "You do not have to face this alone."
)

CLOSING_RESPONSE = (
    "Thank you for trusting SOMA today. Remember, reaching out is a sign of strength. "
    "UCU Counselling Department is always available if you need to speak to someone. "
    "You can also call Butabika National Referral Hospital on 0414 305 000 "
    "or the Uganda Mental Health Support Helpline on 0800 990 000. "
    "Take care of yourself and do not hesitate to come back anytime. Goodbye and be well."
)

CLOSING_WORDS = [
    "bye", "goodbye", "thank you", "thanks", "see you",
    "good night", "goodnight", "ok thanks", "okay thanks"
]

SELF_HARM_WORDS = [
    "kill myself",
    "want to die",
    "end my life",
    "suicide",
    "hurt myself",
    "cut myself",
    "take my own life",
    "dont want to live",
    "no reason to live"
]

# Database model
class ConversationLog(Base):
    __tablename__ = "conversation_logs"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Text, nullable=False)
    user_message = Column(Text, nullable=False)
    soma_response = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_crisis = Column(Text, default="no")

Base.metadata.create_all(bind=engine)

# Request models
class Message(BaseModel):
    text: str
    session_id: str = None
    history: list = []

class AdminLogin(BaseModel):
    password: str

# Serve student chat interface
@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html") as f:
        return f.read()

# Serve admin dashboard
@app.get("/admin", response_class=HTMLResponse)
def admin():
    with open("admin.html") as f:
        return f.read()

# Admin login
@app.post("/admin/login")
def admin_login(credentials: AdminLogin):
    if credentials.password == ADMIN_PASSWORD:
        return {"success": True}
    raise HTTPException(status_code=401, detail="Incorrect password")

# Get all conversations for admin
@app.get("/admin/conversations")
def get_conversations(password: str):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    db = SessionLocal()
    logs = db.query(ConversationLog).order_by(
        ConversationLog.session_id,
        ConversationLog.timestamp
    ).all()

    total = db.query(func.count(ConversationLog.id)).scalar()
    crisis_total = db.query(func.count(ConversationLog.id)).filter(
        ConversationLog.is_crisis == "yes"
    ).scalar()

    students = {}
    for log in logs:
        sid = log.session_id
        if sid not in students:
            students[sid] = {
                "session_id": sid,
                "first_seen": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "last_seen": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "total_messages": 0,
                "crisis_detected": False,
                "messages": []
            }
        students[sid]["last_seen"] = log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        students[sid]["total_messages"] += 1
        if log.is_crisis == "yes":
            students[sid]["crisis_detected"] = True
        students[sid]["messages"].append({
            "id": log.id,
            "user_message": decrypt(log.user_message),
            "soma_response": decrypt(log.soma_response),
            "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "is_crisis": log.is_crisis
        })

    db.close()

    return {
        "total_conversations": total,
        "total_crisis": crisis_total,
        "total_students": len(students),
        "students": list(students.values())
    }

# Delete single student conversations
@app.delete("/admin/delete/{session_id}")
def delete_student(session_id: str, password: str):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    db = SessionLocal()
    db.query(ConversationLog).filter(ConversationLog.session_id == session_id).delete()
    db.commit()
    db.close()
    return {"success": True}

# Delete all conversations
@app.delete("/admin/delete-all")
def delete_all(password: str):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    db = SessionLocal()
    db.query(ConversationLog).delete()
    db.commit()
    db.close()
    return {"success": True}

# Main chat endpoint
@app.post("/chat")
def chat(message: Message):
    session_id = message.session_id

    if any(word in message.text.lower() for word in CLOSING_WORDS):
        db = SessionLocal()
        log = ConversationLog(
            session_id=session_id,
            user_message=encrypt(message.text),
            soma_response=encrypt(CLOSING_RESPONSE),
            is_crisis="no"
        )
        db.add(log)
        db.commit()
        db.close()
        return {
            "response": CLOSING_RESPONSE,
            "session_id": session_id,
            "closing": True
        }

    if any(word in message.text.lower() for word in SELF_HARM_WORDS):
        db = SessionLocal()
        log = ConversationLog(
            session_id=session_id,
            user_message=encrypt(message.text),
            soma_response=encrypt(CRISIS_RESPONSE),
            is_crisis="yes"
        )
        db.add(log)
        db.commit()
        db.close()
        return {
            "response": CRISIS_RESPONSE,
            "session_id": session_id,
            "closing": False
        }

    conversation_history = [
        {
            "role": "system",
            "content": """You are SOMA, a warm and compassionate mental health support chatbot for students at Uganda Christian University (UCU) in Uganda.

Your personality:
- You are warm, gentle, patient and non-judgmental
- You always acknowledge the student's feelings first before anything else
- You speak simply and clearly, avoiding complicated medical language
- You are culturally sensitive to Ugandan university students
- You understand that students may express frustration using strong language like 'I want to kill him' which means anger not literal intent

Your rules:
- Always respond to the emotion behind the message first
- Ask one gentle follow up question to understand more
- Never diagnose any mental health condition
- Never prescribe or suggest medication
- Only refer to UCU Counselling when the student is clearly in serious distress
- Never be preachy or lecture the student
- Keep responses between 3 to 5 sentences
- If a student expresses anger toward another person focus on their feelings not the other person
- Understand common Ugandan student struggles like academic pressure relationship problems family expectations financial stress and loneliness
- Remember what the student has told you earlier in the conversation and refer back to it naturally

You are a support tool only. You are not a replacement for professional counselling."""
        }
    ]

    for entry in message.history:
        conversation_history.append({
            "role": entry["role"],
            "content": entry["content"]
        })

    conversation_history.append({
        "role": "user",
        "content": message.text
    })

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=conversation_history,
            max_tokens=300
        )
        soma_response = response.choices[0].message.content
    except Exception:
        soma_response = FALLBACK_RESPONSE

    db = SessionLocal()
    log = ConversationLog(
        session_id=session_id,
        user_message=encrypt(message.text),
        soma_response=encrypt(soma_response),
        is_crisis="no"
    )
    db.add(log)
    db.commit()
    db.close()

    return {
        "response": soma_response,
        "session_id": session_id,
        "closing": False
    }
