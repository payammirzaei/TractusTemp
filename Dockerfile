FROM nvidia/cuda:13.0.2-cudnn-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG TRACTUSMIND_REF=fc53778908d6e8bb7d30059f719a67e61b5450f0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git python3.12 python3-pip python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

RUN pip install --upgrade pip setuptools wheel

RUN git clone https://github.com/payammirzaei/TractusMind.git /opt/tractusmind \
    && cd /opt/tractusmind \
    && git checkout "${TRACTUSMIND_REF}" \
    && pip install --no-deps -e /opt/tractusmind

RUN pip install \
    "fastembed-gpu>=0.7,<1.0" \
    "qdrant-client>=1.18,<2.0" \
    "fastapi>=0.115,<1.0" \
    "pydantic>=2.9,<3.0" \
    "pydantic-settings>=2.6,<3.0" \
    "httpx>=0.27,<1.0" \
    "structlog>=24.4,<27.0" \
    "tree-sitter>=0.26,<0.27" \
    "tree-sitter-language-pack>=1.14.1,<2.0" \
    "prometheus-client>=0.25,<1.0" \
    "opentelemetry-api>=1.44,<1.45" \
    "opentelemetry-sdk>=1.44,<1.45" \
    "opentelemetry-exporter-otlp-proto-http>=1.44,<1.45" \
    "opentelemetry-instrumentation-fastapi>=0.65b0,<0.66" \
    "msgpack>=1.2.1,<2.0"

WORKDIR /workspace
COPY src /workspace/src
COPY scripts /workspace/scripts

ENV PYTHONPATH=/opt/tractusmind:/workspace
ENV HF_HOME=/models/huggingface
ENV FASTEMBED_CACHE_PATH=/models/fastembed
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.ingest"]
