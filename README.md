# TractusTemp

Temporary GPU bootstrap ingester for TractusMind.

This repository is intentionally disposable. It is used to run a one-shot Tractus-X corpus ingestion job on a CUDA workstation and write the resulting vectors directly into the production TractusMind Qdrant collection.

## Goal

- Use local NVIDIA GPUs for dense embeddings.
- Keep the same TractusMind source definitions, chunking, dense model, sparse model and Qdrant payload format.
- Avoid OpenAI usage during ingestion.
- Exit after all requested sources are processed.

## Expected host

- Linux
- Docker + Docker Compose
- NVIDIA Container Toolkit
- CUDA-capable NVIDIA GPUs

## Quick start

1. Copy `.env.example` to `.env` and fill in the production endpoints/credentials.
2. Run:

```bash
docker compose up --build --abort-on-container-exit
```

3. When complete:

```bash
docker compose down
```

The container removes itself from the workflow after the one-shot job exits; this repository can be deleted afterwards.
