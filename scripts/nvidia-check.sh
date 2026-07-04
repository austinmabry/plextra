#!/usr/bin/env bash
# Verify the NVIDIA GPU is reachable from inside a container and NVENC works.
set -euo pipefail

if ! command -v nvidia-smi >/dev/null; then
    echo "nvidia-smi not found on host — install the NVIDIA driver first."
    exit 1
fi

echo "--- Host GPU ---"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

echo "--- GPU visible inside a container ---"
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi \
        --query-gpu=name --format=csv,noheader 2>/dev/null; then
    echo "OK: containers can see the GPU."
else
    echo "FAILED: install/configure nvidia-container-toolkit, then:"
    echo "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    exit 1
fi

echo "--- NVENC encode test (1s of video through h264_nvenc) ---"
if docker run --rm --gpus all jellyfin/jellyfin:latest \
        /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -loglevel error \
        -f lavfi -i testsrc=duration=1:size=1280x720:rate=30 \
        -c:v h264_nvenc -f null - ; then
    echo "OK: NVENC encoding works."
else
    echo "FAILED: NVENC not usable — check driver version and GPU NVENC support."
    exit 1
fi
