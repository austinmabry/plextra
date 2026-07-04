#!/usr/bin/env bash
# Verify the NVIDIA GPU is reachable from inside a container and NVENC works.
# Tries the modern `--gpus all` path first, then the legacy `--runtime=nvidia`
# path (what Unraid's Nvidia Driver plugin provides).
set -euo pipefail

if ! command -v nvidia-smi >/dev/null; then
    echo "nvidia-smi not found on host — install the NVIDIA driver first."
    exit 1
fi

echo "--- Host GPU ---"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

echo "--- GPU visible inside a container ---"
GPU_FLAGS=""
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi \
        --query-gpu=name --format=csv,noheader 2>/dev/null; then
    GPU_FLAGS="--gpus all"
    echo "OK: containers can see the GPU (--gpus all)."
elif docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all \
        -e NVIDIA_DRIVER_CAPABILITIES=all \
        nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi \
        --query-gpu=name --format=csv,noheader 2>/dev/null; then
    GPU_FLAGS="--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all"
    echo "OK: containers can see the GPU (legacy --runtime=nvidia; on Unraid,"
    echo "    copy unraid/docker-compose.override.yml next to docker-compose.yml)."
else
    echo "FAILED: no container GPU access. Install/configure nvidia-container-toolkit"
    echo "  (on Unraid: the 'Nvidia Driver' plugin), then re-run."
    exit 1
fi

echo "--- NVENC encode test (1s of video through h264_nvenc) ---"
# shellcheck disable=SC2086 - GPU_FLAGS is intentionally word-split
if docker run --rm ${GPU_FLAGS} jellyfin/jellyfin:latest \
        /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -loglevel error \
        -f lavfi -i testsrc=duration=1:size=1280x720:rate=30 \
        -c:v h264_nvenc -f null - ; then
    echo "OK: NVENC encoding works."
else
    echo "FAILED: NVENC not usable — check driver version and GPU NVENC support."
    exit 1
fi
