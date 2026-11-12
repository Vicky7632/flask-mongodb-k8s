# Flask + MongoDB on Kubernetes

A small Flask REST API backed by MongoDB, containerized with Docker and deployed to the local Docker Desktop Kubernetes cluster. Kubernetes runs MongoDB as a single-replica StatefulSet with persistent storage, exposes Flask through a NodePort, and scales Flask from 2 to 5 replicas based on CPU utilization.

## Architecture

```text
Client
  |
  | http://localhost:30082
  v
Flask NodePort Service (faralpha)
  |
  v
Flask Deployment (2-5 pods; Gunicorn, port 5000)
  |
  | authenticated MongoDB URI
  v
MongoDB ClusterIP Service: mongodb:27017
  |
  v
MongoDB StatefulSet (1 pod; port 27017)
  |
  v
mongodb-pvc (1Gi, hostpath StorageClass)
```

The `mongodb-headless` Service supplies the StatefulSet's stable network identity. The regular `mongodb` ClusterIP Service is the address Flask uses for database connections. MongoDB root credentials are read from a Kubernetes Secret and are not stored in the Flask image.

## Prerequisites

- Docker Desktop with Docker Desktop Kubernetes enabled and running.
- `kubectl`, configured to use the `docker-desktop` context.
- Docker CLI available in PowerShell.
- Python 3.12 for running the Flask app locally.
- A cluster Metrics Server for HPA CPU metrics. Docker Desktop's local setup is described below.

Check the selected Kubernetes context before deploying:

```powershell
kubectl config current-context
kubectl --context docker-desktop get nodes
```

## Repository structure

```text
flask-mongodb-k8s/
├── app/
│   ├── app.py
│   └── requirements.txt
├── k8s/
│   ├── flask/
│   │   ├── deployment.yaml
│   │   ├── hpa.yaml
│   │   └── service.yaml
│   └── mongodb/
│       ├── pvc.yaml
│       ├── secret.example.yaml
│       ├── service.yaml
│       └── statefulset.yaml
├── screenshots/
│   └── .gitkeep
├── .dockerignore
├── .gitignore
├── Dockerfile
└── README.md
```

## Flask API

The API listens on port `5000` and uses these environment variables:

- `MONGODB_URI` (defaults to `mongodb://localhost:27017`)
- `MONGODB_DATABASE` (defaults to `flask_mongodb_k8s`)
- `MONGODB_COLLECTION` (defaults to `data`)

### Run locally

For local development, provide a MongoDB instance reachable from your machine. From the project root, create and activate a virtual environment, install dependencies, configure the connection, and start Flask:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\app\requirements.txt

$env:MONGODB_URI = "mongodb://localhost:27017"
$env:MONGODB_DATABASE = "flask_mongodb_k8s"
$env:MONGODB_COLLECTION = "data"
python .\app\app.py
```

For a MongoDB instance requiring authentication, set `MONGODB_URI` to that instance's connection URI instead. Do not put real credentials in source files or commit them.

### Endpoints

- `GET /` returns a welcome message and current UTC time in ISO 8601 format.
- `POST /data` accepts a JSON object and inserts it into MongoDB. It returns the inserted ID.
- `GET /data` returns stored documents as JSON, with MongoDB ObjectIds converted to strings.

PowerShell examples (with the app running locally):

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/"

$body = @{ message = "hello" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:5000/data" `
  -Method Post -ContentType "application/json" -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:5000/data"
```

Invalid JSON or non-object JSON submitted to `POST /data` receives a client error. Database failures return a generic service error without exposing connection details.

## Build the Flask image

Build from the project root. Docker Desktop Kubernetes can use the locally built image, and the Deployment uses `imagePullPolicy: IfNotPresent`.

```powershell
docker build -t faralpha-flask-api:local .
```

The root `Dockerfile` uses `python:3.12-slim`, installs the pinned dependencies, runs Gunicorn bound to `0.0.0.0:5000`, and switches to a non-root user.

## Deploy to Docker Desktop Kubernetes

Run commands from the project root. The steps below target only the `faralpha` namespace.

### 1. Select the context and create the namespace

```powershell
kubectl config use-context docker-desktop
kubectl create namespace faralpha --dry-run=client -o yaml | kubectl apply -f -
```

### 2. Create the MongoDB Secret safely

**`k8s/mongodb/secret.example.yaml` is an example only. Its values are placeholders, so do not apply it to the cluster.** Create the actual live Secret interactively instead. This PowerShell example prompts for the username and a masked password, builds a Secret in memory, and sends it to `kubectl` over standard input. The password is not placed in command history or project files, but it is briefly converted from a `SecureString` to a plaintext .NET string in process memory; removing the variable does not guarantee secure erasure of that memory.

```powershell
$mongoUser = Read-Host "MongoDB root username"
$securePassword = Read-Host "MongoDB root password" -AsSecureString
$passwordPointer = [IntPtr]::Zero

