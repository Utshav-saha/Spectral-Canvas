"""Endpoints for the voice-call page. Everything lives under /api/tel so the
original /api routes stay exactly as the Send and Receive pages expect.

The call is simulated, never placed: /call runs the transmission through
channel_sim (GSM 06.10, packet loss, a wandering level, noise) and /receive
rebuilds the picture from either end of it.

Two generations ship here, A and B. Generation C (WebP + Reed-Solomon) was cut: a byte-exact file transfer has no graded loss to measure or learn from. It is in the git history."""

from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services import tel_pipeline as tel
from app.storage import session_store
from app.config import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/tel")

AUDIO_KINDS = {"tel-send", "tel-call"}


class SendBody(BaseModel):
    image_id: str
    generation: str = "A"
    size: int = 24
    levels: int = 4
    colour: bool = False
    # stretch the histogram before quantising; a call carries too few levels
    # to spend them on a photograph's unused dynamic range
    autocontrast: bool = True
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
    # which transmission this audio came from, so its geometry is known
    reference_id: Optional[str] = None
    security_enabled: bool = False
    caller: Optional[str] = None
    receiver: Optional[str] = None
    pin: Optional[str] = None


class EnhanceBody(BaseModel):
    session_id: str


def _session(session_id, kinds, missing="That transmission has expired. Send it again."):
    session = session_store.get(session_id)
    if not session or session.get("kind") not in kinds:
        raise HTTPException(404, missing)
    return session


def _reference(body, session):
    """The metadata to decode with: the named send, or this session's own."""
    if getattr(body, "reference_id", None):
        ref = _session(body.reference_id, {"tel-send"},
                       "That transmission has expired. Send it again.")
        return ref.get("metadata"), ref.get("sent_array")
    return session.get("metadata"), session.get("sent_array")


async def _read_upload(file, limit=MAX_UPLOAD_BYTES):
    raw = await file.read()
    if len(raw) > limit:
        raise HTTPException(
            400, f"That file is larger than {limit // (1024 * 1024)} MB.")
    return raw


@router.get("/info")
def info():
    # `model` tells the page whether to offer the Enhance button at all
    return {**tel.info(), "model": tel.model_status()}


@router.post("/stage")
async def stage(file: UploadFile = File(...)):
    """Hold the picture server-side so changing the generation or the grid can
    re-plan without uploading it again."""
    raw = await _read_upload(file)
    try:
        image = tel.open_image(raw)
        plan = tel.plan("A", tel.GENERATIONS["A"]["default_size"],
                        tel.GENERATIONS["A"]["default_levels"], False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    image_id = session_store.create({"kind": "tel-image", "image_bytes": raw})
    return {"image_id": image_id, "width": image.width, "height": image.height,
            "plan": plan}


@router.get("/plan")
def plan(generation: str = "A", size: int = 24, levels: int = 4,
         colour: bool = False):
    try:
        return tel.plan(generation, size, levels, colour)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/send")
def send(body: SendBody):
    source = _session(body.image_id, {"tel-image"},
                      "That picture has expired. Choose it again.")
    try:
        result = tel.run_send(source["image_bytes"], body.generation, body.size,
                              body.levels, body.colour,
                              locked=body.security_enabled, caller=body.caller,
                              receiver=body.receiver, pin=body.pin,
                              autocontrast=body.autocontrast)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Encoding failed: {exc}")

    session_id = session_store.create({
        "kind": "tel-send",
        "audio": result["audio"],
        "metadata": result["metadata"],
        "sample_rate": tel.SAMPLE_RATE,
        "wav_bytes": result["wav_bytes"],
        "sent_png": result["sent_png"],
        "sent_array": result["sent_array"],
    })
    return {
        "session_id": session_id,
        "generation": body.generation,
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
        # a simulated call keeps the sender's settings, so it decodes on its own
        "metadata": source.get("metadata"),
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


@router.post("/receive")
def receive(body: ReceiveBody):
    session = _session(body.session_id, AUDIO_KINDS,
                       "That audio has expired. Load it again.")
    metadata, sent_array = _reference(body, session)
    try:
        result = tel.run_receive(session["audio"], metadata,
                                 locked=body.security_enabled, caller=body.caller,
                                 receiver=body.receiver, pin=body.pin,
                                 sent_array=sent_array)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Rebuilding failed: {exc}")

    session_store.update(body.session_id, {"recovered_png": result.pop("png"),
                                           "recovered_array": result.pop("array")})
    return {"session_id": body.session_id,
            "image_url": f"/api/tel/recovered/{body.session_id}",
            "enhanced_url": None, **result}


@router.post("/enhance")
def enhance(body: EnhanceBody):
    """Run the learned upscaler over the rebuilt picture (Track 2).

    Separate from /receive on purpose: rebuilding is the transmission, this is
    a guess made afterwards, and the page shows them apart so nobody mistakes
    invented detail for received detail.
    """
    session = session_store.get(body.session_id)
    if not session or "recovered_array" not in session:
        raise HTTPException(404, "Nothing has been rebuilt from this audio yet.")

    try:
        result = tel.run_enhance(session["recovered_array"])
    except ValueError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"The model could not run: {exc}")

    session_store.update(body.session_id, {"enhanced_png": result.pop("png")})
    return {"session_id": body.session_id,
            "image_url": f"/api/tel/enhanced/{body.session_id}", **result}


@router.get("/enhanced/{session_id}")
def enhanced(session_id: str):
    session = session_store.get(session_id)
    if not session or "enhanced_png" not in session:
        raise HTTPException(404, "Nothing has been enhanced for this audio yet.")
    return Response(content=session["enhanced_png"], media_type="image/png")


@router.get("/audio/{session_id}")
def audio(session_id: str):
    session = _session(session_id, AUDIO_KINDS)
    name = "rx.wav" if session["kind"] == "tel-call" else "tx.wav"
    return Response(content=session["wav_bytes"], media_type="audio/wav",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/waveform/{session_id}")
def waveform(session_id: str, buckets: int = 1000):
    session = _session(session_id, AUDIO_KINDS)
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
