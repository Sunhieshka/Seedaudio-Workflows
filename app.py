"""Short Drama Pipeline: script -> prompts -> sheets -> asset library -> shots -> combine -> Seed Audio redub.

Run:  python app.py   (then open http://127.0.0.1:8100)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
# Credentials: this project's .env (see .env.example).
ENV_PATH = ROOT / ".env"
if ENV_PATH.is_file() and load_dotenv(ENV_PATH):
    print(f"Loaded credentials from {ENV_PATH}")
else:
    print("WARNING: no .env loaded — copy .env.example to .env and fill in the keys.")

sys.path.insert(0, str(ROOT))

from pipeline.server import run  # noqa: E402

if __name__ == "__main__":
    run(os.getenv("PIPELINE_HOST", "127.0.0.1"), int(os.getenv("PIPELINE_PORT", "8100")))
