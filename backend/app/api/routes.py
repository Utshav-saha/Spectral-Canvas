import json
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from app.schemas.models import EncodeParams
from app.services import pipeline
from app.storage import session_store
from app.config import (
    MAX_UPLOAD_BYTES, MAX_TEXT_CHARS, DEFAULTS, TRACKS, DEFAULT_TRACK,
    CALL_MAX_SECONDS,
)

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    """Also the frontend's source of truth for which tracks exist. Track 3 was
    cut, so anything not listed here cannot be asked for."""
    return {
        "status": "ok",
        "defaults": DEFAULTS,
        "tracks": list(TRACKS.values()),
        "default_track": DEFAULT_TRACK,
        "call_max_seconds": CALL_MAX_SECONDS,
    }


@router.post("/encode")
async def encode_endpoint(
    payload: str = Form(...),
    file: Optional[UploadFile] = File(None),
):
    """payload is a JSON string of EncodeParams; file is the optional upload.
    Multipart is used because the image and the params travel together."""
    try:
        params = EncodeParams(**json.loads(payload))
    except Exception as exc:
        raise HTTPException(400, f"Could not read the settings: {exc}")

    if params.text and len(params.text) > MAX_TEXT_CHARS:
        raise HTTPException(400, f"Text is limited to {MAX_TEXT_CHARS} characters.")

    upload_bytes = None
    if file is not None:
        upload_bytes = await file.read()
        if len(upload_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "That file is larger than 12 MB.")

    try:
        result = pipeline.run_encode(params, upload_bytes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Encoding failed: {exc}")

    metadata = result["metadata"]
    session_id = session_store.create({
        "kind": "encode",
        "audio": result["audio"],
        "metadata": metadata,
        "activation": result["activation"],
        "text": result["text"],
        "wav_bytes": result["wav_bytes"],
        "preview_png": result["preview_png"],
    })

    is_text = metadata.get("kind") == "text"
    return {
        "session_id": session_id,
        "track": pipeline.track_of(metadata),
        "kind": "text" if is_text else "image",
        "metadata": metadata,
        "stats": result["stats"],
        "encrypted": metadata["security_enabled"],
        "rows": metadata["rows"],
        "columns": metadata["columns"],
        "duration": metadata["duration_seconds"],
        "audio_url": f"/api/audio/{session_id}",
        "preview_url": None if is_text else f"/api/preview/{session_id}",
    }


@router.get("/audio/{session_id}")
def get_audio(session_id: str):
    session = session_store.get(session_id)
    if not session or "wav_bytes" not in session:
        raise HTTPException(404, "That transmission has expired. Send it again.")
    return Response(
        content=session["wav_bytes"],
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="spectral_{session_id}.wav"'},
    )


@router.get("/preview/{session_id}")
def get_preview(session_id: str):
    session = session_store.get(session_id)
    if not session or "preview_png" not in session:
        raise HTTPException(404, "Preview not found.")
    return Response(content=session["preview_png"], media_type="image/png")


@router.get("/waveform/{session_id}")
def get_waveform(session_id: str, buckets: int = 1000):
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(404, "That transmission has expired. Send it again.")
    sample_rate = (session.get("metadata") or {}).get("sample_rate") \
        or session.get("sample_rate", 44100)
    return pipeline.waveform_payload(session["audio"], sample_rate,
                                     max(100, min(buckets, 2000)))


@router.post("/inspect")
async def inspect_endpoint(file: UploadFile = File(...)):
    """Read an uploaded WAV without decoding it - tells the Receive page
    whether a PIN is going to be needed."""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is larger than 12 MB.")

    try:
        result = pipeline.run_inspect(raw)
    except Exception as exc:
        raise HTTPException(400, f"That does not look like a WAV file: {exc}")

    metadata = result["metadata"]
    session_id = session_store.create({
        "kind": "decode",
        "audio": result["audio"],
        "metadata": metadata,
        "sample_rate": result["sample_rate"],
        "wav_bytes": raw,
    })

    track = pipeline.track_of(metadata)
    # the file says which track it came in on, so the receiver never picks
    carried = "" if track == DEFAULT_TRACK else f"It came in over a phone call. "

    if metadata is None:
        message = ("This file has no Spectral Canvas header, so there is nothing "
                   "to rebuild from it. You can still inspect the waveform.")
    elif metadata.get("kind") == "text":
        message = "This is a text transmission. Decode it whenever you are ready."
    elif metadata.get("security_enabled"):
        message = (f"{carried}This transmission is locked. Enter the numbers "
                   f"and PIN to open it.")
    else:
        message = f"{carried}This transmission is open. Rebuild it whenever you are ready."

    return {
        "session_id": session_id,
        "track": track,
        "kind": (metadata or {}).get("kind", "image"),
        "encrypted": bool(metadata and metadata.get("security_enabled")),
        "has_metadata": metadata is not None,
        "metadata": metadata,
        "stats": result["stats"],
        "message": message,
    }


@router.post("/decode")
def decode_endpoint(body: dict):
    session = session_store.get(body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "That transmission has expired. Upload it again.")

    metadata = session.get("metadata")
    if not metadata:
        raise HTTPException(400, "This file has no Spectral Canvas header to rebuild from.")

    try:
        result = pipeline.run_decode(
            session["audio"], metadata,
            caller=body.get("caller"), receiver=body.get("receiver"),
            pin=body.get("pin"), source_activation=session.get("activation"),
            source_text=session.get("text"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Rebuilding failed: {exc}")

    if metadata.get("kind") == "text":
        return {
            "session_id": body["session_id"],
            "track": pipeline.track_of(metadata),
            "kind": "text",
            "text": result["text"],
            "characters": len(result["text"]),
            "symbols": metadata["symbols"],
            "rows": metadata["rows"],
            "columns": metadata["columns"],
            "mode": "text",
            "decrypted": False,
            "metrics": result["metrics"],
        }

    session_store.update(body["session_id"], {"recovered_png": result["png"]})

    return {
        "session_id": body["session_id"],
        "track": pipeline.track_of(metadata),
        "kind": "image",
        "image_url": f"/api/recovered/{body['session_id']}",
        "rows": metadata["rows"],
        "columns": metadata["columns"],
        "mode": metadata.get("mode", "L"),
        "decrypted": result["decrypted"],
        "metrics": result["metrics"],
    }


@router.get("/recovered/{session_id}")
def get_recovered(session_id: str):
    session = session_store.get(session_id)
    if not session or "recovered_png" not in session:
        raise HTTPException(404, "Nothing has been rebuilt for this transmission yet.")
    return Response(
        content=session["recovered_png"],
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="recovered_{session_id}.png"'},
    )
