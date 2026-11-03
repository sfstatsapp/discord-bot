#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REMOTE_HOST=${BUB_DEPLOY_HOST:-100.110.34.55}
REMOTE_USER=${BUB_DEPLOY_USER:-alwaly}
REMOTE=${REMOTE_USER}@${REMOTE_HOST}
SSH_KEY=${BUB_DEPLOY_SSH_KEY:-$HOME/.ssh/id_ed25519_bub_fedora}
LIVE_DIR=${BUB_DEPLOY_DIR:-/var/lib/bub}
STAGE_DIR=${BUB_DEPLOY_STAGE_DIR:-$LIVE_DIR/.deploy-stage}
EXCLUDES=$ROOT/.deployignore
DRY_RUN=0
INCLUDE_WORKBOOKS=0
# One workbook name per line, from the --include-*-workbook options.
SELECTED_WORKBOOKS=""
INCLUDE_SF6_GIFS=0
NO_RESTART=0

usage() {
    cat <<'EOF'
Usage: scripts/deploy_live.sh [--dry-run] [--include-workbooks] [--include-usf4-workbook] [--include-avtl-workbook] [--include-ggst-workbook] [--include-2xko-workbook] [--include-cotw-workbook] [--include-sf6-gifs] [--no-restart]

Stages and verifies the current Bub tree, synchronizes deployable changes to
the live Fedora service, and restarts Bub only when live files changed.

  --dry-run            Show local-to-stage changes without writing or restarting
  --include-workbooks  Include ODS files; excluded by default to protect cron data
  --include-usf4-workbook  Include only USFIV Frame Data.ods
  --include-avtl-workbook  Include only AVTL Frame Data.ods
  --include-ggst-workbook  Include only GGST Frame Data.ods
  --include-2xko-workbook  Include only 2XKO Frame Data.ods
  --include-cotw-workbook  Include only COTW Frame Data.ods
                       The --include-*-workbook options combine; other workbooks stay untouched
  --include-sf6-gifs   Add local SF6 GIFs without deleting legacy media
  --no-restart         Apply files but leave the current process running
EOF
}

add_workbook() {
    SELECTED_WORKBOOKS="$SELECTED_WORKBOOKS$1
"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --include-workbooks) INCLUDE_WORKBOOKS=1 ;;
        --include-usf4-workbook) add_workbook "USFIV Frame Data.ods" ;;
        --include-avtl-workbook) add_workbook "AVTL Frame Data.ods" ;;
        --include-ggst-workbook) add_workbook "GGST Frame Data.ods" ;;
        --include-2xko-workbook) add_workbook "2XKO Frame Data.ods" ;;
        --include-cotw-workbook) add_workbook "COTW Frame Data.ods" ;;
        --include-sf6-gifs) INCLUDE_SF6_GIFS=1 ;;
        --no-restart) NO_RESTART=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ ! -f "$EXCLUDES" ]; then
    printf 'Missing deploy exclude file: %s\n' "$EXCLUDES" >&2
    exit 1
fi
if [ ! -r "$SSH_KEY" ]; then
    printf 'Missing SSH key: %s\n' "$SSH_KEY" >&2
    exit 1
fi

if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
    host_exec() { flatpak-spawn --host "$@"; }
else
    host_exec() { "$@"; }
fi

remote() {
    host_exec ssh \
        -o BatchMode=yes \
        -o ConnectTimeout=15 \
        -o IdentitiesOnly=yes \
        -i "$SSH_KEY" \
        "$REMOTE" "$@"
}

run_rsync() {
    host_exec rsync "$@"
}

printf 'Preflight %s:%s\n' "$REMOTE" "$LIVE_DIR"
remote "test \"\$(hostname)\" = fedora && test -d '$LIVE_DIR' && test -x '$LIVE_DIR/.venv/bin/python' && test \"\$(systemctl is-active bub.service)\" = active"
if remote "pgrep -f '[s]ync_sf6_google_sheet.py' >/dev/null"; then
    printf 'SF6 workbook sync is currently running; deployment aborted.\n' >&2
    exit 1
fi
remote "mkdir -p '$STAGE_DIR'"

set -- \
    -aH \
    --omit-dir-times \
    --delete-delay \
    --itemize-changes \
    --out-format=%i\ %n%L \
    --exclude-from="$EXCLUDES"
if [ "$INCLUDE_WORKBOOKS" -ne 1 ]; then
    while IFS= read -r workbook; do
        if [ -n "$workbook" ]; then
            set -- "$@" "--include=$workbook"
        fi
    done <<EOF
$SELECTED_WORKBOOKS
EOF
    set -- "$@" "--exclude=*.ods"
fi
if [ "$DRY_RUN" -eq 1 ]; then
    set -- "$@" --dry-run
fi

printf 'Comparing local files with deployment stage...\n'
stage_changes=$(run_rsync "$@" -e "ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $SSH_KEY" "$ROOT/" "$REMOTE:$STAGE_DIR/")
if [ -n "$stage_changes" ]; then
    printf '%s\n' "$stage_changes"
else
    printf 'Deployment stage already matches local deployable files.\n'
