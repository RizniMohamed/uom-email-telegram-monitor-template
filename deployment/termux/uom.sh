#!/data/data/com.termux/files/usr/bin/bash
# A personal monitor supervisor. Secrets/checkpoints never enter a release.
set -u -o pipefail

BASE="${UOM_TERMUX_HOME:-$HOME/.local/share/uom-monitor}"
BIN="$HOME/.local/bin/uom"
SELF="$(realpath "${BASH_SOURCE[0]}")"

init() {
    BASE="$(realpath -m "$BASE")"
    local private_home
    private_home="$(realpath "$HOME")"
    [[ "$BASE" == "$private_home/"* ]] || { echo 'Use a directory inside Termux private HOME'; return 1; }
    umask 077
    mkdir -p "$BASE/releases" || return 1
    chmod 700 "$BASE"
}

log() { printf '%s %s\n' "$(date -Iseconds)" "$*" >> "$BASE/manager.log"; }
fail() { printf '%s\n' "$*" > "$BASE/last-error"; log "$*"; }

atomic_text() {
    printf '%s\n' "$2" > "$1.next" && mv -f "$1.next" "$1" && sync -f "$BASE"
}

current_sha() {
    local target sha
    target="$(readlink -f "$BASE/current")" || return 1
    sha="${target##*/}"
    [[ "$sha" =~ ^[0-9a-f]{40}$ && "$target" == "$BASE/releases/$sha" ]] || return 1
    printf '%s\n' "$sha"
}

switch_release() {
    [[ "$1" =~ ^[0-9a-f]{40}$ && -f "$BASE/releases/$1/.validated" ]] || return 1
    rm -f "$BASE/current.next"
    ln -s "$BASE/releases/$1" "$BASE/current.next" &&
        mv -Tf "$BASE/current.next" "$BASE/current" && sync -f "$BASE"
}

pid_alive() {
    local pid born actual state
    [[ -f "$1" ]] && read -r pid born < "$1" || return 1
    [[ "$pid" =~ ^[0-9]+$ && "$born" =~ ^[0-9]+$ && -r "/proc/$pid/stat" ]] || return 1
    read -r state actual < <(awk '{print $3, $22}' "/proc/$pid/stat" 2>/dev/null)
    [[ "$state" != Z && "$actual" == "$born" ]] && kill -0 "$pid" 2>/dev/null
}

record_pid() {
    local born
    born="$(awk '{print $22}' "/proc/$2/stat" 2>/dev/null)"
    [[ "$born" =~ ^[0-9]+$ ]] || return 1
    printf '%s %s\n' "$2" "$born" > "$1"
}

terminate() {
    local file="$1" pid born group original i
    [[ -f "$file" ]] || return 0
    original="$(cat "$file")"
    if pid_alive "$file"; then
        read -r pid born < "$file"
        group="$(awk '{print $5}' "/proc/$pid/stat" 2>/dev/null)"
        if [[ "$group" == "$pid" ]]; then kill -TERM -- "-$pid" 2>/dev/null || true
        else kill -TERM "$pid" 2>/dev/null || true; fi
        for ((i=0; i<24; i++)); do
            pid_alive "$file" || break
            sleep 5
        done
        if pid_alive "$file"; then
            log 'Process did not exit within 120 seconds; forcing shutdown'
            if [[ "$group" == "$pid" ]]; then kill -KILL -- "-$pid" 2>/dev/null || true
            else kill -KILL "$pid" 2>/dev/null || true; fi
        fi
        wait "$pid" 2>/dev/null || true
    fi
    [[ "$(cat "$file" 2>/dev/null)" != "$original" ]] || rm -f "$file"
}

prepare_release() {
    local sha="$1" candidate="$BASE/releases/$1" repo
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || return 2
    [[ -f "$candidate/.validated" ]] && return 0
    read -r repo < "$BASE/repo.path" || return 1
    # Only incomplete generated releases are removed; BASE state is never touched.
    [[ ! -L "$candidate" && "$(realpath -m "$candidate")" == "$BASE/releases/$sha" ]] || return 1
    rm -rf -- "$candidate"
    mkdir "$candidate" || return 1
    git -C "$repo" archive "$sha" | tar -x -C "$candidate" || return 1
    [[ ! -e "$candidate/.env" && ! -e "$candidate/settings.py" &&
       ! -e "$candidate/uom_monitor_state.json" ]] || return 2
    log "Preparing and testing release $sha; current monitor keeps running"
    python -m venv "$candidate/.venv" > "$candidate/validation.log" 2>&1 || return 1
    local py="$candidate/.venv/bin/python"
    timeout 180 "$py" -m pip install --disable-pip-version-check "$candidate" >> "$candidate/validation.log" 2>&1 || return 1
    timeout 60 "$py" -m compileall -q "$candidate/src" >> "$candidate/validation.log" 2>&1 || return 2
    (
        cd "$candidate" || exit 1
        # Never pass real application configuration to candidate tests.
        export UOM_ENV_FILE=/dev/null UOM_STATE_FILE="$candidate/offline-state.json"
        export UOM_USERNAME=ci-user UOM_PASSWORD=ci-dummy-password
        export TELEGRAM_BOT_TOKEN=123:ci_dummy_token TELEGRAM_CHAT_ID=123
        timeout 120 "$py" -m unittest discover -s tests -t . -v &&
            timeout 30 "$py" -m uom_email_monitor --check-config
    ) >> "$candidate/validation.log" 2>&1 || return 2
    touch "$candidate/.validated" && sync -f "$BASE"
}

