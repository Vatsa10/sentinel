# NETRA — GPU-enabled deployment image.
#
# Built on NVIDIA's CUDA 12.8 runtime because Blackwell GPUs (sm_120) are not
# supported by the default PyTorch wheels. On a host without a GPU the image
# still runs: set NETRA_DEVICE=cpu.
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NETRA_DEVICE=cuda

# ffmpeg is required for RTSP ingestion and stream probing, not optional.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3-pip ffmpeg libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install PyTorch from the CUDA 12.8 index first so the heavy layer caches
# independently of application code.
RUN pip3 install --no-cache-dir \
        torch torchvision --index-url https://download.pytorch.org/whl/cu128

COPY requirements.txt .
RUN pip3 install --no-cache-dir \
        ultralytics easyocr opencv-python-headless numpy \
        fastapi "uvicorn[standard]" sqlalchemy requests python-multipart jinja2

COPY netra/ ./netra/
COPY run.py verify.py ./

# Model weights and the database live on a volume so they survive redeploys.
VOLUME ["/app/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/pipeline/status || exit 1

CMD ["python3", "run.py", "--host", "0.0.0.0", "--port", "8080"]
