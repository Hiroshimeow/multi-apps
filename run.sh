#!/bin/bash

# Ensure script is run from its directory
cd "$(dirname "$0")"

# Check if python3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed."
    exit 1
fi

# Optional: Auto-activate conda env for the TOOL ITSELF (if needed)
# source ~/miniconda3/etc/profile.d/conda.sh
# conda activate ana11

# Run the CLI
python3 cli.py "$@"
