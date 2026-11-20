# Opening the dashboard to the local network (HTTPS)

By default, the dashboard opens only on the computer that runs Reel
(`http://127.0.0.1:8080`). This guide lets staff open it from their own computers on the
same network, over HTTPS (T-1008, D-060).

## How it works

- Reel makes its own small **certificate authority (CA)** and, with it, a certificate for this
  computer's names and addresses (`reel cert`).
- Each staff computer trusts the CA **once**: it installs the file `reel-ca.crt`. After that,
  the browser shows the dashboard as an ordinary secure site, with no warning.
- The CA can sign only this computer's names and private network addresses (10.x, 172.16–31.x,
  192.168.x). Even if someone stole its key, they could not pose as Google, a bank or any other
  site to the staff computers that trust it.
- The private keys are encrypted. Their password is kept in Windows Credential Manager, never
  in a file.
- Everything is free and local: no domain, no account, no internet service.

## 1. On the computer that runs Reel (once)

Run these in the project folder (`C:\Users\User\Desktop\ReelProject`), in a VS Code terminal
or PowerShell.

1. **Stop Reel** if it runs (close it, or `scripts\autostart.ps1 stop`).
2. **Make the certificate:**
   ```powershell
   .venv\Scripts\reel cert
   ```
   It prints this computer's names and addresses, the files it made in `data\tls\`, and the
   CA's thumbprint. Write the thumbprint down: staff use it to check the file they got.
   - Another name, such as a name the school's network gives this computer, can be added with
     `--name reel.school.lan`; another address with `--ip 192.168.1.50`.
3. **Turn HTTPS on** in `config\reel.toml`, under `[web]` (the name is the one `reel cert`
   printed first):
   ```toml
   host = "0.0.0.0"
   tls = true
   base_url = "https://desktop-3gig9mv:8080"
   ```
   `host = "0.0.0.0"` means: answer on every network of this computer.
4. **Open the port in the firewall.** Start PowerShell with *Run as administrator*, go to the
   project folder, and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\network.ps1 allow
   ```
   The rule applies only to *private* networks, and only to computers of the same subnet.
   `scripts\network.ps1 status` shows whether this computer's network is private.
   - A school's shared Wi-Fi is usually *Public*, and should stay so: other people use it
     too. Then open only Reel's port, only to that network's address range, and leave the
     network public:
     ```powershell
     powershell -ExecutionPolicy Bypass -File scripts\network.ps1 allow -Subnet 192.168.32.0/22
     ```
     The range comes from this computer's address and prefix (`Get-NetIPAddress -AddressFamily
     IPv4`): for example 192.168.33.109 with prefix 22 is 192.168.32.0/22.
   - On your own network, you can instead set it to *Private* (Settings › Network & internet ›
     the network › Network profile type).
   - Open the firewall **before** Reel starts with `host = "0.0.0.0"`. Otherwise Windows may
     ask about Python, and pressing *Cancel* there, or leaving *Public* unticked, makes a rule
     that blocks Python and wins over Reel's rule. `status` lists such rules.
5. **Trust the CA on this computer too**, so its own browser has no warning: see step 2.
6. **Start Reel** again (`.venv\Scripts\reel serve`, or `scripts\autostart.ps1 start`). The
   log says `dashboard on https://0.0.0.0:8080; staff open https://…`.
   - If Windows Firewall still asks whether Python may communicate, run
     `scripts\network.ps1 status` after answering. If it lists a rule that blocks Python,
     delete that rule in *Windows Defender Firewall with Advanced Security* › *Inbound Rules*:
     it wins over Reel's rule.

## 2. On every staff computer (once)

1. Copy `data\tls\reel-ca.crt` from the Reel computer to the staff computer (a USB stick or a
   shared folder). **Copy only `reel-ca.crt`.** Never copy `reel-ca.key` or `server.key`.
2. Double-click `reel-ca.crt`. On the *Details* tab, check that the *Thumbprint* is the one
   `reel cert` printed.
3. *Install Certificate…* › *Current User* › *Place all certificates in the following store* ›
   *Browse…* › **Trusted Root Certification Authorities** › *Next* › *Finish*. Windows asks
   once more: answer *Yes*.
   - The same in PowerShell (Windows still asks for confirmation):
     ```powershell
     Import-Certificate -FilePath reel-ca.crt -CertStoreLocation Cert:\CurrentUser\Root
     ```
4. Restart the browser and open the address from `base_url`, for example
   `https://desktop-3gig9mv:8080`. Sign in with the account made with `reel user add`.

Chrome and Edge use the Windows certificate store. Firefox: if it still warns, import the
file in *Settings › Privacy & Security › Certificates › View Certificates › Authorities ›
Import*, and tick *Trust this CA to identify websites*. On a Mac, open the file in *Keychain
Access* and set it to *Always Trust*.

The other products that call Reel's API (Core Engine, Academy Library) must trust
`reel-ca.crt` in the same way, and use the `https://` address.

## Renewing

The certificate is valid for 13 months (browsers refuse longer ones). Reel's log warns 30 days
before the end. Then run `.venv\Scripts\reel cert` and restart Reel. The CA stays the same, so
**staff computers need nothing**.

`reel cert` also has to run again when this computer gets another address or name:

| Change | What to do | Staff computers |
|---|---|---|
| A new IP address (the router gave another one) | `reel cert`, restart | nothing |
| The computer was renamed | `reel cert`, restart; a new CA is made | install the new `reel-ca.crt` |
| The CA key or its password was lost | `reel cert --new-ca`, restart | install the new `reel-ca.crt` |

Tip: ask the network administrator to reserve this computer's IP address in the router, or use
its name in `base_url`.

`.venv\Scripts\reel cert --show` shows the current certificate without changing anything.

## Troubleshooting

| What you see | Why, and what to do |
|---|---|
| The page does not open from another computer | Reel runs with `host = "0.0.0.0"`? The firewall rule exists and the network is *Private* (`scripts\network.ps1 status`)? Both computers are on the same network? Try the IP address instead of the name. |
| "Your connection is not private" / `NET::ERR_CERT_AUTHORITY_INVALID` | `reel-ca.crt` is not installed in *Trusted Root Certification Authorities* of this user, or the browser was not restarted. |
| `NET::ERR_CERT_COMMON_NAME_INVALID` | The address opened is not in the certificate. Run `reel cert` (add `--name` or `--ip` if needed) and restart Reel. |
| `ERR_EMPTY_RESPONSE` or "connection reset" | The address starts with `http://`. Use `https://`. |
| Reel does not start: "has no certificate" | Run `reel cert` first. |
| Reel does not start: "cannot be opened with the password" | The key password in Credential Manager does not match the keys (another Windows user, or it was removed). Run `reel cert --new-ca`; staff install the new CA. |

## Turning it off

Set `tls = false`, `host = "127.0.0.1"` and `base_url = "http://127.0.0.1:8080"` in
`config\reel.toml`, restart Reel, and remove the firewall rule as administrator:
`powershell -ExecutionPolicy Bypass -File scripts\network.ps1 remove`. Staff computers can
remove the CA in `certmgr.msc` › *Trusted Root Certification Authorities* › *Certificates*
(look for "Reel local CA").