fi
media_stage_changes=""
if [ "$INCLUDE_SF6_GIFS" -eq 1 ]; then
    printf 'Comparing local SF6 GIFs with deployment stage...\n'
    remote "mkdir -p '$STAGE_DIR/sf6frames/files'"
    set -- \
        -aH \
        --omit-dir-times \
        --itemize-changes \
        --out-format=%i\ %n%L \
        --include=*/ \
        --include=*.gif \
        --exclude=*
    if [ "$DRY_RUN" -eq 1 ]; then
        set -- "$@" --dry-run
    fi
    media_stage_changes=$(run_rsync "$@" -e "ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $SSH_KEY" "$ROOT/sf6frames/files/" "$REMOTE:$STAGE_DIR/sf6frames/files/")
    if [ -n "$media_stage_changes" ]; then
        printf '%s\n' "$media_stage_changes"
    else
        printf 'Deployment stage already has all local SF6 GIFs.\n'
    fi
fi
if [ "$DRY_RUN" -eq 1 ]; then
    exit 0
fi

printf 'Verifying staged Python sources and application wiring...\n'
remote "'$LIVE_DIR/.venv/bin/python' -m compileall -q '$STAGE_DIR' && cd '$LIVE_DIR' && PYTHONPATH='$STAGE_DIR' '$LIVE_DIR/.venv/bin/python' -c 'import sys; sys.path.insert(0, \"$STAGE_DIR\"); import bot; assert bot.client and bot.tree and callable(bot.main)'"
if [ "$INCLUDE_SF6_GIFS" -eq 1 ]; then
    printf 'Verifying staged SF6 GIFs...\n'
    remote "'$LIVE_DIR/.venv/bin/python' -c 'from pathlib import Path; files=list(Path(\"$STAGE_DIR/sf6frames/files\").glob(\"*/*.gif\")); assert files; assert all(path.read_bytes()[:6] in {b\"GIF87a\", b\"GIF89a\"} for path in files)'"
fi

workbook_filters=""
if [ "$INCLUDE_WORKBOOKS" -ne 1 ]; then
    while IFS= read -r workbook; do
        if [ -n "$workbook" ]; then
            workbook_filters="$workbook_filters --include='$workbook'"
        fi
    done <<EOF
$SELECTED_WORKBOOKS
EOF
    workbook_filters="$workbook_filters --exclude='*.ods'"
fi
live_changes=$(remote "rsync -aHn --omit-dir-times --delete-delay --itemize-changes --out-format='%i %n%L' --exclude-from='$STAGE_DIR/.deployignore' $workbook_filters '$STAGE_DIR/' '$LIVE_DIR/'")
media_live_changes=""
if [ "$INCLUDE_SF6_GIFS" -eq 1 ]; then
    media_live_changes=$(remote "rsync -aHn --omit-dir-times --itemize-changes --out-format='%i %n%L' --include='*/' --include='*.gif' --exclude='*' '$STAGE_DIR/sf6frames/files/' '$LIVE_DIR/sf6frames/files/'")
fi
if [ -z "$live_changes" ] && [ -z "$media_live_changes" ]; then
    printf 'Live deployment already matches the verified stage; restart skipped.\n'
    exit 0
fi
if [ -n "$live_changes" ]; then
    printf '%s\n' "$live_changes"
fi
if [ -n "$media_live_changes" ]; then
    printf '%s\n' "$media_live_changes"
fi

printf 'Applying verified files...\n'
remote "if ! cmp -s '$STAGE_DIR/requirements.txt' '$LIVE_DIR/requirements.txt'; then '$LIVE_DIR/.venv/bin/python' -m pip install -r '$STAGE_DIR/requirements.txt'; fi"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$LIVE_DIR/.deploy-backups/$timestamp
remote "mkdir -p '$backup_dir' && rsync -aH --omit-dir-times --delete-delay --backup --backup-dir='$backup_dir' --exclude-from='$STAGE_DIR/.deployignore' $workbook_filters '$STAGE_DIR/' '$LIVE_DIR/'"
if [ "$INCLUDE_SF6_GIFS" -eq 1 ]; then
    remote "rsync -aH --omit-dir-times --backup --backup-dir='$backup_dir' --include='*/' --include='*.gif' --exclude='*' '$STAGE_DIR/sf6frames/files/' '$LIVE_DIR/sf6frames/files/'"
fi

if [ "$NO_RESTART" -eq 1 ]; then
    printf 'Files deployed without restart. Backup: %s:%s\n' "$REMOTE" "$backup_dir"
    exit 0
fi

printf 'Restarting Bub through systemd Restart=always...\n'
remote 'old_pid=$(systemctl show -p MainPID --value bub.service); test "$old_pid" -gt 0; kill -TERM "$old_pid"; attempts=0; while [ "$attempts" -lt 45 ]; do sleep 1; new_pid=$(systemctl show -p MainPID --value bub.service); state=$(systemctl is-active bub.service || true); if [ "$state" = active ] && [ "$new_pid" -gt 0 ] && [ "$new_pid" != "$old_pid" ]; then printf "Bub restarted: old_pid=%s new_pid=%s\n" "$old_pid" "$new_pid"; exit 0; fi; attempts=$((attempts + 1)); done; systemctl status bub.service --no-pager; exit 1'
printf 'Deployment complete. Backup: %s:%s\n' "$REMOTE" "$backup_dir"
