from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessageIn(BaseModel):
    message: str
    response_id: str | None = None


class ChatMessageOut(BaseModel):
    reply: str
    response_id: str


@router.get("/greeting", response_model=ChatMessageOut)
def chat_greeting() -> ChatMessageOut:
    return ChatMessageOut(
        reply=(
            "Hey! I’m the Green Candle AI. Ask me about services, pricing, "
            "or how dispatch works."
        ),
        response_id=str(uuid4()),
    )


@router.post("", response_model=ChatMessageOut)
def chat_reply(payload: ChatMessageIn) -> ChatMessageOut:
    cleaned_message = payload.message.strip()
    if not cleaned_message:
        reply = "Send a message and I’ll help right away."
    else:
        reply = (
            "Placeholder response: I got your message — “"
            f"{cleaned_message[:280]}"
            "”. Live AI reply wiring comes next."
        )

    return ChatMessageOut(
        reply=reply,
        response_id=payload.response_id or str(uuid4()),
    )
