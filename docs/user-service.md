# Run the dashboard as a systemd user service

A user service keeps the dashboard running independently of a terminal. With
lingering enabled, the user service manager starts at boot and remains available
after logout. The application runs as the installing user, with access to that
user's repository registry and checkouts.

The examples use a dedicated release directory and the standard home-directory
paths. They contain no machine-specific registry, credentials, or account names.
Keep the actual configuration outside this repository.

## Files and behavior

| Purpose | Installed location |
| --- | --- |
| Service definition | `~/.config/systemd/user/guardrail-hub.service` |
| Optional GitHub authentication settings | `~/.config/systemd/user/guardrail-hub.service.d/github-https.conf` |
| Repository registry | `~/.config/guardrail-hub/config.toml` |
| Installed releases | `~/.local/share/guardrail-hub/releases/<full-commit-sha>/` |
| Active release symlink | `~/.local/share/guardrail-hub/current` |
| Previous release path, after an upgrade | `~/.local/share/guardrail-hub/PREVIOUS_RELEASE` |
| Managed repository clones | `~/.local/share/guardrail-hub/checkouts/` |

The service binds to `127.0.0.1:8600`, sends logs to the user journal, and restarts
five seconds after an unexpected failure. Five starts within a minute trigger
systemd's start limit. An intentional `systemctl stop` does not trigger recovery.

Each release is exported from an exact Git commit and installed non-editably into
its own Python 3.12 virtual environment using its committed `uv.lock`. The service
executes that environment directly: it does not fetch application code or resolve
dependencies at startup. Editing or switching the development checkout cannot
change the installed application. Repository-data refreshes are separate from
application upgrades.

## Prepare the registry

Prerequisites: Linux with a systemd user manager, Git, `uv`, Python 3.12 (which uv
can provision), and a checkout of this repository. The examples assume the
checkout is `~/src/guardrail-hub`; substitute your checkout path in the commands.
Node.js and development dependencies are not required to serve the installed UI.

If you do not already have a registry:

```bash
mkdir -p "$HOME/.config/guardrail-hub"
cp -n "$HOME/src/guardrail-hub/config.example.toml" "$HOME/.config/guardrail-hub/config.toml"
```

Edit the copied file to register your actual repositories. Remove placeholder
entries you do not need. Existing configuration, including a symlink managed by
a dotfiles repository, can be reused unchanged. `[checkouts] enabled = true`
keeps dashboard data on managed default-branch clones even when development
checkouts are on feature branches.

The unit passes an explicit config path, host, and port. To change the listening
address or port, edit its `ExecStart`, not just `[server]` in the registry. `%h`
is expanded by systemd to the user's home directory. For custom XDG locations,
adjust the unit, commands, and checkout directory together.

## Build and select a release

Run this Bash block for the initial installation and again whenever you explicitly
choose to upgrade. It deploys the fetched `origin/main` commit without switching
the development checkout. Inspect the selected revision before deploying if your
workflow requires review beyond merging. Failed builds leave the active release
unchanged; an already existing release directory is never overwritten.

```bash
(
  set -euo pipefail
  hub_source="$HOME/src/guardrail-hub"
  hub_base="$HOME/.local/share/guardrail-hub"
  git -C "$hub_source" fetch origin main
  hub_revision=$(git -C "$hub_source" rev-parse origin/main)
  hub_release="$hub_base/releases/$hub_revision"
  test ! -e "$hub_release"
  mkdir -p "$hub_release"
  git -C "$hub_source" archive "$hub_revision" | tar -x -C "$hub_release"
  uv sync --project "$hub_release" --locked --no-dev --no-editable --python 3.12
  printf '%s\n' "$hub_revision" > "$hub_release/REVISION"
  "$hub_release/.venv/bin/guardrail-hub" version
  if [ -L "$hub_base/current" ]; then
    readlink -f "$hub_base/current" > "$hub_base/PREVIOUS_RELEASE"
  else
    test ! -e "$hub_base/current"
  fi
  ln -s "$hub_release" "$hub_base/current.next"
  mv -Tf "$hub_base/current.next" "$hub_base/current"
)
```

The symlink selects the release for the next start; an already running process
continues using its original installation until restarted. If a build fails,
inspect the incomplete release before removing it and retrying. If `current.next`
exists from an interrupted activation, inspect it before retrying as well. Keep
previous releases until the replacement is verified; never delete the running
release or its environment.

## Install the unit and enable boot startup

The example [unit](../deploy/systemd/guardrail-hub.service) uses no credentials.
Copy it on first installation; inspect differences before replacing a customized
unit during an upgrade.

