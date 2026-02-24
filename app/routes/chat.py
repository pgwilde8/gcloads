from uuid import uuid4
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from openai import OpenAI


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessageIn(BaseModel):
    message: str
    response_id: str | None = None


class ChatMessageOut(BaseModel):
    reply: str
    response_id: str


# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


@router.get("/greeting", response_model=ChatMessageOut)
def chat_greeting() -> ChatMessageOut:
    try:
        response = client.responses.create(
            model="gpt-4o",
            input="You are the Green Candle AI assistant for a truck dispatch service. Greet the user and mention you can help with services, pricing, or how dispatch works. Keep it brief and friendly.",
            store=True
        )
        return ChatMessageOut(
            reply=response.output_text,
            response_id=response.id,
        )
    except Exception as e:
        # Fallback greeting if API fails
        return ChatMessageOut(
            reply=(
                "Hey! I'm the Green Candle AI. Ask me about services, pricing, "
                "or how dispatch works."
            ),
            response_id=str(uuid4()),
        )


@router.post("", response_model=ChatMessageOut)
def chat_reply(payload: ChatMessageIn) -> ChatMessageOut:
    cleaned_message = payload.message.strip()
    if not cleaned_message:
        reply = "Send a message and I'll help right away."
        return ChatMessageOut(
            reply=reply,
            response_id=str(uuid4()),
        )
    
    try:
        # Prepare the input for Responses API
        messages = [
            {
                "role": "system",
                "content": "You are the Green Candle AI assistant for a truck dispatch service called Green Candle Dispatch. Key information: - 2.5% flat fee - AI-powered load scanning and negotiation - 24/7 board monitoring - Instant paperwork automation - Help with owner-operators and trucking companies - Services include load finding, rate negotiation, and paperwork processing Be helpful, concise, and focused on trucking/dispatch topics."
            },
            {
                "role": "user", 
                "content": cleaned_message
            }
        ]
        
        # Create response with previous context if available
        kwargs = {
            "model": "gpt-4o",
            "input": messages,
            "store": True
        }
        
        if payload.response_id:
            kwargs["previous_response_id"] = payload.response_id
        
        response = client.responses.create(**kwargs)
        
        return ChatMessageOut(
            reply=response.output_text,
            response_id=response.id,
        )
        
    except Exception as e:
        # Handle API errors gracefully
        error_message = "I'm having trouble connecting right now. Please try again in a moment."
        
        # Provide more specific error for missing API key
        if "API key" in str(e).lower():
            error_message = "Service configuration issue. Please contact support."
            
        return ChatMessageOut(
            reply=error_message,
            response_id=payload.response_id or str(uuid4()),
        )
