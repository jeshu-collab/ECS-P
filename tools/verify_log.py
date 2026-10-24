#!/usr/bin/env python3
"""Verify the hash chain (and external checkpoints) of an AquaMesh evidence log.
Usage: python3 tools/verify_log.py data/evidence.sqlite"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aquamesh.evidence import Evidence
from aquamesh.util import Clock
if len(sys.argv) < 2:
    sys.exit(__doc__)
ev = Evidence(sys.argv[1], Clock())
print(json.dumps(ev.verify(), indent=1))
