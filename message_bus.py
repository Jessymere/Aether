"""Message bus for agent-to-agent communication in Aether.

The orchestrator broadcasts each agent's output to the bus and every agent call
reads back the messages addressed to it, so agents cooperate with shared context
instead of only passing data linearly.

``unread()``/``mark_read()`` exist for request-and-reply exchanges, but the
pipeline currently only broadcasts and reads — it never blocks waiting for a
reply. ``config`` and ``ollama_client`` are imported lazily by the delegate at
the bottom so this module stays importable from anywhere without a cycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


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

    def __len__(self) -> int:
        """Total messages ever posted — used as a per-run start marker."""
        return len(self._messages)

    def transcript(self, since: int = 0) -> list[dict]:
        """Messages after index ``since`` as JSON-serialisable dicts.

        Lets a caller persist just one run's agent-to-agent conversation into
        that run's record instead of the whole process lifetime.
        """
        return [
            {
                "sender": m.sender,
                "recipient": m.recipient,
                "content": m.content,
                "timestamp": m.timestamp,
            }
            for m in self._messages[since:]
        ]


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
    """Ask a model with message-bus context prepended.

    Thin delegate: ``agents.py`` owns the implementation and the model routing,
    so this no longer keeps a second copy of the local-vs-gateway lookup that
    drifts from ``config.model_route``.
    """
    from agents import ask_with_context_bus as _impl
    return _impl(role, system, prompt, bus, agent_name,
                 include_history=include_history)