#!/bin/bash
# Installs MetaHuman for Blender into the Blender versions on this Mac, using each Blender's own
# extension installer:  Blender --command extension install-file -r user_default --enable <zip>
# With --uninstall it removes the extension again (the Uninstall script calls it that way).
# Written for the bash 3.2 that ships with macOS.

set -u
cd "$(dirname "$0")" || exit 1

EXT_ID="mfb"
NAME="MetaHuman for Blender"
MODE="install"
[ "${1:-}" = "--uninstall" ] && MODE="uninstall"

finish() {
    echo
    read -r -p "Press Return to close this window. " _
    exit "${1:-0}"
}

echo "$NAME: $MODE"
echo

ZIP=""
if [ "$MODE" = "install" ]; then
    ZIP=$(ls -1 "$EXT_ID"-*.zip 2>/dev/null | tail -n 1)
    if [ -z "$ZIP" ]; then
        echo "Can't find $EXT_ID-<version>.zip next to this script. Keep the files of the download together."
        finish 1
    fi
    ZIP="$PWD/$ZIP"
fi

# ---- find Blender ---------------------------------------------------------

APPS=()
add_app() {
    local app="${1%/}" a
    [ -x "$app/Contents/MacOS/Blender" ] || return 0
    for a in "${APPS[@]+"${APPS[@]}"}"; do
        [ "$a" = "$app" ] && return 0
    done
    APPS+=("$app")
}

for app in /Applications/Blender*.app /Applications/Blender*/Blender*.app \
           "$HOME"/Applications/Blender*.app \
           "$HOME/Library/Application Support/Steam/steamapps/common/Blender/Blender.app"; do
    add_app "$app"
done
while IFS= read -r app; do
    add_app "$app"
done < <(mdfind "kMDItemCFBundleIdentifier == 'org.blenderfoundation.blender'" 2>/dev/null)

app_version() {
    /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$1/Contents/Info.plist" 2>/dev/null \
        || echo "unknown"
}

# 4.2 or newer: extensions came with Blender 4.2
new_enough() {
    local major minor
    major=$(echo "$1" | cut -d. -f1)
    minor=$(echo "$1" | cut -d. -f2)
    case "$major$minor" in *[!0-9]*|"") return 1 ;; esac
    [ "$major" -gt 4 ] || { [ "$major" -eq 4 ] && [ "$minor" -ge 2 ]; }
}

if [ ${#APPS[@]} -eq 0 ]; then
    echo "No Blender was found in Applications."
    read -r -p "Drag Blender.app into this window and press Return (or just Return to stop): " app
    app="${app%"${app##*[![:space:]]}"}"   # trailing spaces
    app="${app//\\ / }"                    # Terminal escapes spaces when you drag a file in
    app="${app#\'}"; app="${app%\'}"
    [ -n "$app" ] || finish 1
    add_app "$app"
    if [ ${#APPS[@]} -eq 0 ]; then
        echo "That is not a Blender app: $app"
        finish 1
    fi
fi

echo "Blender versions found:"
i=1
for app in "${APPS[@]}"; do
    printf "  %d) Blender %s   (%s)\n" "$i" "$(app_version "$app")" "$app"
    i=$((i + 1))
done
echo
read -r -p "Which ones? Type numbers separated by spaces, or press Return for all: " choice

CHOSEN=()
if [ -z "${choice// /}" ]; then
    CHOSEN=("${APPS[@]}")
else
    for n in $choice; do
        case "$n" in *[!0-9]*) echo "Skipping '$n': not a number."; continue ;; esac
        if [ "$n" -ge 1 ] && [ "$n" -le ${#APPS[@]} ]; then
            CHOSEN+=("${APPS[$((n - 1))]}")
        else
            echo "Skipping $n: not in the list."
        fi
    done
fi
[ ${#CHOSEN[@]} -gt 0 ] || { echo "Nothing chosen."; finish 1; }

# ---- Blender must be closed -----------------------------------------------
# A running Blender would overwrite the change to its preferences when it quits.

while pgrep -x Blender >/dev/null 2>&1; do
    read -r -p "Blender is running. Quit Blender, then press Return (or type q to stop): " answer
    [ "$answer" = "q" ] && finish 1
done

# ---- install or remove ----------------------------------------------------

LOG="$HOME/Library/Logs/$NAME installer.log"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
failed=0
for app in "${CHOSEN[@]}"; do
    version=$(app_version "$app")
    echo
    echo "Blender $version ($app)"
    if ! new_enough "$version"; then
        echo "  Skipped: $NAME needs Blender 4.2 or newer."
        failed=1
        continue
    fi
    if [ "$MODE" = "install" ]; then
        "$app/Contents/MacOS/Blender" --command extension install-file -r user_default --enable "$ZIP" >>"$LOG" 2>&1
    else
        "$app/Contents/MacOS/Blender" --command extension remove "$EXT_ID" >>"$LOG" 2>&1
    fi
    if [ $? -eq 0 ]; then
        echo "  Done."
    else
        echo "  Failed. Details are in: $LOG"
        failed=1
    fi
done

echo
if [ "$MODE" = "install" ]; then
    echo "Open Blender, press N in the 3D Viewport and choose the MetaHuman tab,"
    echo "or use the MetaHuman menu in the top bar."
fi
finish $failed
