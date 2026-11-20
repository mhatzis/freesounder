import sys
from pathlib import Path

OTA_LIB = Path(__file__).resolve().parents[2] / "pi-gen/stage-freesounder/01-filesystem/files/usr/lib/freesounder/ota"
sys.path.insert(0, str(OTA_LIB))