```bash
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$HOME/src/guardrail-hub/deploy/systemd/guardrail-hub.service" \
  "$HOME/.config/systemd/user/guardrail-hub.service"
systemd-analyze --user verify "$HOME/.config/systemd/user/guardrail-hub.service"
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now guardrail-hub.service
```

Local policy may require an administrator to authorize `loginctl enable-linger`.
Lingering affects the entire user service manager, not just this application.
The service's `WantedBy=default.target` makes it start with that manager; a
desktop autostart entry is unnecessary. Enabling the service creates its symlink
under `~/.config/systemd/user/default.target.wants/`.

For an upgrade after selecting a new release, restart explicitly:

```bash
systemctl --user restart guardrail-hub.service
```

## Optional GitHub authentication without a session SSH agent

At boot, a user service may have no usable SSH agent. For GitHub repositories,
the optional [drop-in](../deploy/systemd/github-https.conf) rewrites
`git@github.com:…` URLs to HTTPS inside this service and uses the GitHub CLI's
existing credential helper. It does not alter global Git settings or repository
remotes. Check `command -v gh` and adjust the helper path if needed. Authenticate
with `gh auth login` if you have not already done so.

```bash
mkdir -p "$HOME/.config/systemd/user/guardrail-hub.service.d"
install -m 644 "$HOME/src/guardrail-hub/deploy/systemd/github-https.conf" \
  "$HOME/.config/systemd/user/guardrail-hub.service.d/github-https.conf"
systemctl --user daemon-reload
systemctl --user restart guardrail-hub.service
```

No token belongs in the unit, registry, or repository. If GitHub CLI credentials
are stored in a desktop keyring, private-repository fetches may fail until the
keyring is unlocked after login. Existing clones remain readable; use the
dashboard's Refresh control after unlocking. Fetching private repositories before
any login requires separately provisioned credentials suitable for unattended
use; this example does not create them.

Network availability can also delay refresh at boot. Refresh occurs at application
startup and through the UI; no periodic fetch or application-upgrade timer is
installed. A fetch failure appears on the repository card rather than preventing
the UI from starting. A repository without an existing clone has no cached data
to display until its first successful fetch.

## Verify and operate

```bash
systemctl --user status guardrail-hub.service
systemctl --user is-enabled guardrail-hub.service
loginctl show-user "$USER" -p Linger
curl --fail --retry 10 --retry-connrefused --retry-delay 1 --max-time 30 \
  http://127.0.0.1:8600/health
journalctl --user -u guardrail-hub.service -n 100 --no-pager
cat "$HOME/.local/share/guardrail-hub/current/REVISION"
```

Look for an active service, `enabled`, `Linger=yes`, and successful HTTP responses.
Inspect each repository's `error` and `fetch_error` in `/health`: an HTTP 200
alone does not establish that every repository refreshed. Open
`http://127.0.0.1:8600/` to check the UI. Use `journalctl --user -u
guardrail-hub.service -f` to follow logs and `systemctl --user cat
guardrail-hub.service` to see the effective unit and drop-ins.

To test recovery during a suitable interruption window, terminate only this
service's main process and verify it returns after five seconds:

```bash
systemctl --user kill --kill-whom=main --signal=SIGKILL guardrail-hub.service
# Wait at least five seconds, then check recovery and /health.
systemctl --user show guardrail-hub.service -p ActiveState -p MainPID -p NRestarts
```

An increasing `NRestarts` plus restored HTTP responses verifies recovery. Verify
boot startup and operation after logout at your next planned reboot/logout;
checking the configuration alone does not exercise those transitions.

After editing a unit or drop-in, run `systemctl --user daemon-reload` and restart.
Registry edits need only a restart. These local files are not automatically
updated when the source repository changes.

## Rollback and disable

After an upgrade, restore the saved previous release with:

```bash
(
  set -euo pipefail
  hub_base="$HOME/.local/share/guardrail-hub"
  hub_previous=$(cat "$hub_base/PREVIOUS_RELEASE")
  test -x "$hub_previous/.venv/bin/guardrail-hub"
  ln -s "$hub_previous" "$hub_base/current.rollback"
  mv -Tf "$hub_base/current.rollback" "$hub_base/current"
  systemctl --user reset-failed guardrail-hub.service
  systemctl --user restart guardrail-hub.service
)
```

Check `/health` and the journal after rolling back. A first installation has no
previous release to restore. To stop temporarily, use `systemctl --user stop
guardrail-hub.service`; to disable autostart as well:

```bash
systemctl --user disable --now guardrail-hub.service
```

This does not remove configuration, releases, or managed clones. It also leaves
lingering enabled for other user services. Disable lingering only if it is no
longer needed by any of them.

References: [systemd service settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[loginctl lingering](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html),
and [uv non-editable installations](https://docs.astral.sh/uv/concepts/projects/sync/).
