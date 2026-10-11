# UoM Email → Telegram → Your Android Phone Server

**A complete setup guide by MN Rizni Mohamed**

Source: [RizniMohamed/uom-email-telegram-monitor-template](https://github.com/RizniMohamed/uom-email-telegram-monitor-template)

This guide is for students who want new university emails in their own Telegram chat while an Android phone runs the Python monitor using Termux and Termux:Boot. Share this Markdown document with your batchmates. Everyone must configure their own account and bot.

If it helps you, please **star the original repository** and share it. Starring is optional. The project is released under the MIT license, with copyright © 2026 MN Rizni Mohamed.

## Contents

1. [Understand the system](#1-understand-the-system)
2. [Prepare your accounts and phone](#2-prepare-your-accounts-and-phone)
3. [Install Termux and Termux:Boot](#3-install-termux-and-termuxboot)
4. [Install the phone tools](#4-install-the-phone-tools)
5. [Create your Telegram bot](#5-create-your-telegram-bot)
6. [Choose your repository and install the project](#6-choose-your-repository-and-install-the-project)
7. [Test UoM TLS without credentials](#7-test-uom-tls-without-credentials)
8. [Find your Telegram chat ID privately](#8-find-your-telegram-chat-id-privately)
9. [Configure your private environment file](#9-configure-your-private-environment-file)
10. [Choose new setup or checkpoint migration](#10-choose-new-setup-or-checkpoint-migration)
11. [Start and verify monitoring](#11-start-and-verify-monitoring)
12. [Enable Termux:Boot](#12-enable-termuxboot)
13. [Set Android and Samsung background permissions](#13-set-android-and-samsung-background-permissions)
14. [Use the management commands and logs](#14-use-the-management-commands-and-logs)
15. [Understand and test automatic updates](#15-understand-and-test-automatic-updates)
16. [Optional: use your own private repository](#16-optional-use-your-own-private-repository)
17. [Recover from a failed update](#17-recover-from-a-failed-update)
18. [Backups, limitations, and troubleshooting](#18-backups-limitations-and-troubleshooting)
19. [Develop and contribute](#19-develop-and-contribute)
20. [License and author credit](#20-license-and-author-credit)

## 1. Understand the system

The phone is the server: it connects to the university IMAP inbox, then sends messages through Telegram's HTTPS Bot API. Telegram does not fetch your university email itself. You can read the resulting Telegram chat on the same phone or another device.

```text
UoM INBOX -- verified IMAPS :993 --> Android / Termux / Python
                                    |
                                    +-- HTTPS :443 --> Telegram bot --> your chat

Termux:Boot --> shell manager --> Python monitor
GitHub main --> tested updates --> manager --> safe restart / code rollback
```

The monitor polls INBOX every 30 seconds and leaves university emails unread. Credentials and the delivery checkpoint live in Termux's private HOME, outside downloaded code. The phone only downloads code; you make code changes in your own development repository.

## 2. Prepare your accounts and phone

You need:

- Your own working UoM email account and IMAP login credentials.
- A Telegram account and a bot you create yourself.
- An Android phone with a supported Termux installation, internet access, and enough storage for release environments.
- A GitHub account only if you want to star, create your own template repository, contribute, or customize updates. Reading the public source requires no GitHub login.

The phone needs outbound HTTPS and IMAPS connections. Private GitHub SSH access, if chosen later, also needs port 22. No inbound port, root access, USB debugging, or connected computer is required to run it. A computer is optional for development or migrating an existing monitor.

Use your own private Telegram chat for initial setup. Email content is deliberately forwarded to the configured chat, so choose the destination carefully.

## 3. Install Termux and Termux:Boot

For a new setup, install both apps from **the same distribution source**, for example both from F-Droid:

- [Termux on F-Droid](https://f-droid.org/packages/com.termux/)
- [Termux:Boot on F-Droid](https://f-droid.org/packages/com.termux.boot/)

Open Termux and let its initial setup complete. **Open Termux:Boot once** using its launcher icon. Its boot scripts will be added later, after the monitor is configured and working.

If you already have a working Termux installation, keep it and install the matching Boot app. Mixing app sources/signatures can prevent the apps from working together. Uninstalling Termux removes its private data, so preserve that data before ever changing installations. See the official [Termux installation guide](https://github.com/termux/termux-app#installation) and [Termux:Boot instructions](https://github.com/termux/termux-boot#how-to-use).

## 4. Install the phone tools

In Termux:

```bash
pkg update
pkg upgrade
pkg install python python-pip python-ensurepip-wheels git bash openssh coreutils util-linux tar gawk nano ca-certificates
python --version
git --version
command -v flock setsid timeout
```

The last command should show all three commands. `util-linux` supplies process locking and detached sessions; the pip/ensurepip packages support new virtual environments. Keep Termux's system pip managed by `pkg`. The application installs its dependencies separately inside release virtual environments. [Termux Python package reference](https://github.com/termux/termux-packages/blob/master/packages/python/build.sh).

Python 3.12+ is required. Python 3.14 is the CI target; the template has offline compatibility coverage on Python 3.14.6.

## 5. Create your Telegram bot

1. Open the verified [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`.
3. Choose a display name, then a unique bot username ending in `bot`.
4. Save the token privately. It controls your bot; never post it or enter it in a public chat-ID website.
5. Open the chat with your new bot and send `/start`.

For a group destination, add your own bot to the group, give it permission to send messages/files, and send a command addressed to it in that group, such as `/start@YourBotUsername`. Group chat IDs may be negative. Start with a private bot chat if you are unsure.

Telegram documents this process in its [official bot tutorial](https://core.telegram.org/bots/tutorial).

## 6. Choose your repository and install the project

For the simplest setup, use the public template directly. The phone will receive future commits from this project's `main` branch:

```bash
git clone https://github.com/RizniMohamed/uom-email-telegram-monitor-template.git ~/uom-email-telegram-monitor
cd ~/uom-email-telegram-monitor
bash deployment/termux/uom.sh install "$PWD"
export PATH="$HOME/.local/bin:$PATH"
```

The installer builds a release and runs offline tests. It does **not** start monitoring. If installation fails, inspect `~/.local/share/uom-monitor/releases/<commit>/validation.log`; the commit is shown by `git rev-parse HEAD` in the checkout.

Open `nano ~/.bashrc` and add this line once so future shells find the management command:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

If you want to control every update or customize the code, use GitHub's **Use this template → Create a new repository** first, and clone your own repository instead. Public repositories can be cloned over HTTPS without credentials. For a private repository, complete [step 16](#16-optional-use-your-own-private-repository) before cloning/installing. GitHub explains [template repositories](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template).

Use one checkout and one monitor per account. Automatic updates execute code from the chosen repository; choose a repository you control or a maintainer you trust. Offline tests are a functional check, not a sandbox for untrusted code.

## 7. Test UoM TLS without credentials

After successful installation:

```bash
~/.local/share/uom-monitor/current/.venv/bin/python -m uom_email_monitor --check-imap-tls
```

Success reports the verified TLS version and cipher. This checks the TLS handshake and IMAP greeting without logging in, selecting the inbox, sending Telegram messages, or changing state. The probe closes the transport directly without IMAP LOGOUT because UoM can close unauthenticated sessions.

If it fails, check the phone's internet connection, date/time, CA certificates, and outbound port 993. Do not disable certificate or hostname verification or lower OpenSSL security settings. The preserved cipher configuration is:

```text
ECDHE+AESGCM:ECDHE+CHACHA20:AES256-GCM-SHA384:AES128-GCM-SHA256
```

TLS 1.2 is the minimum.

## 8. Find your Telegram chat ID privately

Make sure you sent `/start` to your bot. Run this in Termux. It prompts for the token without putting it in shell history or a browser URL, and prints only chat IDs/types:

```bash
~/.local/share/uom-monitor/current/.venv/bin/python - <<'PY'
import getpass
import requests

token = getpass.getpass("Your bot token (hidden): ")
try:
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates", timeout=20
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        print("Telegram rejected the lookup; verify your bot token privately.")
    else:
        chats = {}
        for update in result.get("result", []):
            for kind in ("message", "edited_message", "channel_post"):
                message = update.get(kind, {})
                chat = message.get("chat", {})
                if "id" in chat:
                    chats[chat["id"]] = chat.get("type", "unknown")
        for chat_id, chat_type in chats.items():
            print(f"Chat ID: {chat_id} ({chat_type})")
        if not chats:
            print("No chats found. Send /start to your bot and try again.")
except Exception as exc:
    print(f"Lookup failed ({type(exc).__name__}); check connectivity/token privately.")
PY
```

Choose the ID for your intended destination. Do not screenshot or share the token. Use a new bot dedicated to this monitor so another application does not consume its updates. The lookup uses Telegram's official [getUpdates method](https://core.telegram.org/bots/api#getupdates).

## 9. Configure your private environment file

From Termux:

```bash
umask 077
cp -n ~/uom-email-telegram-monitor/.env.example ~/.local/share/uom-monitor/monitor.env
chmod 600 ~/.local/share/uom-monitor/monitor.env
nano ~/.local/share/uom-monitor/monitor.env
```

Fill the four blank credential fields in the editor:

| Field | Your value |
| --- | --- |
| `UOM_USERNAME` | Your own university IMAP username |
| `UOM_PASSWORD` | Your own university password |
| `TELEGRAM_BOT_TOKEN` | Token from your own BotFather bot |
| `TELEGRAM_CHAT_ID` | The numeric destination ID from step 8 |

Leave `UOM_IMAP_HOST=mail.uom.lk`, `UOM_IMAP_PORT=993`, `CHECK_INTERVAL=30`, and `RETRY_INTERVAL=30` unless your account/environment needs a change. The example contains no real credentials. Do not copy it over an already configured file.

Quote passwords containing spaces or `#`. `${...}` is treated literally; dotenv interpolation is disabled. Do not use a literal `$HOME` in the file's state path. The sample relative `./uom_monitor_state.json` resolves against this private environment directory, and the manager explicitly forces that same private path. Variables already exported in your shell override file entries, so clear obsolete exports if they cause confusion.

Validate configuration without logging in or writing state:

```bash
UOM_ENV_FILE="$HOME/.local/share/uom-monitor/monitor.env" \
UOM_STATE_FILE="$HOME/.local/share/uom-monitor/uom_monitor_state.json" \
~/.local/share/uom-monitor/current/.venv/bin/python -m uom_email_monitor --check-config
```

For a new installation, reporting no state is expected at this point. Existing users must preserve and migrate their state before starting.

## 10. Choose new setup or checkpoint migration

**Choose exactly one path below. Never create a new baseline if you already have a delivery checkpoint.** The manager refuses to start without a checkpoint to prevent accidental resets.

### A. Brand-new installation: create the first checkpoint

This path intentionally skips old messages already in INBOX. It begins forwarding messages newer than the initial baseline; it does not replay your whole email history.

Run the existing application once in the foreground using the private files:

```bash
umask 077
base="$HOME/.local/share/uom-monitor"
if [ -e "$base/uom_monitor_state.json" ]; then
    echo 'Checkpoint already exists; preserve it and skip first-run initialization.'
else
    UOM_ENV_FILE="$base/monitor.env" UOM_STATE_FILE="$base/uom_monitor_state.json" \
    "$base/current/.venv/bin/python" -m uom_email_monitor
fi
```

Wait for `Baseline established; existing emails ignored` and `Monitoring inbox...`, then press **Ctrl+C** and wait for exit. This is the first real login/monitoring run. It creates the checkpoint; any newer messages processed while it runs are recorded normally. If login or Telegram access fails, fix your configuration privately; do not create a fake/empty JSON checkpoint.

Confirm the file now exists and rerun the read-only check from step 9. It must report **existing state loaded**. Continue to step 11.

### B. Existing monitor: migrate its latest whole checkpoint

Prepare the phone first while the old monitor runs. At handoff, stop that monitor and wait for exit. Keep it stopped while Android is active: locks are local to a device and cannot coordinate two computers/phones.

For an existing Windows installation, use its project directory in PowerShell:

```powershell
# Stop the existing monitor with Ctrl+C and wait for exit before copying.
$checkpointBackupDir = Join-Path $env:USERPROFILE '.uom-monitor-backups'
New-Item -ItemType Directory -Path $checkpointBackupDir -Force | Out-Null
$checkpointStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$checkpointBackup = Join-Path $checkpointBackupDir "android-state-$checkpointStamp.json"
Copy-Item -LiteralPath .\uom_monitor_state.json -Destination $checkpointBackup
Get-FileHash -LiteralPath $checkpointBackup -Algorithm SHA256
```

Transfer that exact file through a trusted private method. It must include the full UID/UIDVALIDITY and any pending text/attachment/warning delivery status. Do not edit UIDs, reset the file, or use a stale backup.

If transferring through Android Downloads, grant access for this one-time copy, then move the runtime copy into private HOME:

```bash
termux-setup-storage
source_file="$HOME/storage/downloads/uom_monitor_state.json"
state_file="$HOME/.local/share/uom-monitor/uom_monitor_state.json"
sha256sum "$source_file"
# Compare against the stopped original monitor's SHA256 hash first.
if [ -e "$state_file" ]; then
    echo 'STOP: destination already has a checkpoint. Preserve and inspect it.'
else
    install -m 600 "$source_file" "$state_file"
    sha256sum "$state_file"
fi
```

Verify matching hashes, keep a private original backup, and remove the temporary shared-storage copy using your file manager. Run the read-only configuration/state check from step 9: **existing state loaded** is required.

If the phone has already monitored mail, preserve its newer checkpoint. To return to Windows later, stop the phone and transfer its latest whole checkpoint back; do not resume with a stale pre-migration file.

## 11. Start and verify monitoring

With a valid private configuration and initialized/migrated checkpoint, and any old monitor stopped:

```bash
uom start
uom status
uom logs
```

Verify one running manager and one running monitor. Send a harmless new email to your own university inbox. Check that it arrives in the intended Telegram chat and remains unread in the university inbox. Try a small PDF/photo and formatted message, then `uom restart` and another new email. Confirm already acknowledged deliveries are not replayed.

Do not start a second copy on another device with an independent checkpoint. Keep the phone online, with suitable battery/charging and storage conditions for your own use.

## 12. Enable Termux:Boot

After successful live verification, install the launcher:

```bash
mkdir -p ~/.termux/boot
install -m 700 ~/uom-email-telegram-monitor/deployment/termux/boot.sh ~/.termux/boot/10-uom-monitor
```

Termux:Boot must have been opened once. Reboot and unlock the phone, then use `uom status` and a new harmless email to verify actual startup. Android decides when the boot app is allowed to run; test this on your device rather than assuming it is immediate.

`uom start` sets an enabled marker. `uom stop` removes it, pausing Boot startup until you explicitly start again. The launcher invokes the existing manager, whose lock prevents duplicates. [Official Boot behavior and setup](https://github.com/termux/termux-boot#how-to-use).

## 13. Set Android and Samsung background permissions

Apply the available settings to **Termux and Termux:Boot**:

- Set app battery usage to **Unrestricted**, and allow background activity.
- Allow background data; exempt the apps from Data Saver if you use it.
- Allow notifications so the Termux running session remains visible.

On Samsung Galaxy phones, including the S23 Ultra:

1. Open **Settings → Apps → Termux → Battery → Unrestricted**; repeat for Boot where available.
2. Open **Settings → Battery / Device care → Battery → Background usage limits**.
3. Add the apps to **Never sleeping apps** and remove them from Sleeping/Deep sleeping lists.

Menu names vary by Android/One UI version. Samsung documents [background app restrictions](https://developer.samsung.com/mobile/app-management.html). Test screen-off operation and a temporary network interruption.

An optional wake lock can help keep CPU activity available, at the cost of battery:

```bash
touch ~/.local/share/uom-monitor/wake-lock
uom restart
# To disable later, remove the wake-lock marker and restart:
# rm ~/.local/share/uom-monitor/wake-lock
# uom restart
```

The manager requests/releases the Termux wake lock when available. If Android kills or force-stops the entire Termux app, reopen Termux and use `uom start`; a killed shell cannot restart its own app. Boot is not a guaranteed recovery mechanism for every Android force-stop. Termux also describes [Android process-killing limits](https://github.com/termux/termux-app#readme).

## 14. Use the management commands and logs

| Command | Result |
| --- | --- |
| `uom start` | Starts the manager and Python; repeated calls keep one instance |
| `uom stop` | Gracefully stops both, retains state, and pauses Boot |
| `uom restart` | Stops/starts with the same checkpoint |
| `uom status` | Process status, active commit SHA, and latest update failure |
| `uom logs` | Last 80 lines of each runtime log |
| `uom update` | Immediate update request; when stopped, only validates |

Runtime files:

```text
~/.local/bin/uom                         stable manager
~/uom-email-telegram-monitor/            Git checkout, no credentials/state
~/.local/share/uom-monitor/
  monitor.env                           private credentials
  uom_monitor_state.json                permanent delivery checkpoint
  uom_monitor_state.lock                Python lock; do not delete to bypass it
  current -> releases/<commit>           active code and virtual environment
  previous                              previous healthy code commit
  activation-pending                    interrupted-update recovery marker
  releases/<commit>/validation.log       dependency/offline-test output
  manager.log, monitor.log               runtime logs and rotated copies
```

Runtime logs rotate at 2 MiB with three archives per stream. Copy-and-truncate rotation can lose a few concurrent log lines; it does not modify the checkpoint. Validated releases are retained for recovery. Periodically review storage and remove only known inactive releases, preserving current/previous code, secrets, and all delivery state.

Shutdown allows up to 120 seconds before killing a stuck process. Child crash recovery is checked every five seconds; a bounded update operation can delay it. Network outages use the existing 30-second retry behavior, while failed GitHub fetches are retried on later update checks.

## 15. Understand and test automatic updates

Every five minutes the manager fetches `main` from the chosen origin. For a new commit it:

1. Archives the immutable SHA into a separate release and builds an isolated virtual environment.
2. Installs dependencies, compiles the package, and runs the full offline suite plus a dummy configuration check while the current monitor continues running.
3. Verifies that the candidate can read the private configuration/checkpoint without writing it.
4. Writes a recovery marker, stops the current Python process, and atomically switches the active code pointer.
5. Starts the candidate and requires a fresh successful inbox-progress log within 90 seconds while its process remains alive.
6. On activation failure, restores previous code and restarts it with the **latest shared checkpoint**. State is never rolled back with code.

An unchanged SHA does nothing and never restarts the monitor. Test failures block that commit until a new commit or manual `uom update`; fetch/install and health failures retry later. Interrupted activation is recovered on the next manager start. Failed updates appear in `uom status` and private logs.

GitHub Actions validates source separately; the phone independently runs the tests it downloaded. The manager does not check remote Actions status. Tests use dummy credentials and synthetic mail, not your university or Telegram account.

To test updates under your control, create your own repository from the template. Make a harmless README change on `main` using your development machine or GitHub editor, wait five minutes or run `uom update`, then check logs for `Update healthy` and the new SHA in `uom status`. Confirm a subsequent new email forwards normally and the checkpoint continues forward.

The stable shell manager and Boot launcher are not replaced automatically while supervising an update. After reviewing changes to these scripts:

```bash
uom stop
cd ~/uom-email-telegram-monitor
git pull --ff-only
bash deployment/termux/uom.sh install "$PWD"
install -m 700 deployment/termux/boot.sh ~/.termux/boot/10-uom-monitor
uom start
```

This preserves the environment file and state. The phone fetches source; it never commits or pushes automatically.

## 16. Optional: use your own private repository

Use **Use this template** on the public GitHub repository, create a repository in your own account, and choose Private. It should include the template's `main` branch. Public upstream cloning does not require an SSH key; the following is only for accessing your own private copy.

Generate a dedicated deploy key on the phone, once:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/uom_monitor_deploy -N '' -C 'android-uom-readonly'
cat ~/.ssh/uom_monitor_deploy.pub
```

Copy only the public `.pub` value into your own repository's **Settings → Deploy keys → Add deploy key**. Leave **Allow write access unchecked**. The private key stays on your phone. [GitHub's deploy-key instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

Obtain and verify GitHub's host key:

```bash
ssh-keyscan -t ed25519 github.com > ~/.ssh/github-host.candidate
ssh-keygen -lf ~/.ssh/github-host.candidate
```

Compare the SHA256 fingerprint with GitHub's [current official SSH fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints). Only after it matches:

```bash
cat ~/.ssh/github-host.candidate >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
rm ~/.ssh/github-host.candidate
nano ~/.ssh/config
```

Add this block while preserving any other hosts:

```sshconfig
Host uom-github
    HostName github.com
    User git
    IdentityFile ~/.ssh/uom_monitor_deploy
    IdentitiesOnly yes
    BatchMode yes
    StrictHostKeyChecking yes
    UserKnownHostsFile ~/.ssh/known_hosts
```

```bash
chmod 600 ~/.ssh/config ~/.ssh/uom_monitor_deploy
ssh -T uom-github
```

A successful GitHub authentication response says there is no shell and normally exits with status 1. Then clone your repository in place of the public command in step 6, replacing the placeholders:

```bash
git clone git@uom-github:YOUR_GITHUB_USERNAME/YOUR_REPOSITORY.git ~/uom-email-telegram-monitor
```

If already installed from the public template, stop the manager, change the existing checkout's origin with `git remote set-url origin git@uom-github:YOUR_GITHUB_USERNAME/YOUR_REPOSITORY.git`, then start again. Keep the same private state. Do not overwrite an existing checkout or create another active monitor. No GitHub token is required on the phone, and the read-only key prevents it pushing code.

## 17. Recover from a failed update

Usually the manager has already restored the previous code. Inspect:

```bash
uom status
uom logs
```

Read `releases/<failed-commit>/validation.log` for installation/test errors. Fix code in your own development repository and push a new commit, or use `uom update` to retry after resolving a transient problem. Never delete the checkpoint to make broken code start.

For an intentional manual code rollback:

```bash
uom stop
base="$HOME/.local/share/uom-monitor"
cat "$base/previous"
# Replace this placeholder with the reviewed previous 40-character commit SHA.
previous_sha='PASTE_PREVIOUS_COMMIT'
if [[ "$previous_sha" =~ ^[0-9a-f]{40}$ && -f "$base/releases/$previous_sha/.validated" ]]; then
    ln -s "$base/releases/$previous_sha" "$base/current.manual"
    mv -Tf "$base/current.manual" "$base/current"
fi
uom start
```

Polling will retry the newer `main`. To keep the rollback, revert the bad change in your development repository and publish the corrected `main`. Never roll back state with code. Preserve a pending-activation marker and its referenced releases if recovery itself fails; inspect logs and pointers before changing them.

## 18. Backups, limitations, and troubleshooting

Make a private checkpoint backup while stopped:

```bash
uom stop
umask 077
mkdir -p ~/.uom-monitor-backups
checkpoint_stamp="$(date +%Y%m%d-%H%M%S)"
cp ~/.local/share/uom-monitor/uom_monitor_state.json "$HOME/.uom-monitor-backups/state-$checkpoint_stamp.json"
uom start
```

Keep an additional private backup away from the phone if you depend on it; uninstalling Termux or losing the phone can remove the live copy. Preserve credentials privately too. Restoring stale state can replay deliveries or miss progress, so inspect backups rather than blindly resetting state.

| Problem | What to check |
| --- | --- |
| `uom` not found | Export `~/.local/bin` into PATH; verify installation completed |
| Missing configuration | Fill the four required fields in private `monitor.env` |
| Startup says migrate checkpoint | Complete new-account initialization or migrate the existing whole state |
| TLS failure | Phone time, network, CA certificates, and IMAPS 993; keep verification enabled |
| No Telegram messages | Correct bot/chat ID, bot `/start`, chat permissions, and recent logs |
| No old emails | First initialization deliberately baselines old INBOX messages |
| Another process holds the lock | Stop the other monitor; do not delete its lock file |
| Invalid checkpoint | Preserve the file and backups; inspect privately instead of resetting |
| Rejected update | Inspect the failed SHA's validation log; fix code or retry installation |
| Stops after screen-off/reboot | Boot opened once, permissions, battery limits, and actual device testing |
| Termux force-stopped | Reopen it and run `uom start` |

Working attachment limits are 45 MiB per file, 100 MiB accepted files per email, 25 candidate files, 10 MiB for photo sending, and three file-delivery attempts. Common photos, documents, Office files, text/data files, archives, and media are forwarded; executable/script, HTML/SVG, unknown, empty, oversized, or excess files are skipped with a warning. Referenced CID/inline images are supported; remote image URLs are not downloaded. Telegram may impose its own [API limits](https://core.telegram.org/bots/api).

Only INBOX is monitored. Long bodies become previews and complex HTML/CSS is simplified. Large raw emails are downloaded/decoded in memory. A failed chat or pending warning can hold later emails. Telegram does not provide a send idempotency key: a crash after acceptance but before acknowledgement reaches disk can cause a duplicate. Android can terminate the whole app, and an offline/powered-off phone cannot deliver mail. Verify your own device before depending on unattended operation.

Runtime logs omit passwords, bot-token URLs, email subjects, and attachment filenames. Telegram messages intentionally contain email content. Do not publish real messages, filled configuration, runtime state, or SSH keys in Git, screenshots, or bug reports.

## 19. Develop and contribute

Use a separate development checkout and virtual environment, not the phone's private runtime folder. From a development checkout with Python 3.14:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -t . -v
python -m ruff check src tests
bash -n deployment/termux/uom.sh
bash -n deployment/termux/boot.sh
```

On Windows, use `.venv\Scripts\python.exe` for Python commands instead of activating a POSIX virtual environment. Git for Windows provides Bash for the shell-manager fixtures. The real POSIX process/lock test is skipped on Windows and runs in Linux CI or Termux.

The tests use synthetic emails, mocked Telegram/IMAP calls, and local Git repositories. External application network access is blocked. CI uses Python 3.14 and no application secrets. It checks compilation, offline tests, dummy configuration, lint, package building, and Termux script syntax.

Submit focused fixes with tests. Preserve UID/pending-delivery state, certificate verification, and the existing feature set. Keep `.env.example` blank; include only dummy values in issues and pull requests.

## 20. License and author credit

The template's code and documentation are released under the [MIT License](https://github.com/RizniMohamed/uom-email-telegram-monitor-template/blob/main/LICENSE), copyright © 2026 **MN Rizni Mohamed**. MIT permits use, changes, redistribution, and commercial use subject to retaining its copyright and license notice. External dependencies and Termux apps have their own licenses.

Please keep the original author visible when sharing this guide, and link back to the repository if it helped you. README links and stars are voluntary; there is no star-before-use requirement added to the MIT license. See the [MIT explanation](https://choosealicense.com/licenses/mit/) and [Open Source Definition](https://opensource.org/osd).

**Author:** [MN Rizni Mohamed / RizniMohamed](https://github.com/RizniMohamed)

**Project:** [uom-email-telegram-monitor-template](https://github.com/RizniMohamed/uom-email-telegram-monitor-template)
