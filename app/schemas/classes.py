from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class ClassCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Class name is required")
        return v


class ClassUpdate(BaseModel):
    name: Optional[str] = None
    is_archived: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Class name cannot be blank")
        return v


class ClassResponse(BaseModel):
    id: int
    name: str
    is_archived: bool
    is_system_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