check_config() {
    [[ -f "$BASE/monitor.env" && -f "$BASE/uom_monitor_state.json" ]] || return 1
    UOM_ENV_FILE="$BASE/monitor.env" UOM_STATE_FILE="$BASE/uom_monitor_state.json" \
        timeout 30 "$BASE/releases/$1/.venv/bin/python" -m uom_email_monitor --check-config > /dev/null 2>&1
}

start_monitor() {
    local sha pid
    sha="$(current_sha)" && check_config "$sha" || return 1
    terminate "$BASE/monitor.pid"
    touch "$BASE/monitor.log"
    HEALTH_OFFSET="$(stat -c %s "$BASE/monitor.log")"
    UOM_ENV_FILE="$BASE/monitor.env" UOM_STATE_FILE="$BASE/uom_monitor_state.json" \
        PYTHONUNBUFFERED=1 setsid "$BASE/releases/$sha/.venv/bin/python" -m uom_email_monitor \
        >> "$BASE/monitor.log" 2>&1 9>&- &
    pid=$!
    record_pid "$BASE/monitor.pid" "$pid" || { kill -TERM "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; return 1; }
    log "Monitor started on $sha"
}

healthy() {
    # Logs after this launch only; previous successful runs cannot satisfy health.
    local n
    for ((n=0; n<18; n++)); do
        pid_alive "$BASE/monitor.pid" || return 1
        if tail -c "+$((HEALTH_OFFSET + 1))" "$BASE/monitor.log" | grep 'Monitoring inbox' > /dev/null; then return 0; fi
        sleep 5
    done
    return 1
}

recover_interrupted() {
    local old
    [[ -f "$BASE/activation-pending" ]] || return 0
    read -r old < "$BASE/activation-pending" || return 1
    switch_release "$old" || return 1
    rm -f "$BASE/activation-pending"
    sync -f "$BASE"
    log 'Interrupted activation recovered; previous code restored, state unchanged'
}

activate() {
    local sha="$1" old
    old="$(current_sha)" || return 1
    [[ "$old" != "$sha" ]] || return 0
    check_config "$sha" || { fail 'Candidate cannot read the existing configuration/checkpoint'; return 1; }
    # Durable recovery marker BEFORE stopping/switching. Never roll back mail state.
    atomic_text "$BASE/activation-pending" "$old" || return 1
    terminate "$BASE/monitor.pid"
    if switch_release "$sha" && start_monitor && healthy; then
        atomic_text "$BASE/previous" "$old" || return 1
        rm -f "$BASE/activation-pending" "$BASE/last-error" "$BASE/failed-commit"
        sync -f "$BASE"
        log "Update healthy: $sha"
        return 0
    fi
    terminate "$BASE/monitor.pid"
    recover_interrupted || return 1
    start_monitor || true
    fail "Activation failed for $sha; previous code restored; delivery state retained"
    return 1
}

check_updates() {
    local repo sha old rejected result
    read -r repo < "$BASE/repo.path" || return 1
    if ! timeout 60 git -C "$repo" fetch --quiet origin main > /dev/null 2>&1; then
        log 'GitHub fetch failed; keeping current version and retrying in five minutes'
        return 1
    fi
    sha="$(git -C "$repo" rev-parse FETCH_HEAD)" || return 1
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || return 1
    old="$(current_sha)" || return 1
    [[ "$sha" != "$old" ]] || return 0
    rejected="$(cat "$BASE/failed-commit" 2>/dev/null || true)"
    [[ "$sha" != "$rejected" ]] || return 0
    if prepare_release "$sha"; then :
    else
        result=$?
        # Bad code is held until a new commit/manual retry; network/install errors retry.
        [[ "$result" != 2 ]] || atomic_text "$BASE/failed-commit" "$sha"
        fail "Update rejected for $sha: installation/tests failed; current version retained"
        return 1
    fi
    if ! pid_alive "$BASE/manager.pid"; then
        log "Validated $sha; activation deferred until monitoring is started"
        return 0
    fi
    activate "$sha"
}

rotate_logs() {
    local file
    for file in "$BASE/manager.log" "$BASE/monitor.log"; do
        [[ -f "$file" ]] || continue
        if (( $(stat -c %s "$file") >= 2097152 )); then
            [[ ! -f "$file.2" ]] || mv -f "$file.2" "$file.3"
            [[ ! -f "$file.1" ]] || mv -f "$file.1" "$file.2"
            # Preserve the writer's inode. A few log lines may race this copy.
            cp "$file" "$file.1" && : > "$file"
        fi
    done
}

cleanup() {
    trap - EXIT TERM INT HUP
    terminate "$BASE/monitor.pid"
    recover_interrupted || true
    rm -f "$BASE/manager.pid"
    if [[ "${WAKE_HELD:-0}" == 1 ]]; then termux-wake-unlock > /dev/null 2>&1 || true; fi
    log 'Manager stopped'
}

run_manager() {
    exec 9>"$BASE/manager.lock"
    flock -n 9 || return 0
    record_pid "$BASE/manager.pid" "$$" || return 1
    trap cleanup EXIT
    trap 'exit 0' TERM INT HUP
    terminate "$BASE/monitor.pid"  # Reap an orphan after a killed manager.
    recover_interrupted || { fail 'Cannot recover pending activation; inspect release pointers'; return 1; }
    WAKE_HELD=0
    if [[ -f "$BASE/wake-lock" ]] && command -v termux-wake-lock > /dev/null; then
        termux-wake-lock > /dev/null 2>&1 && WAKE_HELD=1
    fi
    local next_check=$SECONDS
    while [[ -f "$BASE/enabled" ]]; do
        if ! pid_alive "$BASE/monitor.pid"; then
            start_monitor || log 'Monitor startup failed; retrying shortly without resetting state'
        fi
        if (( SECONDS >= next_check )) || [[ -f "$BASE/check-now" ]]; then
            rm -f "$BASE/check-now"
            next_check=$((SECONDS + 300))
            check_updates || true
        fi
        rotate_logs
        sleep 5
    done
}

main() {
    init || return 1
    case "${1:-status}" in
        install)
            local repo sha
            repo="$(git -C "${2:-.}" rev-parse --show-toplevel)" || return 1
            repo="$(realpath "$repo")"
            [[ "$BASE/" != "$repo/"* ]] || { echo 'Keep runtime data outside the checkout'; return 1; }
            pid_alive "$BASE/manager.pid" && { echo 'Stop the manager before reinstalling it'; return 1; }
            atomic_text "$BASE/repo.path" "$repo" || return 1
            sha="$(git -C "$repo" rev-parse HEAD)"
            prepare_release "$sha" || { echo 'Initial offline validation failed; see release validation.log'; return 1; }
            if ! current_sha > /dev/null; then switch_release "$sha" || return 1; fi
            mkdir -p "$HOME/.local/bin"
            cp "$SELF" "$BIN.next" && chmod 700 "$BIN.next" && mv -f "$BIN.next" "$BIN" || return 1
            echo 'Installed. Configure private monitor.env and migrate state BEFORE uom start.'
            ;;
        start)
            pid_alive "$BASE/manager.pid" && { echo 'Manager already running'; return 0; }
            local sha i
            if ! sha="$(current_sha)" || ! check_config "$sha"; then
                echo 'Install first, configure monitor.env, and migrate the existing checkpoint'
                return 1
            fi
            touch "$BASE/enabled"
            nohup setsid bash "$BIN" run < /dev/null > /dev/null 2>&1 9>&- &
            for ((i=0; i<20; i++)); do
                pid_alive "$BASE/manager.pid" && { echo 'Manager started; use uom status and uom logs'; return 0; }
                sleep 0.5
            done
            echo 'Manager did not start; use uom logs'; return 1
            ;;
        stop)
            rm -f "$BASE/enabled"
            terminate "$BASE/manager.pid"
            terminate "$BASE/monitor.pid"
            echo 'Stopped; checkpoint retained. Boot startup stays paused until uom start.'
            ;;
        restart) main stop && main start ;;
        status)
            if pid_alive "$BASE/manager.pid"; then echo 'Manager running'; else echo 'Manager stopped'; fi
            if pid_alive "$BASE/monitor.pid"; then echo 'Monitor running'; else echo 'Monitor stopped'; fi
            echo "Release: $(current_sha || echo not-installed)"
            [[ ! -f "$BASE/last-error" ]] || cat "$BASE/last-error"
            ;;
        logs) tail -n 80 "$BASE/manager.log" "$BASE/monitor.log" 2>/dev/null ;;
        update)
            rm -f "$BASE/failed-commit"
            if pid_alive "$BASE/manager.pid"; then touch "$BASE/check-now"; echo 'Update check requested; use uom logs'
            else
                (exec 9>"$BASE/manager.lock"; flock -n 9 && check_updates)
            fi
            ;;
        run) run_manager ;;
        *) echo 'Usage: uom {install [checkout]|start|stop|restart|status|logs|update}'; return 1 ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
