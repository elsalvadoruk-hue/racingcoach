"""HTTP request bodies independent of runtime services and application startup."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class AppSettingsBody(BaseModel):
    telemetry_dirs: Optional[list[str]] = Field(default=None, max_length=12)
    gcp_project_id: Optional[str] = Field(default=None, max_length=120)
    gcp_location: Optional[str] = Field(default=None, max_length=60)
    speech_region: Optional[str] = Field(default=None, max_length=20)


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=20000)


class SessionLogAuto(BaseModel):
    enabled: bool
    consent_version: int | None = None


class SessionLogSend(BaseModel):
    questions: bool = False
    note: str = Field(default="", max_length=500)


class PracticeStartRequest(BaseModel):
    filename: str
    focus_index: int = Field(0, ge=0, le=10)
    # which list the focus came from: the driver's own laps or the Garage61 fast laps (None = the coach's list)
    source: Optional[Literal["PERSONAL", "GARAGE61", "WORKSPACE"]] = None


class DrillStartRequest(BaseModel):
    filename: str
    corner_label: str
    warmup: bool = True


class PracticeCloseRequest(BaseModel):
    status: Literal["DONE", "DROPPED"]
