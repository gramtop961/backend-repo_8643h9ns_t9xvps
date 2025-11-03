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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    attachments: Optional[List[str]] = None

class ChatResponse(BaseModel):
    conversation_id: str
    reply: MessageResponse


def to_str_id(doc):
    doc = dict(doc)
    if doc.get("_id"):
        doc["id"] = str(doc.pop("_id"))
    if doc.get("conversation_id") and isinstance(doc["conversation_id"], ObjectId):
        doc["conversation_id"] = str(doc["conversation_id"])
    if doc.get("message_id") and isinstance(doc["message_id"], ObjectId):
        doc["message_id"] = str(doc["message_id"])
    return doc


def generate_assistant_reply(user_text: str) -> str:
    """
    Simple built-in assistant logic to keep the app fully self-contained and unlimited.
    This is rule-based and does not use external APIs.
    """
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


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
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

    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


@app.post("/conversations", response_model=ConversationResponse)
def create_conversation(req: CreateConversationRequest):
    title = req.title or "New Chat"
    conv = ConversationSchema(title=title)
    inserted_id = create_document("conversation", conv)
    return {"id": inserted_id, "title": title}


@app.get("/conversations", response_model=List[ConversationResponse])
def list_conversations():
    docs = get_documents("conversation", {})
    result = []
    for d in docs:
        d = to_str_id(d)
        result.append({"id": d["id"], "title": d.get("title", "Chat")})
    # newest first by created_at if present
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


@app.get("/messages", response_model=List[MessageResponse])
def list_messages(conversation_id: str = Query(..., description="Conversation ID")):
    try:
        conv_oid = ObjectId(conversation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid conversation_id")

    docs = get_documents("message", {"conversation_id": conv_oid})
    # sort by created_at if present
    docs.sort(key=lambda x: x.get("created_at"))
    out = []
    for d in docs:
        d = to_str_id(d)
        # Standardize created_at to ISO if present
        ca = None
        if "created_at" in d and d["created_at"] is not None:
            try:
                ca = d["created_at"].isoformat()
            except Exception:
                ca = None
        out.append(
            MessageResponse(
                id=d["id"],
                conversation_id=str(d.get("conversation_id")),
                role=d.get("role", "user"),
                content=d.get("content", ""),
                attachments=d.get("attachments"),
                created_at=ca,
            )
        )
    return out


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    user_text = (req.message or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Ensure conversation
    conv_id = req.conversation_id
    if not conv_id:
        conv = ConversationSchema(title=user_text[:40] or "New Chat")
        conv_id = create_document("conversation", conv)

    # Validate/convert conv id
    try:
        conv_oid = ObjectId(conv_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid conversation_id")

    # Store user message
    user_msg = MessageSchema(conversation_id=str(conv_id), role="user", content=user_text, attachments=req.attachments)
    create_document("message", {
        "conversation_id": conv_oid,
        "role": user_msg.role,
        "content": user_msg.content,
        "attachments": req.attachments or [],
    })

    # Generate assistant reply
    reply_text = generate_assistant_reply(user_text)

    # Store assistant message
    assistant_msg_doc = {
        "conversation_id": conv_oid,
        "role": "assistant",
        "content": reply_text,
        "attachments": [],
    }
    reply_id = create_document("message", assistant_msg_doc)

    reply = MessageResponse(
        id=reply_id,
        conversation_id=str(conv_id),
        role="assistant",
        content=reply_text,
        attachments=[],
    )
    return ChatResponse(conversation_id=str(conv_id), reply=reply)


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
