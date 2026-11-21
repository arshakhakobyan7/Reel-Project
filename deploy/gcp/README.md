# Reel on a temporary Google Cloud server (T-1018, D-070)

The user wants Reel online for a few days so that other people can try it, without the PC
being on. The Google Cloud Free Trial ($300 credit, 90 days) pays for it. Nothing is charged
unless the account is upgraded to a paid one. When the review is over, the VM is deleted.

## What runs where

| | |
|---|---|
| VM | Ubuntu 24.04, `e2-standard-8` (8 vCPU, 32 GB), 200 GB disk, in a European region |
| Reel | `/opt/reel`, the account `reel`, systemd service `reel` on 127.0.0.1:8080 |
| HTTPS | Caddy on 443 with a free Let's Encrypt certificate for `<ip-with-dashes>.sslip.io` |
| Settings | `config/reel.toml` copied from the PC. The server's differences are in `.env` (`server.env.template`): Linux paths, no TLS in Reel, `auth.store = "posix"`, CPU instead of the Intel GPU. |
| Secrets | `.env` (a new session secret, 0600) and `data/secrets/tokens.json` (Google token, 0600) |

## Steps

1. **The user:**
   - starts the Free Trial at <https://cloud.google.com/free> and gets the console's first
     project;
   - never presses "Activate full account".
2. **On the PC:** the free Google Cloud CLI.
   - Install: `winget install Google.CloudSDK`.
   - Then `gcloud auth login` (the user signs in in the browser) and
     `gcloud config set project <id>`.
3. **The VM and the firewall:**
   ```powershell
   gcloud compute instances create reel-demo --zone=europe-west3-a `
     --machine-type=e2-standard-8 --image-family=ubuntu-2404-lts-amd64 `
     --image-project=ubuntu-os-cloud --boot-disk-size=200GB --boot-disk-type=pd-balanced `
     --tags=reel-web
   gcloud compute firewall-rules create reel-web --allow=tcp:80,tcp:443 --target-tags=reel-web
   ```
   The host name is the VM's external address with dashes, plus `.sslip.io`, for example
   `34-90-12-7.sslip.io`.
4. **Copy** to `/opt/reel`:
   - the committed code (`git archive HEAD`);
   - `config/reel.toml`, `config/groups.toml`, `config/google-client.json`;
   - `data/reel.db`. It brings the admin account and the record of the video already on
     YouTube, so the same lesson is not published twice.
5. **Set up:** `sudo bash /opt/reel/deploy/gcp/setup.sh <host>` installs everything, then
   starts Reel and Caddy.
6. **The Google token** goes from the PC's Credential Manager into the server's owner-only
   store, through the SSH connection. It is never printed or written to a file on the way.
   Then `reel auth status` on the server.
7. **Check:**
   - `https://<host>/login`;
   - the test suite on the server: `sudo -u reel .venv/bin/python -m pytest -q`;
   - one lesson processed from start to end.
8. **Afterwards:** delete the VM and the firewall rule (or the whole project):
   ```powershell
   gcloud compute instances delete reel-demo --zone=europe-west3-a
   gcloud compute firewall-rules delete reel-web
   ```

## Notes

- While the server is used, do not upload the same lesson on the PC too. The two databases do
  not know about each other, so the lesson could be published twice.
- There is no Intel GPU on the VM. Video decoding, encoding (libx264) and the LLM run on
  the CPU, so a lesson takes longer than on the PC.
- `config/consent.json` does not exist yet. A published lesson therefore waits at
  Consent for the admin, as on the PC.
