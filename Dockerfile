FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG TRACTUSMIND_REF=ac9b0607117adb5c7c559824c5178b6d03e7caed

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git python3.12 python3-pip python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

RUN pip install --upgrade pip setuptools wheel

# Pull the exact TractusMind revision whose chunking, payload and collection
# conventions match production. Install the package without its CPU FastEmbed extra;
# this image intentionally supplies fastembed-gpu instead.
RUN git clone https://github.com/payammirzaei/TractusMind.git /opt/tractusmind \
    && cd /opt/tractusmind \
    && git checkout "${TRACTUSMIND_REF}" \
    && pip install --no-deps -e /opt/tractusmind

RUN pip install \
    "fastembed-gpu>=0.7,<1.0" \
    "qdrant-client>=1.18,<2.0" \
    "pydantic>=2.9,<3.0" \
    "pydantic-settings>=2.6,<3.0" \
    "httpx>=0.27,<1.0" \
    "structlog>=24.4,<27.0" \
    "tree-sitter>=0.26,<0.27" \
    "tree-sitter-language-pack>=1.14.1,<2.0" \
    "prometheus-client>=0.25,<1.0" \
    "msgpack>=1.2.1,<2.0"

WORKDIR /workspace
COPY src /workspace/src
COPY scripts /workspace/scripts

ENV PYTHONPATH=/opt/tractusmind:/workspace
ENV HF_HOME=/models/huggingface
ENV FASTEMBED_CACHE_PATH=/models/fastembed
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.ingest"]
