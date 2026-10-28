# AquaMesh_Digital_Twin
Run guide only — the full explanation (logic, folders, calibration, hardware, API) is in `../README.md`.

    pip install -r requirements.txt
    python aquamesh_pi.py             # live hardware only
    python3 tests/test_engine.py      # tests         open http://localhost:8000 → overview; press 9 for Reason

The dashboard reads `/api/twin/state`; sensor values appear only when received from hardware. The PC gateway loads its Gemini key from the local `.env` file. See [`docs/DASHBOARD.md`](docs/DASHBOARD.md) for the camera upload and analysis API.