try {
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $mongoPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)

    $secretJson = @{
        apiVersion = "v1"
        kind = "Secret"
        metadata = @{
            name = "mongodb-secret"
            namespace = "faralpha"
        }
        type = "Opaque"
        stringData = @{
            MONGO_INITDB_ROOT_USERNAME = $mongoUser
            MONGO_INITDB_ROOT_PASSWORD = $mongoPassword
        }
    } | ConvertTo-Json -Depth 5

    $secretJson | kubectl --context docker-desktop create -f -
}
finally {
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    Remove-Variable mongoPassword, secretJson -ErrorAction SilentlyContinue
    $securePassword.Dispose()
}
```

The Secret is required before MongoDB starts. Use credentials that meet your local policy; this example does not supply or recommend a real password.

### 3. Create MongoDB storage and networking

```powershell
kubectl --context docker-desktop apply -f .\k8s\mongodb\pvc.yaml
kubectl --context docker-desktop apply -f .\k8s\mongodb\service.yaml
kubectl --context docker-desktop apply -f .\k8s\mongodb\statefulset.yaml
kubectl --context docker-desktop -n faralpha wait --for=condition=Ready pod/mongodb-0 --timeout=300s
kubectl --context docker-desktop -n faralpha get pvc mongodb-pvc
kubectl --context docker-desktop -n faralpha get endpoints mongodb
```

`pvc.yaml` requests `1Gi`, `ReadWriteOnce` storage using the `hostpath` StorageClass. If the class's dynamic provisioner is available, Kubernetes provisions a PersistentVolume and binds it to `mongodb-pvc`. This is local-cluster storage, not a portable or highly available storage solution.

The PVC check should show `Bound`; the `mongodb` Service endpoints should list the ready MongoDB pod on port `27017`. The StatefulSet mounts the claim at `/data/db`. The ordinary `mongodb` ClusterIP Service selects MongoDB pods on port `27017`; Flask connects to `mongodb:27017` through Kubernetes DNS. The `mongodb-headless` Service is assigned `clusterIP: None` and is the StatefulSet's governing service for stable pod DNS.

### 4. Deploy Flask and the HPA

Build the image first, then apply the Flask Deployment and its NodePort Service, followed by the HPA:

```powershell
kubectl --context docker-desktop apply -f .\k8s\flask\deployment.yaml
kubectl --context docker-desktop apply -f .\k8s\flask\service.yaml
kubectl --context docker-desktop apply -f .\k8s\flask\hpa.yaml
kubectl --context docker-desktop -n faralpha rollout status deployment/flask-api --timeout=300s
```

The Flask Service is a `NodePort`: access the API at `http://localhost:30082` on Docker Desktop.

```powershell
Invoke-RestMethod -Uri "http://localhost:30082/"
$body = @{ message = "hello from Kubernetes" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:30082/data" `
  -Method Post -ContentType "application/json" -Body $body
Invoke-RestMethod -Uri "http://localhost:30082/data"
```

## Resources and autoscaling

| Workload | CPU request | CPU limit | Memory request | Memory limit |
|---|---:|---:|---:|---:|
| Flask container | 100m | 500m | 128Mi | 256Mi |
| MongoDB container | 200m | 500m | 250Mi | 500Mi |

The `flask-api` HPA uses `autoscaling/v2`, targets average CPU utilization of `75%` of the Flask CPU request, and keeps between 2 and 5 replicas. The HPA requires Metrics Server and CPU requests on the target containers to calculate utilization.

### Docker Desktop Metrics Server note

Install Metrics Server from the official release manifest:

```powershell
kubectl --context docker-desktop apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

In this Docker Desktop cluster, kubelet certificates did not include the node IP in their IP SANs, so Metrics Server could not verify the kubelet certificate. The local development workaround is to **append** `--kubelet-insecure-tls` to the existing Metrics Server container arguments. This JSON Patch appends one item to the existing `args` list; it does not replace the list or any current arguments. The patch assumes the Metrics Server container is at index `0` and already has an `args` list:

```powershell
$patchFile = Join-Path $env:TEMP "metrics-server-args-patch.json"
@'
[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]
'@ | Set-Content -LiteralPath $patchFile -Encoding ascii

try {
    kubectl --context docker-desktop -n kube-system patch deployment metrics-server `
      --type=json --patch-file $patchFile
}
finally {
    Remove-Item -LiteralPath $patchFile -ErrorAction SilentlyContinue
}

kubectl --context docker-desktop -n kube-system rollout status deployment/metrics-server
kubectl --context docker-desktop -n kube-system get deployment metrics-server `
  -o jsonpath="{.spec.template.spec.containers[0].args}"
```

