from pydantic import BaseModel


class Transcript(BaseModel):
    """Vendor-neutral STT result: recognized text and its language code."""

    text: str
    language: str
