"""Pydantic models for SwarmMind data layer."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Project(BaseModel):
    """A research project that groups sources, conversations and notes."""

    id: str = Field(default="", description="Unique project identifier (UUID).")
    name: str = Field(..., description="Human-readable project name.")
    description: str = Field(default="", description="Optional project description.")
    web_search_enabled: bool = Field(default=True, description="Allow web search workers.")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Source(BaseModel):
    """A knowledge source attached to a project."""

    id: str = Field(default="", description="Unique source identifier (UUID).")
    project_id: str = Field(..., description="FK — owning project.")
    source_type: str = Field(
        ...,
        description="One of: youtube, web, pdf, docx, pptx, xlsx, image, audio, epub, csv, json, xml, text, zip.",
    )
    source_uri: str = Field(..., description="File path, URL, or raw text.")
    display_name: str = Field(default="", description="Human-friendly label.")
    status: str = Field(
        default="pending",
        description="One of: pending, processing, ready, error.",
    )
    char_count: int = Field(default=0)
    chunk_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    added_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(BaseModel):
    """A single research query / conversation turn."""

    id: str = Field(default="", description="Unique conversation identifier (UUID).")
    project_id: str = Field(..., description="FK — owning project.")
    query: str = Field(..., description="The user's research question.")
    web_search_used: bool = Field(default=False)
    worker_count: int = Field(default=0, description="Number of workers spawned.")
    report_json: str = Field(default="", description="Full research report as JSON.")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Note(BaseModel):
    """A user-written note within a project."""

    id: str = Field(default="", description="Unique note identifier (UUID).")
    project_id: str = Field(..., description="FK — owning project.")
    title: str = Field(default="", description="Note title.")
    content: str = Field(default="", description="Note body (Markdown).")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