This option disables kubelet TLS certificate verification. It is **only a local development workaround and is not production-safe**. Do not use it as a production security configuration. Verify Metrics Server and the resource metrics API before expecting the HPA to show CPU values:

```powershell
kubectl --context docker-desktop -n kube-system rollout status deployment/metrics-server
kubectl --context docker-desktop get apiservice v1beta1.metrics.k8s.io
kubectl --context docker-desktop top nodes
kubectl --context docker-desktop top pods -n faralpha
```

### Controlled HPA test

The test below uses the already deployed local Flask image as a temporary load-generator pod, sends only `GET /` requests to the Flask Service, and runs 8 workers for 120 seconds. It inserts no database records. Run it from PowerShell; in a second PowerShell window, observe the HPA and pod metrics:

```powershell
$script = @'
import concurrent.futures
import time
import urllib.request

url = "http://flask-api:5000/"
deadline = time.monotonic() + 120
errors = []

def worker():
    count = 0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                response.read()
                count += 1
        except Exception as exc:
            errors.append(type(exc).__name__)
            time.sleep(0.1)
    return count

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    total = sum(pool.map(lambda _: worker(), range(8)))

print(f"Completed {total} successful GET requests; {len(errors)} request errors.")
'@
$payload = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($script))
$code = "exec(__import__('base64').b64decode('$payload'))"

kubectl --context docker-desktop run hpa-loadgen `
  --namespace=faralpha `
  --image=faralpha-flask-api:local `
  --image-pull-policy=IfNotPresent `
  --restart=Never --rm -i --attach=true --command -- python -c $code
```

In another terminal:

```powershell
kubectl --context docker-desktop -n faralpha get hpa flask-api --watch
kubectl --context docker-desktop -n faralpha top pods -l app=flask-api
```

`--rm` removes the temporary pod when the command completes. If it remains after interruption, remove it with:

```powershell
kubectl --context docker-desktop -n faralpha delete pod hpa-loadgen --ignore-not-found --wait=true
```

In the verified test, 8 workers ran for 120 seconds and completed **84,677 successful requests with 0 request errors**. The HPA scaled Flask **2 → 4 → 5** replicas, never exceeding its maximum of 5. After load stopped and the stabilization period elapsed, CPU returned below target and the Deployment scaled back to **2** replicas. These are results from that run, not a guarantee of identical counts on another run.

## Verification and troubleshooting

Run these commands with the `docker-desktop` context to inspect the resources:

```powershell
kubectl --context docker-desktop -n faralpha get pods -o wide
kubectl --context docker-desktop -n faralpha get deployments,statefulsets
kubectl --context docker-desktop -n faralpha get services
kubectl --context docker-desktop -n faralpha get pvc
kubectl --context docker-desktop -n faralpha get hpa
kubectl --context docker-desktop -n faralpha describe hpa flask-api
kubectl --context docker-desktop top nodes
kubectl --context docker-desktop top pods -n faralpha
kubectl --context docker-desktop -n faralpha logs deployment/flask-api
kubectl --context docker-desktop -n faralpha logs statefulset/mongodb
kubectl --context docker-desktop -n kube-system logs deployment/metrics-server
```

If HPA CPU targets show `<unknown>`, check Metrics Server rollout and `v1beta1.metrics.k8s.io` availability, then check `kubectl top pods -n faralpha`. HPA scale-down is intentionally delayed by its stabilization behavior; allow several minutes after stopping load.

## Cleanup

Deleting the namespace removes the assignment's Kubernetes resources:

```powershell
kubectl --context docker-desktop delete namespace faralpha
```

**Warning:** deleting `mongodb-pvc` or the namespace that contains it removes the claim. Depending on the StorageClass provisioner and PV reclaim policy, the backing volume and stored MongoDB data may also be deleted. Back up any data you need before cleanup. Docker images are separate from Kubernetes resources; remove the local image only if no longer needed:

```powershell
docker image rm faralpha-flask-api:local
```

## Design choices and alternatives

- MongoDB uses a one-replica StatefulSet and PVC to give its pod stable identity and persistent data. A managed database or a properly operated MongoDB replica set is a better fit for production availability.
- The `hostpath` StorageClass and Docker Desktop NodePort are convenient for a local assignment. Other environments commonly use a cloud CSI-backed StorageClass and an Ingress controller or cloud LoadBalancer.
- MongoDB root credentials are supplied through a Secret rather than embedded in the Flask image or Deployment URI. For production, use a dedicated least-privilege application database user and an external secret-management solution.
- Flask runs behind Gunicorn in a non-root container. The HPA scales on CPU utilization; alternatives include scaling on memory or application-specific metrics when those better reflect workload demand.
