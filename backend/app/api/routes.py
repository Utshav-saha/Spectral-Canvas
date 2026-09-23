import json
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from app.schemas.models import EncodeParams
from app.services import pipeline
from app.services import channel_lab
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
        # Track 2 only: how long a real call would have taken, and whether
        # that is long enough to be worth saying out loud. No longer a limit.
        "call_seconds": result.get("call_seconds"),
        "long": bool(result.get("long")),
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
        # A recording of a real call is the common case here, and it lands on
        # this page because it is the one called "Receive". It can never carry
        # a header: what a phone records is sound off a voice line, not the WAV
        # this server wrote. Its geometry lives in the Call page's send
        # session, so that is where it has to be rebuilt. Saying only "no
        # header" sends people away thinking the recording is broken.
        message = ("This file has no Spectral Canvas header. If it is a "
                   "recording of a real phone call, rebuild it on the Call "
                   "page's Receive tab instead - a recording carries no "
                   "header, so it has to be matched to the transmission it "
                   "came from. Otherwise there is nothing to rebuild here, "
                   "though you can still inspect the waveform.")
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


@router.get("/channel/effects")
def channel_effects():
    """The catalogue the experiments page builds itself from, so the UI and
    the validator cannot drift apart. `model` says whether the learned
    restoration step can run, so the page knows whether to offer it."""
    return {**channel_lab.catalogue(), "model": channel_lab.model_status()}


@router.post("/channel")
def channel_endpoint(body: dict):
    """Put a transmission through a channel and measure what it cost.

    Needs a session from /api/encode, because the experiment is only meaningful
    against the activation that was actually sent.
    """
    session = session_store.get(body.get("session_id", ""))
    if not session or session.get("kind") != "encode":
        raise HTTPException(404, "That transmission has expired. Send it again.")

    try:
        result = channel_lab.run_channel(
            session, body.get("effects") or [],
            caller=body.get("caller"), receiver=body.get("receiver"),
            pin=body.get("pin"),
            # "undo": run the LTI inverse over the damaged audio as well, so
            # the page can show what division by H(f) does and does not fix.
            # "restore": then hand what is left to the learned model.
            undo=bool(body.get("undo")), epsilon=body.get("epsilon"),
            restore=bool(body.get("restore")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"The channel run failed: {exc}")

    # The clean decode is the honest baseline: it isolates what the channel
    # did from what the encoder's own quantisation did. Decoded once and kept
    # on the session, because it does not change between experiments.
    baseline = session.get("clean_image")
    if baseline is None:
        clean = pipeline.run_decode(
            session["audio"], session["metadata"],
            caller=body.get("caller"), receiver=body.get("receiver"),
            pin=body.get("pin"), source_activation=session.get("activation"))
        baseline = clean["image_array"]
        session_store.update(body["session_id"], {
            "clean_image": baseline, "clean_metrics": clean["metrics"]})

    run_id = session_store.create({
        "kind": "channel",
        "audio": result["audio"],
        "metadata": session["metadata"],
        "sample_rate": result["sample_rate"],
        "recovered_png": result["png"],
    })

    undone = None
    if result.get("undone"):
        fixed = result["undone"]
        undo_id = session_store.create({
            "kind": "channel",
            "audio": fixed["audio"],
            "metadata": session["metadata"],
            "sample_rate": result["sample_rate"],
            "recovered_png": fixed["png"],
        })
        undone = {
            "run_id": undo_id,
            "image_url": f"/api/recovered/{undo_id}",
            "audio_url": f"/api/channel/audio/{undo_id}",
            "metrics": fixed["metrics"],
            "row_error": channel_lab.row_profile(baseline, fixed["image_array"]),
            "stats": fixed["stats"],
            "epsilon": fixed["epsilon"],
            # kept apart: a real inverse, a partial one, and none at all
            "undone": fixed["undone"],
            "attempted": fixed["attempted"],
            "skipped": fixed["skipped"],
        }

    restored = None
    if result.get("restored"):
        model = result["restored"]
        model_id = session_store.create({
            "kind": "channel",
            "audio": result.get("undone", result)["audio"],
            "metadata": session["metadata"],
            "sample_rate": result["sample_rate"],
            "recovered_png": model["png"],
        })
        restored = {
            "run_id": model_id,
            "image_url": f"/api/recovered/{model_id}",
            "metrics": model["metrics"],
            "row_error": channel_lab.row_profile(baseline, model["image_array"]),
            # whether the model was handed the inverted picture or the raw one
            "after": model["after"],
        }

    return {
        "run_id": run_id,
        "session_id": body["session_id"],
        "chain": result["chain"],
        "description": result["description"],
        "image_url": f"/api/recovered/{run_id}",
        "audio_url": f"/api/channel/audio/{run_id}",
        "metrics": result["metrics"],
        "baseline_metrics": session_store.get(body["session_id"]).get("clean_metrics"),
        # per-row error: one image row is one frequency, so this is where an
        # LTI channel's fingerprint actually shows up
        "row_error": channel_lab.row_profile(baseline, result["image_array"]),
        "stats": result["stats"],
        "clean_stats": result["clean_stats"],
        "undone": undone,
        "restored": restored,
        "rows": session["metadata"]["rows"],
        "columns": session["metadata"]["columns"],
        "mode": session["metadata"].get("mode", "L"),
    }


@router.get("/channel/audio/{run_id}")
def channel_audio(run_id: str):
    session = session_store.get(run_id)
    if not session or session.get("kind") != "channel":
        raise HTTPException(404, "That channel run has expired. Run it again.")
    from spectral.common.wav_container import write_wav_bytes
    wav = write_wav_bytes(session["sample_rate"], session["audio"],
                          session["metadata"])
    return Response(content=wav, media_type="audio/wav",
                    headers={"Content-Disposition":
                             f'attachment; filename="channel_{run_id}.wav"'})


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
