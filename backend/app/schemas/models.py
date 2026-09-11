from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class EncodeParams(BaseModel):
    source_type: Literal["image", "text", "doodle"] = "image"
    text: Optional[str] = None
    data_url: Optional[str] = None

    target_width: int = Field(64, ge=8, le=160)
    target_height: int = Field(64, ge=8, le=160)
    mode: Literal["L", "RGB"] = "L"
    gray_levels: int = Field(16, ge=2, le=256)
    frame_duration: float = Field(0.05, gt=0.005, le=0.25)
    f_min: int = Field(1000, ge=100)
    f_max: int = Field(8000, le=20000)
    sample_rate: int = 44100

    security_enabled: bool = False
    caller: Optional[str] = None
    receiver: Optional[str] = None
    pin: Optional[str] = None
    alpha: float = Field(0.15, ge=0.0, le=0.6)


class EncodeResponse(BaseModel):
    session_id: str
    metadata: dict
    stats: dict
    encrypted: bool
    rows: int
    columns: int
    duration: float
    audio_url: str
    preview_url: str


class InspectResponse(BaseModel):
    session_id: str
    encrypted: bool
    has_metadata: bool
    metadata: Optional[dict] = None
    stats: dict
    message: str


class DecodeRequest(BaseModel):
    session_id: str
    caller: Optional[str] = None
    receiver: Optional[str] = None
    pin: Optional[str] = None


class DecodeResponse(BaseModel):
    session_id: str
    image_url: str
    rows: int
    columns: int
    mode: str
    decrypted: bool
    metrics: Optional[dict] = None
