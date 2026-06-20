# Deploying VesperClaw to your VPS (Ubuntu/Debian)

Runs the agent 24/7 as two `systemd` services — the trading loop and the dashboard —
so it survives reboots and restarts on crash. Your laptop is not involved once deployed.

## What runs
| Service | What | Exposure |
|---|---|---|
| `vesperclaw-loop` | the autonomous trading loop (`live_paper`) | internal |
| `vesperclaw-dashboard` | Streamlit glass box | `http://<your-vps-ip>:8501` |

---

## Option A — deploy from a GitHub repo (recommended)

On your **laptop**, push the project to GitHub first (see below). Then on the **VPS**:

```bash
# log in to your VPS, then:
export REPO_URL=https://github.com/YOUR_USERNAME/vesperclaw.git
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/vesperclaw/main/deploy/deploy.sh -o deploy.sh
# (or clone the repo and run bash deploy/deploy.sh)
bash deploy.sh
```

The script installs Python, creates the venv, installs deps, sets up `.env`, and starts both services.

## Option B — copy files up manually (no GitHub)

From your **laptop** (PowerShell), copy the project to the VPS, then run the deploy script there:

```powershell
# from C:\bitget  (replace USER@HOST)
scp -r . root@YOUR_VPS_IP:/opt/vesperclaw
```
Then on the VPS:
```bash
cd /opt/vesperclaw && bash deploy/deploy.sh
```

---

## Add your keys (required once)

The deploy script creates `/opt/vesperclaw/.env` from the example. Edit it:

```bash
nano /opt/vesperclaw/.env
```
Set at least:
```
LLM_PROVIDER=qwen
QWEN_API_KEY=your-real-qwen-key
RUN_MODE=live_paper
```
Then restart the loop:
```bash
systemctl restart vesperclaw-loop
```

> **Never commit `.env`** — it's gitignored. Your keys live only on the VPS.

---

## Verify it's running

```bash
systemctl status vesperclaw-loop vesperclaw-dashboard
journalctl -u vesperclaw-loop -f          # live loop logs
```
Open the dashboard: **`http://<your-vps-ip>:8501`**

If you can't reach it, open the port:
```bash
ufw allow 8501/tcp        # if using ufw
```
…and make sure your VPS provider's firewall/security-group also allows TCP 8501.

---

## Updating after code changes

Push changes from your laptop, then on the VPS:
```bash
cd /opt/vesperclaw && bash deploy/deploy.sh update
```

---

## Generating a trade log fast (for the submission)

`live_paper` accumulates trades slowly (15-min cycles). To produce a full CSV trade
log quickly, run a fast demo once on the VPS (or your laptop):
```bash
/opt/vesperclaw/.venv/bin/python /opt/vesperclaw/main.py --mode fast_demo --reset
```
This replays 1-minute candles and fills `data/trade_log.csv`.

---

## Security notes
- You log in as **root with a password** — consider adding an SSH key and disabling
  password login later; root+password is the most brute-forced setup on the internet.
- The dashboard on `0.0.0.0:8501` is **public**. It's read-only (no controls that move
  money — paper mode only), but anyone with the IP can view it. That's fine for judging.
