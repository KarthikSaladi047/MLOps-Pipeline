# MLOps-Pipeline

An end-to-end **GitOps-driven MLOps pipeline** that takes a machine learning model from raw dataset to a production inference endpoint without a single manual `kubectl apply`. Every push to `main` retrains the model, versions it, ships the artifact to an object store, and triggers ArgoCD to roll out the new model to a KServe-served endpoint on Kubernetes.

Built around the classic **Iris classification** problem to keep the ML simple — the focus here is the **infrastructure and automation around the model**, not the model itself.

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Layout (Branch Strategy)](#repository-layout-branch-strategy)
- [End-to-End Pipeline Flow](#end-to-end-pipeline-flow)
- [Components](#components)
- [Getting Started](#getting-started)
- [Required Secrets & Variables](#required-secrets--variables)
- [Local Development](#local-development)
- [Links](#links)

---

## Architecture

```
        ┌────────────────────┐
        │   Developer push   │
        │   to main branch   │
        └─────────┬──────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │   GitHub Actions Pipeline    │
   │  (.github/workflows/         │
   │       pipeline.yml)          │
   └─────────┬────────────────────┘
             │
             │  1. dvc pull (dataset from DagsHub)
             │  2. python train.py
             │  3. MLflow log + register model
             │  4. Upload artifact to MinIO (S3)
             │  5. Patch kserve/inference.yaml
             │     on the `argo` branch
             ▼
   ┌──────────────────────────────┐
   │       GitOps Repo            │
   │     (argo branch)            │
   │  - kserve/inference.yaml     │
   │  - ui/ui-deployment.yaml     │
   └─────────┬────────────────────┘
             │  ArgoCD watches & syncs
             ▼
   ┌──────────────────────────────┐
   │     Kubernetes Cluster       │
   │                              │
   │  ┌────────────────────────┐  │
   │  │  KServe InferenceSvc   │◄─┼── pulls model from MinIO
   │  │  (MLServer / MLflow)   │  │
   │  └──────────┬─────────────┘  │
   │             │                │
   │  ┌──────────▼─────────────┐  │
   │  │  Gradio UI (NodePort)  │◄─┼── user opens browser
   │  └────────────────────────┘  │
   └──────────────────────────────┘
```

---

## Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3.10 |
| ML Framework | scikit-learn 1.4.1 (RandomForestClassifier) |
| Data Versioning | DVC + DagsHub (S3-backed remote) |
| Experiment Tracking | MLflow 2.3.1 (hosted on DagsHub) |
| Model Registry | MLflow Registry + MinIO (S3-compatible) for raw artifacts |
| CI | GitHub Actions |
| Container Registry | GHCR (`ghcr.io/karthiksaladi047/mlops-pipeline`) |
| GitOps / CD | ArgoCD |
| Model Serving | KServe v0.16.0 (RawDeployment mode) + MLServer |
| Inference UI | Gradio (V2 Inference Protocol client) |
| TLS / Certs | cert-manager |
| Orchestration | Kubernetes |

---

## Repository Layout (Branch Strategy)

This repo uses **branch-based separation of concerns** so the CI loop on `main` never has to touch the deployment manifests directly — it patches them on the `argo` branch instead, keeping a clean GitOps boundary.

### `main` — ML code & training pipeline
```
├── .github/workflows/pipeline.yml   # CI: train → upload → patch GitOps
├── data/
│   └── iris.csv.dvc                 # DVC pointer to dataset on DagsHub
├── .dvc/config                      # DVC remote config (DagsHub)
├── train.py                         # Training script (RF + MLflow logging)
└── requirements.txt
```

### `argo` — GitOps manifests (continuously reconciled by ArgoCD)
```
├── kserve/inference.yaml            # KServe InferenceService (auto-patched by CI)
└── ui/ui-deployment.yaml            # Gradio UI Deployment + NodePort Service
```

### `infra` — Cluster bootstrap
```
├── README.md                        # Cluster setup instructions
└── argo-apps/
    ├── argo-app-kserve.yaml         # ArgoCD Application for kserve/
    ├── argo-app-ui.yaml             # ArgoCD Application for ui/
    └── kserve-sa.yaml               # ServiceAccount + Secret for MinIO access
```

### `ui` — Gradio frontend source
```
├── app.py                           # Gradio app calling KServe V2 endpoint
└── Dockerfile.ui                    # Container image (pushed to GHCR)
```

---

## End-to-End Pipeline Flow

When you push to `main`, the following happens automatically:

1. **Checkout & dependencies** — GitHub Actions checks out the repo and installs `requirements.txt`.
2. **Pull dataset** — `dvc pull -r origin` retrieves `data/iris.csv` from the DagsHub DVC remote using basic auth.
3. **Train model** — `train.py` runs:
   - Splits the data 80/20.
   - Trains a `RandomForestClassifier(n_estimators=50)`.
   - Logs params, metrics, and the model to MLflow on DagsHub.
   - **Accuracy gate**: if accuracy < 0.85, the run fails and the deployment is blocked.
   - Registers the model as `iris-prod-model` in the MLflow Registry.
   - Exports the MLflow-format model to `exported_model/`.
4. **Push artifact** — The MinIO client (`mc`) uploads `exported_model/` to `play.min.io` under `ml-models/iris/<RUN_ID>/model/`.
5. **GitOps patch** — Checks out the `argo` branch, runs `sed` on `kserve/inference.yaml` to replace `storageUri` with the new `s3://...` path, and pushes the commit.
6. **ArgoCD sync** — ArgoCD detects the manifest change on `argo` and reconciles the KServe `InferenceService`. KServe pulls the new model from MinIO and rolls out a fresh predictor pod.
7. **Serve** — The Gradio UI (already deployed) calls the new predictor at `http://iris-classifier-predictor.ml-serving.svc.cluster.local/v2/models/iris-classifier/infer`.

---

## Components

### Training (`train.py`)
Trains a Random Forest on the Iris dataset, logs everything to MLflow, gates on accuracy, and exports the model in MLflow's flavor format so KServe + MLServer can serve it directly.

### Inference Service (`kserve/inference.yaml` on `argo`)
A KServe `InferenceService` using the `kserve-mlserver` runtime with `modelFormat: mlflow`. The `storageUri` is the only field the CI ever touches — everything else (resources, SA, namespace) is static.

### Inference UI (`app.py` on `ui`)
A Gradio app that exposes four sliders (sepal/petal length/width), constructs a [V2 Inference Protocol](https://kserve.github.io/website/master/modelserving/data_plane/v2_protocol/) payload, calls the in-cluster KServe endpoint, and maps the returned class index (`0/1/2`) back to a flower name (Setosa / Versicolor / Virginica). Image is built and pushed to GHCR.

### Cluster Bootstrap (`infra` branch)
One-time setup to install ArgoCD, cert-manager, KServe (CRDs + controller in RawDeployment mode), and apply the three ArgoCD `Application` resources that wire the cluster to this repo.

---

## Getting Started

### 1. Fork & clone
```bash
git clone https://github.com/KarthikSaladi047/MLOps-Pipeline.git
cd MLOps-Pipeline
```

### 2. Bootstrap the cluster (one-time)
Switch to the `infra` branch and follow the steps in [infra/README.md](https://github.com/KarthikSaladi047/MLOps-Pipeline/blob/infra/README.md):

```bash
# Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Install cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml

# Install KServe (RawDeployment mode)
kubectl create namespace kserve
helm install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd --version v0.16.0 -n kserve --wait
helm install kserve oci://ghcr.io/kserve/charts/kserve --version v0.16.0 -n kserve \
  --set kserve.controller.deploymentMode=RawDeployment --wait

# Wire ArgoCD to this repo
kubectl apply -f argo-apps/kserve-sa.yaml
kubectl apply -f argo-apps/argo-app-kserve.yaml
kubectl apply -f argo-apps/argo-app-ui.yaml
```

### 3. Trigger the pipeline
Push any commit to `main` — GitHub Actions does the rest. Watch the run under the **Actions** tab.

### 4. Access the UI
The Gradio service is exposed as a `NodePort` on port `30500`:
```
http://<any-node-ip>:30500
```

---

## Required Secrets & Variables

Configure these in **Settings → Secrets and variables → Actions** on your fork.

### Repository Secrets
| Name | Purpose |
|---|---|
| `DAGSHUB_USER_NAME` | DagsHub username (for DVC pull + MLflow auth) |
| `DAGSHUB_PASSWORD` | DagsHub token / password |
| `MINIO_ACCESS_KEY` | MinIO access key for the model bucket |
| `MINIO_SECRET_KEY` | MinIO secret key |

### Repository Variables
| Name | Default | Purpose |
|---|---|---|
| `MINIO_BUCKET` | `ml-models` | Bucket where exported model artifacts are stored |

### Cluster Secret
The `dagshub-s3-secret` in `argo-apps/kserve-sa.yaml` holds the MinIO credentials KServe uses to pull the model. Replace the placeholders before applying.

---

## Local Development

### Run the training script locally
```bash
pip install -r requirements.txt

export MLFLOW_TRACKING_URI=https://dagshub.com/KarthikSaladi047/MLOps-Pipeline.mlflow
export MLFLOW_TRACKING_USERNAME=<your-dagshub-user>
export MLFLOW_TRACKING_PASSWORD=<your-dagshub-token>

dvc pull
python train.py
```

### Run the UI locally (against a port-forwarded KServe)
```bash
git checkout ui

# Port-forward the in-cluster predictor
kubectl -n ml-serving port-forward svc/iris-classifier-predictor 8080:80 &

export KSERVE_INFERENCE_URL=http://localhost:8080/v2/models/iris-classifier/infer
pip install gradio requests
python app.py
# Open http://localhost:7860
```

---

## Links

- **DagsHub project**: https://dagshub.com/KarthikSaladi047/MLOps-Pipeline
- **MLflow tracking server**: https://dagshub.com/KarthikSaladi047/MLOps-Pipeline.mlflow
- **MinIO (artifact store)**: https://play.min.io
- **Branches**:
  - [`main`](https://github.com/KarthikSaladi047/MLOps-Pipeline/tree/main) — training code
  - [`argo`](https://github.com/KarthikSaladi047/MLOps-Pipeline/tree/argo) — GitOps manifests
  - [`infra`](https://github.com/KarthikSaladi047/MLOps-Pipeline/tree/infra) — cluster bootstrap
  - [`ui`](https://github.com/KarthikSaladi047/MLOps-Pipeline/tree/ui) — Gradio frontend

---

## License

MIT — feel free to fork, adapt, and use as a reference architecture for your own MLOps stack.
