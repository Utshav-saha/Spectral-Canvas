"""Endpoints for the voice-call page. Everything lives under /api/tel so the
original /api routes stay exactly as the Send and Receive pages expect.

A call can be simulated or placed for real. /call runs the transmission through
channel_sim (GSM 06.10, packet loss, a wandering level, noise) offline; /dial
places an actual SIP call with pjsua and plays the transmission into it, and
/play does the same through a virtual audio cable for a call dialled by hand in
a softphone. Either way /receive rebuilds the picture -- from the simulated
audio directly, or from the recording the phone made, which arrives through
/upload and is matched to its send by /inspect.

Two generations ship here, A and B. Generation C (WebP + Reed-Solomon) was cut: a byte-exact file transfer has no graded loss to measure or learn from. It is in the git history."""

from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services import tel_pipeline as tel
from app.storage import session_store
from app.config import MAX_AUDIO_UPLOAD_BYTES, MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/tel")

AUDIO_KINDS = {"tel-send", "tel-call", "tel-upload"}


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
    # which transmission this audio is a recording of. A recording carries no
    # header, so its geometry has to come from the send session.
    reference_id: Optional[str] = None
    security_enabled: bool = False
    caller: Optional[str] = None
    receiver: Optional[str] = None
    pin: Optional[str] = None


class DialBody(BaseModel):
    session_id: str
    # a SIP address, not a phone number: this reaches the Linphone app on the
    # phone over SIP, and never touches the PSTN
    target: str
    # PCMU by default, the way the CLI has always done it. Left unrestricted,
    # the call negotiates whatever both ends happen to prefer, and the page
    # cannot tell you afterwards whether the result meant anything. G.711 is
    # the gentle one: it carries a tone without modelling it.
    codec: Optional[str] = "PCMU"


class PlayBody(BaseModel):
    session_id: str
    # an output device index or a name fragment; null picks the best virtual
    # cable on this machine
    device: Optional[str] = None
    lead_in: float = Field(3.0, ge=0.0, le=30.0)


class AnswerBody(BaseModel):
    session_id: str
    codec: Optional[str] = "PCMU"


class InspectBody(BaseModel):
    session_id: str
    reference_id: Optional[str] = None


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


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """A recording of a real call. It carries no header, so it has to be
    matched to the send it came from before it can be rebuilt."""
    raw = await _read_upload(file, MAX_AUDIO_UPLOAD_BYTES)
    try:
        audio, source = tel.read_any_audio(raw, file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"That audio could not be read: {exc}. Supported: "
                                 f"WAV, and .mka/.m4a/.mp4/.caf when ffmpeg is installed.")

    session_id = session_store.create({
        "kind": "tel-upload",
        "audio": audio,
        "sample_rate": tel.SAMPLE_RATE,
        "wav_bytes": tel.to_wav_bytes(audio),
    })
    return {"session_id": session_id, **source,
            **tel.run_inspect(audio, None)}


@router.post("/inspect")
def inspect(body: InspectBody):
    session = _session(body.session_id, AUDIO_KINDS,
                       "That audio has expired. Load it again.")
    metadata, _ = _reference(body, session)
    try:
        return {"session_id": body.session_id,
                **tel.run_inspect(session["audio"], metadata)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"That audio could not be inspected: {exc}")


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


@router.get("/dial/status")
def dial_status():
    """Whether this server can place a call at all, and why not if it cannot."""
    try:
        from voip import dial
    except ImportError as exc:
        return {"pjsua": False, "configured": False, "identity": None,
                "registrar": None, "message": f"The call module is unavailable: {exc}"}
    return dial.status()


@router.post("/dial")
def dial(body: DialBody):
    """Place a real SIP call and play the transmission into it.

    Returns as soon as pjsua is dialling; the page polls /dial/{call_id}.
    Credentials are read from the server's environment, never from this body.
    """
    session = _session(body.session_id, {"tel-send"})
    try:
        from voip import dial as dialer
        from voip.config import VoipDependencyError, VoipError
    except ImportError as exc:
        raise HTTPException(503, f"The call module is unavailable: {exc}")

    try:
        call_id = dialer.place(session["audio"], body.target, codec=body.codec)
    except VoipDependencyError as exc:
        raise HTTPException(503, str(exc))
    except VoipError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"The call could not be placed: {exc}")

    return {"call_id": call_id, "session_id": body.session_id,
            **(dialer.progress(call_id) or {})}


@router.post("/answer")
def answer(body: AnswerBody):
    """Wait for the phone to call this machine, then play into that call.

    The dependable direction: a handset placing a call needs no push
    notification, which is what stops an incoming one from ever ringing.
    """
    session = _session(body.session_id, {"tel-send"})
    try:
        from voip import dial as dialer
        from voip.config import VoipDependencyError, VoipError
    except ImportError as exc:
        raise HTTPException(503, f"The call module is unavailable: {exc}")

    try:
        call_id = dialer.answer(session["audio"], codec=body.codec)
    except VoipDependencyError as exc:
        raise HTTPException(503, str(exc))
    except VoipError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Could not start waiting: {exc}")

    return {"call_id": call_id, "session_id": body.session_id,
            **(dialer.progress(call_id) or {})}


@router.get("/dial/{call_id}")
def dial_progress(call_id: str):
    from voip import dial as dialer
    found = dialer.progress(call_id)
    if found is None:
        raise HTTPException(404, "No such call.")
    return {"call_id": call_id, **found}


@router.get("/play/devices")
def play_devices():
    """Audio outputs, with the virtual cables marked.

    This is the route that works everywhere. pjsua is not packaged for
    Windows, so dialling from the server is macOS and Linux only; playing the
    transmission into a softphone's microphone needs no SIP stack at all.
    """
    try:
        from voip import audio_out
    except ImportError as exc:
        return {"ready": False, "devices": [],
                "message": f"The playback module is unavailable: {exc}"}
    return audio_out.status()


@router.post("/play")
def play(body: PlayBody):
    """Play the transmission into a virtual cable, for a call placed by hand."""
    session = _session(body.session_id, {"tel-send"})
    try:
        from voip import audio_out
        from voip.config import VoipDependencyError, VoipError
    except ImportError as exc:
        raise HTTPException(503, f"The playback module is unavailable: {exc}")

    try:
        play_id = audio_out.play(session["audio"], device=body.device,
                                 lead_in=body.lead_in)
    except VoipDependencyError as exc:
        raise HTTPException(503, str(exc))
    except VoipError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Playback could not start: {exc}")

    return {"play_id": play_id, "session_id": body.session_id,
            **(audio_out.progress(play_id) or {})}


@router.get("/play/{play_id}")
def play_progress(play_id: str):
    from voip import audio_out
    found = audio_out.progress(play_id)
    if found is None:
        raise HTTPException(404, "No such playback.")
    return {"play_id": play_id, **found}


@router.post("/play/stop")
def play_stop():
    """Cut a transmission short. The call itself is not ours to hang up."""
    from voip import audio_out
    return {"stopped": audio_out.stop()}


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
