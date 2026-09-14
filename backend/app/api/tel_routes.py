"""Endpoints for the voice-call page. Everything lives under /api/tel so the
original /api routes stay exactly as the Send and Receive pages expect."""

from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services import tel_pipeline as tel
from app.storage import session_store
from app.config import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/tel")


class SendBody(BaseModel):
    image_id: str
    size: int = 96
    quality: int = 50
    security_enabled: bool = False
    caller: Optional[str] = None
    receiver: Optional[str] = None
    pin: Optional[str] = None


class CallBody(BaseModel):
    session_id: str
    loss: float = Field(0.02, ge=0.0, le=0.3)
    noise_db: float = Field(-45.0, ge=-80.0, le=-10.0)
    seed: int = 0


class ReceiveBody(BaseModel):
    session_id: str
    security_enabled: bool = False
    caller: Optional[str] = None
    receiver: Optional[str] = None
    pin: Optional[str] = None


def _session(session_id, kinds, missing="That transmission has expired. Send it again."):
    session = session_store.get(session_id)
    if not session or session.get("kind") not in kinds:
        raise HTTPException(404, missing)
    return session


async def _read_upload(file):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is larger than 12 MB.")
    return raw


@router.get("/info")
def info():
    return tel.info()


@router.post("/stage")
async def stage(file: UploadFile = File(...)):
    """Hold the picture server-side so changing size or quality can re-plan
    without uploading it again."""
    raw = await _read_upload(file)
    try:
        image = tel.open_image(raw)
        plan = tel.plan(raw, tel.image_webp.DEFAULT_SIZE, tel.image_webp.DEFAULT_QUALITY)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    image_id = session_store.create({"kind": "tel-image", "image_bytes": raw})
    return {"image_id": image_id, "width": image.width, "height": image.height,
            "plan": plan}


@router.get("/plan/{image_id}")
def plan(image_id: str, size: int = 96, quality: int = 50):
    session = _session(image_id, {"tel-image"}, "That picture has expired. Choose it again.")
    try:
        return tel.plan(session["image_bytes"], size, quality)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/send")
def send(body: SendBody):
    source = _session(body.image_id, {"tel-image"}, "That picture has expired. Choose it again.")
    try:
        result = tel.run_send(source["image_bytes"], body.size, body.quality,
                              locked=body.security_enabled, caller=body.caller,
                              receiver=body.receiver, pin=body.pin)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Encoding failed: {exc}")

    session_id = session_store.create({
        "kind": "tel-send",
        "audio": result["audio"],
        "sample_rate": tel.SAMPLE_RATE,
        "wav_bytes": result["wav_bytes"],
        "sent_png": result["sent_png"],
        "sent_array": result["sent_array"],
    })
    return {
        "session_id": session_id,
        "report": result["report"],
        "stats": result["stats"],
        "locked": body.security_enabled,
        "audio_url": f"/api/tel/audio/{session_id}",
        "sent_url": f"/api/tel/sent/{session_id}",
    }


@router.post("/call")
def call(body: CallBody):
    source = _session(body.session_id, {"tel-send"})
    try:
        result = tel.run_call(source["audio"], body.loss, body.noise_db, body.seed)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"The simulated call failed: {exc}")

    session_id = session_store.create({
        "kind": "tel-call",
        "audio": result["audio"],
        "sample_rate": tel.SAMPLE_RATE,
        "wav_bytes": result["wav_bytes"],
        "sent_array": source["sent_array"],
    })
    return {
        "session_id": session_id,
        "stats": result["stats"],
        "loss": body.loss,
        "noise_db": body.noise_db,
        "seed": body.seed,
        "audio_url": f"/api/tel/audio/{session_id}",
    }


@router.post("/inspect")
async def inspect(file: UploadFile = File(...)):
    raw = await _read_upload(file)
    try:
        audio, source = tel.read_any_wav(raw)
        result = tel.run_inspect(audio)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"That audio could not be read: {exc}")

    session_id = session_store.create({
        "kind": "tel-upload",
        "audio": audio,
        "sample_rate": tel.SAMPLE_RATE,
        "wav_bytes": tel.to_wav_bytes(audio),
    })
    return {"session_id": session_id, **source, **result}


@router.post("/receive")
def receive(body: ReceiveBody):
    session = _session(body.session_id, {"tel-send", "tel-call", "tel-upload"},
                       "That audio has expired. Load it again.")
    try:
        result = tel.run_receive(session["audio"], locked=body.security_enabled,
                                 caller=body.caller, receiver=body.receiver,
                                 pin=body.pin, sent_array=session.get("sent_array"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Rebuilding failed: {exc}")

    session_store.update(body.session_id, {"recovered_png": result.pop("png")})
    return {"session_id": body.session_id,
            "image_url": f"/api/tel/recovered/{body.session_id}", **result}


@router.get("/audio/{session_id}")
def audio(session_id: str):
    session = _session(session_id, {"tel-send", "tel-call", "tel-upload"})
    name = "rx.wav" if session["kind"] == "tel-call" else "tx.wav"
    return Response(content=session["wav_bytes"], media_type="audio/wav",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/waveform/{session_id}")
def waveform(session_id: str, buckets: int = 1000):
    session = _session(session_id, {"tel-send", "tel-call", "tel-upload"})
    return tel.waveform_payload(session["audio"], max(100, min(buckets, 2000)))


@router.get("/sent/{session_id}")
def sent(session_id: str):
    session = _session(session_id, {"tel-send"})
    return Response(content=session["sent_png"], media_type="image/png")


@router.get("/recovered/{session_id}")
def recovered(session_id: str):
    session = session_store.get(session_id)
    if not session or "recovered_png" not in session:
        raise HTTPException(404, "Nothing has been rebuilt from this audio yet.")
    return Response(content=session["recovered_png"], media_type="image/png")
