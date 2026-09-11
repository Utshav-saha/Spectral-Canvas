# Spectral Canvas

Encoding and recovering images through audio spectrograms.
Signals and Systems coursework.

Rows become pitches, columns become moments, brightness becomes loudness.
A picture is turned into a sound you can listen to, then rebuilt from nothing
but that sound. A phone number and PIN lock a transmission so it stays
unreadable without them.

```
├── backend/     FastAPI + the pure spectral/ DSP library
├── frontend/    React + Vite
└── docs/        BACKEND_GUIDE.md, FRONTEND_GUIDE.md, project plan
```

## Requirements

- Python 3.10+
- Node 18+

## Run it

Two terminals. Backend first.

**Terminal 1 — backend**

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Check it: <http://127.0.0.1:8000/api/health>
Interactive API docs: <http://127.0.0.1:8000/docs>

**Terminal 2 — frontend**

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

`vite.config.js` proxies every `/api` call to port 8000, so no host is
hardcoded and CORS never bites in development. If you change the backend port,
change it in `vite.config.js` too.

## Production build

```bash
cd frontend
npm run build        # -> frontend/dist
npm run preview      # serve the build locally
```

To serve the built frontend from FastAPI itself, add to `app/main.py`:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="site")
```

Mount it **after** `include_router`, or the catch-all will swallow `/api`.

## Run the test

```bash
cd backend
python -m tests.test_roundtrip
```

Encodes and rebuilds five ways — grayscale open, grayscale locked, colour open,
colour locked, and rendered text — through the real int16 WAV container.
Every path should report a mean pixel error of `0.000`.

## Try it end to end

1. **Send files** → pick Live doodle → draw something → **Save drawing**
2. Turn on **Lock this transmission**, enter two 11-digit numbers and a PIN
3. **Send**, then click the `output.wav` card to see its waveform, and press play
4. **Download audio**
5. **Receive** → drop that file in → it reports the transmission is locked
6. Enter the same numbers and PIN → **Unlock and rebuild**

Try a wrong PIN first. It rebuilds to static, because the scramble gets
reversed with the wrong permutation — which is the point.
