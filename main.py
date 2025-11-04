import os
import base64
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId
from starlette.responses import Response

from database import db, create_document, get_documents
from schemas import Conversation as ConversationSchema, Message as MessageSchema

# Optional OpenAI integration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    attachments: Optional[List[dict]] = None

class ChatReply(BaseModel):
    reply: str


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None

class ConversationResponse(BaseModel):
    id: str
    title: str

class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    attachments: Optional[List[str]] = None
    created_at: Optional[str] = None



def to_str_id(doc):
    doc = dict(doc)
    if doc.get("_id"):
        doc["id"] = str(doc.pop("_id"))
    if doc.get("conversation_id") and isinstance(doc["conversation_id"], ObjectId):
        doc["conversation_id"] = str(doc["conversation_id"])
    if doc.get("message_id") and isinstance(doc["message_id"], ObjectId):
        doc["message_id"] = str(doc["message_id"])
    return doc


def local_generate_assistant_reply(user_text: str) -> str:
    t = user_text.strip()
    if not t:
        return "I'm here! Ask me anything."
    lower = t.lower()
    if "joke" in lower:
        return "Here's one: Why do programmers prefer dark mode? Because light attracts bugs."
    if "hello" in lower or "hi" in lower:
        return "Hello! I'm your always-on AI. How can I help today?"
    if "help" in lower:
        return "Tell me what you're trying to do, and I'll break it into clear steps."
    if len(t) < 12:
        return f"You said: '{t}'. Tell me more so I can give a better answer."
    return (
        "Here's a quick, helpful answer based on what you asked: "
        + t[:300]
        + "\n\nI can also provide examples, step-by-step guides, or summaries if you like."
    )


@app.get("/")
def read_root():
    return {"message": "Chat API is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


@app.post("/chat", response_model=ChatReply)
def chat(req: ChatRequest):
    user_text = (req.message or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Optional: store messages to DB (simple log)
    try:
        conv = ConversationSchema(title=user_text[:40] or "New Chat")
        conv_id = create_document("conversation", conv)
        conv_oid = ObjectId(conv_id)
        create_document("message", {
            "conversation_id": conv_oid,
            "role": "user",
            "content": user_text,
            "attachments": req.attachments or [],
        })
    except Exception:
        conv_oid = None

    # Build system prompt with attachment summary
    attachment_note = ""
    if req.attachments:
        try:
            names = ", ".join([a.get("name", "file") for a in req.attachments])
            attachment_note = f"\n\nUser included attachments: {names}. If relevant, reference them in your answer."
        except Exception:
            attachment_note = ""

    reply_text = None

    # Try OpenAI first if key is available
    if OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            completion = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are StudyCenter Ai, a helpful, concise study assistant. Prefer clear, structured answers."},
                    {"role": "user", "content": user_text + attachment_note},
                ],
                temperature=0.3,
            )
            reply_text = completion.choices[0].message.content or ""
        except Exception as e:
            # Fallback to local
            reply_text = None

    if not reply_text:
        reply_text = local_generate_assistant_reply(user_text)

    # Save assistant message
    try:
        if conv_oid is not None:
            create_document("message", {
                "conversation_id": conv_oid,
                "role": "assistant",
                "content": reply_text,
                "attachments": [],
            })
    except Exception:
        pass

    return {"reply": reply_text}


@app.post("/attachments")
async def upload_attachment(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(None),
    message_id: Optional[str] = Form(None),
):
    data = await file.read()
    b64 = base64.b64encode(data).decode("utf-8")

    conv_oid = None
    msg_oid = None
    if conversation_id:
        try:
            conv_oid = ObjectId(conversation_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid conversation_id")
    if message_id:
        try:
            msg_oid = ObjectId(message_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid message_id")

    doc = {
        "conversation_id": conv_oid,
        "message_id": msg_oid,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(data),
        "data_base64": b64,
    }
    att_id = create_document("attachment", doc)
    return {
        "id": att_id,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(data),
        "download_url": f"/attachments/{att_id}",
    }


@app.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: str):
    try:
        oid = ObjectId(attachment_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid attachment id")

    results = get_documents("attachment", {"_id": oid})
    if not results:
        raise HTTPException(status_code=404, detail="Attachment not found")
    att = results[0]
    try:
        raw = base64.b64decode(att.get("data_base64", ""))
    except Exception:
        raise HTTPException(status_code=500, detail="Corrupted attachment data")

    filename = att.get("filename") or "download"
    content_type = att.get("content_type") or "application/octet-stream"

    headers = {
        "Content-Disposition": f"attachment; filename=\"{filename}\""
    }
    return Response(content=raw, media_type=content_type, headers=headers)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
