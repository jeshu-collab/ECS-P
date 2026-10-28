#!/usr/bin/env bash
# Start the AquaMesh gateway + dashboard on the Raspberry Pi.   ./run_pi.sh          real hardware
#                                                               ./run_pi.sh --sim storm    simulated demo
cd "$(dirname "$0")"
exec python3 aquamesh_pi.py "$@"
