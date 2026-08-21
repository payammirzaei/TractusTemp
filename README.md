# TractusTemp

Disposable dual-GPU bootstrap ingester for TractusMind.

It runs the **same TractusMind source registry, GitHub discovery/fetching, SmartChunker, embedding text builders, sparse BM25 model, Qdrant collection naming and payload format** as production, but replaces the Railway CPU dense embedder with `fastembed-gpu` on the local workstation.

The Docker image is pinned to TractusMind revision `ac9b0607117adb5c7c559824c5178b6d03e7caed`, so the temporary job cannot silently drift away from the production corpus format.

## What it does

`GitHub -> TractusMind discovery -> fetch -> SmartChunker -> BGE GPU embeddings + BM25 -> production Qdrant`

- Uses both NVIDIA GPUs by default (`GPU_DEVICE_IDS=0,1`).
- Uses `BAAI/bge-small-en-v1.5`, matching TractusMind production.
- Uses `Qdrant/bm25`, matching TractusMind production hybrid retrieval.
- Does **not** call OpenAI and consumes **zero OpenAI tokens**.
- Uses a GitHub token only for authenticated source reads.
- Writes directly to the model-scoped TractusMind Qdrant collection.
- Removes stale vectors for a source after a successful full snapshot.
- Stores local completion/state bundles under `state/` so interrupted runs are safe to retry.
- Exits when the selected sources are complete. Nothing needs to remain running on this host.

## Host

Designed for the HP Z4 bootstrap workstation:

- Linux
- Docker Engine + Docker Compose v2
- NVIDIA Container Toolkit
- 2x NVIDIA RTX PRO 4500 Blackwell
- 125 GiB RAM

The host CUDA driver can be newer than the CUDA runtime inside the container. The image uses the NVIDIA CUDA 12.8 + cuDNN runtime.

## 1. Clone

```bash
git clone https://github.com/payammirzaei/TractusTemp.git
cd TractusTemp
```

## 2. Create local secrets

```bash
cp .env.example .env
```

If GitHub CLI is already authenticated, obtain the token with:

```bash
gh auth status
gh auth token
```

Put that token into `GITHUB_TOKEN=` in `.env`. It is ignored by Git and is never baked into the image.

You also need a **temporary externally reachable Qdrant endpoint** in `QDRANT_URL`. Do not expose an unauthenticated Qdrant service to the internet. Prefer a temporary Railway public endpoint protected with a Qdrant API key, then remove the endpoint after this bootstrap.

Required values:

```env
GITHUB_TOKEN=...
QDRANT_URL=https://...
QDRANT_API_KEY=...
```

There is no `OPENAI_API_KEY` because ingestion does not use OpenAI.

## 3. Preflight

```bash
bash scripts/preflight.sh
```

It verifies Docker, Compose, the host GPUs, NVIDIA Container Toolkit, and required local environment variables.

## 4. Run

```bash
bash run.sh
```

Or directly:

```bash
docker compose up --build --abort-on-container-exit
```

Typical output:

```text
[preflight] dense=BAAI/bge-small-en-v1.5 devices=[0, 1] parallel=2 batch=256
[tractusx-docs] produced 6,033 chunks; indexing
[tractusx-docs] 2,048/6,033 indexed (33.9%) @ ... chunks/s
...
================ BULK INGEST SUMMARY ================
Succeeded: 6/6
  ✓ tractusx-sdk: ... chunks
  ✓ tractusx-edc: ... chunks
  ✓ digital-twin-registry: ... chunks
  ✓ semantic-models: ... chunks
  ✓ tractusx-docs: ... chunks
  ✓ tractusx-release: ... chunks
=====================================================
```

## Resume after interruption

Simply run:

```bash
bash run.sh
```

With `SKIP_COMPLETED=true`, sources already completed at the same Git commit are skipped. Qdrant point upserts are idempotent, so restarting a partially completed source is safe.

To force one source to run again, remove its local state marker:

```bash
rm state/tractusx-docs.json
bash run.sh
```

To process only selected sources, set for example:

```env
SOURCE_IDS=tractusx-docs,tractusx-sdk
```

## Watch GPUs

In another terminal:

```bash
watch -n 1 nvidia-smi
```

or:

```bash
bash scripts/monitor.sh
```

## Stop and remove the temporary container

```bash
docker compose down
```

If you want to remove downloaded model caches too:

```bash
rm -rf .model-cache
```

Keep `state/` until TractusMind production state has been reconciled. Each successful state file includes the exact source commit and file SHA metadata needed to adopt that snapshot without repeating GPU embeddings.

## Important production rule

While this bootstrap writes a source, do **not** run a competing full sync of the same source from the Railway worker. Both use deterministic point IDs, so corruption is unlikely, but concurrent snapshot cleanup is unnecessary risk. Pause/avoid manual `Sync all` until this one-shot run is complete.

## Source allowlist

The source list is not duplicated here. It is read from the pinned TractusMind `config/sources.toml`, currently containing:

- `tractusx-sdk`
- `tractusx-edc`
- `digital-twin-registry`
- `semantic-models`
- `tractusx-docs`
- `tractusx-release`

That keeps the temporary bootstrap and production corpus definition identical.
