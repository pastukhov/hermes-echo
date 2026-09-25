"""Hermes stage data models (ТЗ section 22).

The logical JSON contract Hermes must return:

    {
      "reply": "string",
      "note": {
        "create": true,
        "title": "string",
        "content": "markdown",
        "tags": ["tag1", "tag2"]
      }
    }

``reply`` is always required. ``note`` defaults to "no note" when absent.
"""
from pydantic import BaseModel, Field
from backend.src.voice_gateway.knowledge.models import KnowledgeProposal


class HermesNote(BaseModel):
    """Optional note request carried in the Hermes structured response."""

    knowledge: KnowledgeProposal | None = None
    create: bool = False
    title: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)


class HermesResponse(BaseModel):
    """Validated structured Hermes result (ТЗ section 22)."""

    reply: str
    note: HermesNote = Field(default_factory=HermesNote)
