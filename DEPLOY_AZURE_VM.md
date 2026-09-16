# Deploying Kinguard AI on an Azure VM (self-signed HTTPS)

This guide runs Kinguard AI on a single Azure VM using the Docker image's
built-in **self-signed HTTPS**. Browsers show a one-time certificate warning,
after which the dashboard — including the **Use my camera** feature — works,
because the page origin is `https://` (a secure context).

No domain and no reverse proxy are required.

---

## 0. Prerequisites

- An Azure VM (Ubuntu 22.04/24.04 recommended, 2 vCPU / 4 GB or more for smooth
  pose inference).
- The VM's **public IP address**.
- Docker installed on the VM (see below).

Optional, from your local machine with the Azure CLI:

```sh
az vm show -d -g <your-rg> -n <your-vm> --query publicIps -o tsv
```

---

## 1. Install Docker (skip if already installed)

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
```

Log out and back in (or run `newgrp docker`) so the group change applies, then
verify:

```sh
docker run --rm hello-world
```

---

## 2. Get the project onto the VM

Either clone it:

```sh
git clone <your-repo-url> kinguard-py
cd kinguard-py
```

or copy the current directory from your machine:

```sh
scp -r ./kinguard-py azureuser@<vm-public-ip>:~/kinguard-py
```

---

## 3. Build the image

From the project directory on the VM:

```sh
docker build -t kinguard-py .
```

The build downloads the pose model once, so the container runs fully offline
afterwards. Expect the build to take a few minutes the first time.

---

## 4. Run the container

```sh
mkdir -p /opt/kinguard/evidence
docker run -d \
  --name kinguard \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /opt/kinguard/evidence:/app/evidence \
  -e FALL_THRESHOLD_SECONDS=2.0 \
  kinguard-py
```

The image serves **HTTPS on port 8080** by default (self-signed cert, generated
at build time). `--restart unless-stopped` keeps it running across VM reboots.

Check it started cleanly:

```sh
docker ps                     # STATUS should show "healthy" after ~20s
docker logs -f kinguard       # follow the app logs (Ctrl+C to stop following)
```

---

## 5. Open port 8080 in Azure and on the VM

This is the step that most often makes the dashboard look "down". Open the port
in **both** places.

### Azure Network Security Group (NSG)

```sh
az vm open-port \
  --resource-group <your-rg> \
  --name <your-vm> \
  --port 8080 \
  --priority 1001
```

(Or add an inbound rule for TCP 8080 in the Azure portal → VM → Networking.)

### VM firewall

If `ufw` is active on the VM:

```sh
sudo ufw allow 8080/tcp
sudo ufw status
```

---

## 6. Open the dashboard

In a browser, go to:

```
https://<vm-public-ip>:8080
```

You will see a certificate warning (the cert is self-signed with `CN=kinguard`).
Accept it once:

- **Chrome / Edge:** "Advanced" → "Proceed to `<ip>` (unsafe)".
- **Firefox:** "Advanced…" → "Accept the Risk and Continue".
- **Safari:** "Show Details" → "visit this website".

After that the dashboard loads. Click **Use my camera** and grant camera
permission — the browser captures frames and the server runs pose detection on
them.

> Note: the VM has no camera of its own, so the browser-webcam mode is the way
> to demo this in the cloud.

---

## Verifying the secure-context requirement

The camera button only works when the page is served over HTTPS (or
`http://localhost`). If you deliberately switch the container back to plain HTTP:

```sh
docker run -d --name kinguard --restart unless-stopped \
  -p 8080:8080 -e TLS_CERT= -e TLS_KEY= kinguard-py
```

then `http://<vm-public-ip>:8080` will load the dashboard but **block camera
access** (`navigator.mediaDevices` is unavailable). Keep TLS enabled for the
webcam feature.

---

## Updating after a code change

```sh
cd kinguard-py
git pull                 # or re-copy the sources
docker build -t kinguard-py .
docker rm -f kinguard
docker run -d --name kinguard --restart unless-stopped \
  -p 8080:8080 -v /opt/kinguard/evidence:/app/evidence kinguard-py
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Page won't load / times out | NSG or VM firewall | Recheck step 5 |
| `docker ps` shows `unhealthy` or `Exited` | App crash / bad config | `docker logs kinguard` |
| Dashboard loads but camera blocked | Served over plain HTTP | Use HTTPS (don't clear `TLS_CERT`) |
| Certificate warning every visit | Self-signed cert | Expected; or use a domain + Caddy |
| Very low FPS | Underpowered VM | Use ≥2 vCPU; lower `JPEG_QUALITY` |

---

## Security reminder

The dashboard has **no authentication** and exposes camera/pose data and incident
history. Anyone who can reach `https://<vm-public-ip>:8080` can use it. For a
public deployment, restrict the NSG rule to your own IP address, put it behind
VPN/SSH tunnel, or add auth (Caddy `basic_auth`, or a domain + reverse proxy).
