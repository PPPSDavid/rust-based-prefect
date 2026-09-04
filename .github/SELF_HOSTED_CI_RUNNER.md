# Self-hosted GitHub Actions runner (Linux aarch64 wheels)

The **`wheel-linux-aarch64`** CI job and the **Linux aarch64** matrix leg in **Publish to TestPyPI** / **Publish to PyPI** use:

```yaml
runs-on: [self-hosted, linux, ARM64]
```

So **`cibuildwheel`** runs on a **native ARM64** machine (for example a Raspberry Pi with **64-bit OS**) instead of QEMU on `ubuntu-latest`. You must register a runner that exposes the default labels **`self-hosted`**, **`Linux`**, and **`ARM64`** (GitHub assigns these when the runner binary matches an aarch64 Linux host).

## Prerequisites (on the Pi)

- **64-bit Linux** on ARM (`uname -m` → `aarch64`).
- **Outbound HTTPS** to GitHub and container registries.
- **Docker** — `cibuildwheel` uses Docker on Linux. Example (Debian / Raspberry Pi OS):

  ```bash
  sudo apt-get update
  sudo apt-get install -y docker.io
  sudo usermod -aG docker "$USER"
  ```

  Sign out and back in (or reboot) so **`docker`** group applies. Verify: `docker run --rm hello-world`.

- **CPython 3.12–3.14 wheels on aarch64** are produced **inside** `cibuildwheel`’s manylinux Docker images (`CIBW_BUILD` includes **`cp311-*`** through **`cp314-*`**); the Pi host only supplies **`cibuildwheel`** via the ephemeral **venv**—no host Python 3.12+ install required.

- **Python 3.11.x** as **`/usr/bin/python3`** (Debian **bookworm** ships 3.11). The aarch64 jobs **do not** use `actions/setup-python` because GitHub’s manifest does not publish an arm64 + Debian 12 build for that action; CI asserts `sys.version_info[:2] == (3, 11)` instead.
- **PEP 668:** Raspberry Pi OS / Debian mark the system interpreter as **externally managed**, so workflows install **`pip`** / **`cibuildwheel`** into a short-lived **`venv`** under `$RUNNER_TEMP`, then prepend its **`bin`** to **`GITHUB_PATH`** for later steps.

## Register the runner (GitHub UI)

1. Repository → **Settings** → **Actions** → **Runners** → **New self-hosted runner**.
2. Choose **Linux** and **ARM64** — copy the download + config commands shown there (token is single-use and expires).

## Install and run

On the Pi, use the **exact** `mkdir`, `curl`, and `tar` commands from the GitHub **New self-hosted runner** page (they pin the correct **linux-arm64** bundle). Then run `./config.sh` with the `--url` and `--token` shown there:

```bash
./config.sh --url https://github.com/OWNER/REPO --token RUNNER_REGISTRATION_TOKEN
```

During **`config.sh`**:

- Accept defaults so the runner is labeled **`self-hosted`**, **`Linux`**, **`ARM64`** (do **not** replace aarch64 builds with only custom labels unless you also update workflow **`runs-on`** to match).
- Optional: run as a service:

  ```bash
  sudo ./svc.sh install
  sudo ./svc.sh start
  ```

The runner should appear as **Idle** under **Settings → Actions → Runners**.

## Security (public repositories)

For **public** repos, GitHub **does not** run workflows from **fork** PRs on self-hosted runners from the base repo (see GitHub docs on self-hosted runner security). Pushes to branches in the **same** repo still use your runner.

## Operations

- If the Pi is **offline**, jobs targeting **`[self-hosted, linux, ARM64]`** stay **queued** until the runner reconnects or the run is cancelled.
- One runner executes **one job** at a time unless you add more runners or larger hardware.

## Changing labels

If you use **custom labels** only (for example `ironflow-pi`), update workflow **`runs-on`** in:

- `.github/workflows/ci.yml` → job **`wheel-linux-aarch64`**
- `.github/workflows/publish-testpypi.yml` and **`publish-pypi.yml`** → matrix row **`linux-aarch64`**

so they match your runner’s labels exactly.
