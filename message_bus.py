"""Message bus for agent-to-agent communication in Aether.

Agents can post messages to the bus, query messages addressed to them,
and request clarifications that pause the pipeline until answered.
This lets agents cooperate instead of just passing data linearly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from config import EXPLABS_API_KEY, EXPLABS_MODELS, EXPLABS_URL, OLLAMA_URL
from ollama_client import chat


@dataclass
class Message:
    """A single message between agents."""
    sender: str
    recipient: str
    content: str
    timestamp: float = field(default_factory=time.time)
    reply_to: str | None = None
    resolved: bool = False
    _id: str = ""


class MessageBus:
    """Small in-memory message bus for agent communication."""

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._counter = 0

    def send(self, sender: str, recipient: str, content: str,
             reply_to: str | None = None) -> str:
        """Send a message from one agent to another. Returns message id."""
        msg = Message(sender=sender, recipient=recipient, content=content,
                      reply_to=reply_to)
        self._counter += 1
        msg._id = f"msg-{self._counter}"
        self._messages.append(msg)
        return msg._id

    def inbox(self, recipient: str) -> list[Message]:
        """Return all messages for a recipient, oldest first."""
        return [m for m in self._messages if m.recipient == recipient]

    def unread(self, recipient: str) -> list[Message]:
        """Return unread messages for a recipient."""
        return [m for m in self._messages
                if m.recipient == recipient and not m.resolved]

    def mark_read(self, message_id: str) -> None:
        """Mark a message as read/resolved."""
        for m in self._messages:
            if m._id == message_id:
                m.resolved = True

    def history(self, limit: int = 50) -> list[Message]:
        """Return the most recent messages."""
        return self._messages[-limit:]

    def conversation(self, participant: str) -> list[Message]:
        """All messages involving a participant (sent or received)."""
        return [m for m in self._messages
                if m.sender == participant or m.recipient == participant]


# Singleton bus instance shared across the builder
bus = MessageBus()


def ask_with_context(role: str, system: str, prompt: str,
                     agent_name: str,
                     include_history: int = 5) -> str:
    """Ask a model with recent message-bus context prepended.

    Lets an agent see recent message-bus messages before responding,
    enabling true multi-agent conversation.
    """
    from agents import ask_with_context_bus
    return ask_with_context_bus(role, system, prompt, bus, agent_name,
                                include_history=include_history)


def ask_with_context_bus(role: str, system: str, prompt: str,
                         bus: MessageBus, agent_name: str,
                         include_history: int = 5) -> str:
    """Ask a model with message-bus context prepended."""
    from config import MODELS, MODELS as _MODELS, OLLAMA_URL
    from ollama_client import chat

    base_url, api_key = OLLAMA_URL, None
    model = MODELS.get(role, "qwen3.5:4b")
    for _role, slug in EXPLABS_MODELS.items():
        if model == slug:
            base_url, api_key = EXPLABS_URL, EXPLABS_API_KEY
            break

    recent = bus.conversation(participant=agent_name)[-include_history:]
    context_lines = []
    for msg in recent:
        context_lines.append(
            f"[{msg.sender} → {msg.recipient}] {msg.content}"
        )
    context_str = "\n".join(context_lines) if context_lines else "(no prior messages)"

    full_prompt = (
        f"{prompt}\n\n"
        f"--- Recent agent messages (most recent last) ---\n"
        f"{context_str}\n"
        f"--- End of agent messages ---\n"
    )

    return chat(base_url, model, [
        {"role": "system", "content": system},
        {"role": "user", "content": full_prompt},
    ], num_predict=2048, api_key=api_key)