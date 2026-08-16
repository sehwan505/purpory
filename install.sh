#!/bin/sh
set -eu

component=all
mode=release
uninstall=false
repository=${PURPORY_REPOSITORY:-sehwan505/purpory}
version=${PURPORY_VERSION:-latest}
release_base=${PURPORY_RELEASE_BASE_URL:-}
bin_dir=${PURPORY_BIN_DIR:-$HOME/.local/bin}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || pwd)

die() {
  printf 'purpory install: %s\n' "$*" >&2
  exit 1
}

for argument in "$@"; do
  case "$argument" in
    cli|app|all) component=$argument ;;
    --local) mode=local ;;
    --uninstall) uninstall=true ;;
    -h|--help)
      printf '%s\n' 'usage: install.sh [cli|app|all] [--local|--uninstall]'
      exit 0
      ;;
    *) die "unknown argument: $argument" ;;
  esac
done

case $(uname -s) in
  Darwin) platform=darwin ;;
  Linux) platform=linux ;;
  *) die 'use install.ps1 on Windows; desktop installation supports macOS and Linux' ;;
esac

case $(uname -m) in
  arm64|aarch64) arch=arm64 ;;
  x86_64|amd64) arch=amd64 ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac

if [ "$platform" = darwin ]; then
  app_dir=${PURPORY_APP_DIR:-$HOME/Applications}
  app_target=$app_dir/Purpory.app
else
  data_home=${XDG_DATA_HOME:-$HOME/.local/share}
  app_dir=${PURPORY_APP_DIR:-$data_home/purpory}
  app_target=$app_dir/purpory-desktop
  desktop_target=$data_home/applications/purpory.desktop
  icon_target=$data_home/icons/hicolor/512x512/apps/purpory.png
fi

remove_cli() {
  rm -f -- "$bin_dir/purpory"
  printf 'Removed CLI: %s\n' "$bin_dir/purpory"
}

remove_app() {
  if [ "$platform" = darwin ]; then
    [ "$app_target" != / ] || die 'refusing to remove root directory'
    rm -rf -- "$app_target"
  else
    [ "$app_dir" != / ] || die 'refusing to remove root directory'
    rm -rf -- "$app_dir"
    rm -f -- "$desktop_target" "$icon_target"
  fi
  printf 'Removed app: %s\n' "$app_target"
}

if [ "$uninstall" = true ]; then
  case "$component" in
    cli) remove_cli ;;
    app) remove_app ;;
    all) remove_cli; remove_app ;;
  esac
  printf '%s\n' 'Kept project data in ~/.purpory.'
  exit 0
fi

temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/purpory-install.XXXXXX")
cleanup() { rm -rf -- "$temp_dir"; }
trap cleanup EXIT HUP INT TERM

release_url() {
  if [ -n "$release_base" ]; then
    printf '%s/%s' "${release_base%/}" "$1"
    return
  fi
  if [ "$version" = latest ]; then
    printf 'https://github.com/%s/releases/latest/download/%s' "$repository" "$1"
  else
    printf 'https://github.com/%s/releases/download/%s/%s' "$repository" "$version" "$1"
  fi
}

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    die 'shasum or sha256sum is required'
  fi
}

download() {
  asset=$1
  destination=$2
  command -v curl >/dev/null 2>&1 || die 'curl is required'
  curl -fL --retry 3 --progress-bar "$(release_url "$asset")" -o "$destination"
  checksums=$temp_dir/checksums.txt
  if [ ! -f "$checksums" ]; then
    curl -fsSL --retry 3 "$(release_url checksums.txt)" -o "$checksums"
  fi
  expected=$(awk -v name="$asset" '$2 == name { print $1 }' "$checksums")
  [ -n "$expected" ] || die "checksum missing for $asset"
  actual=$(sha256 "$destination")
  [ "$actual" = "$expected" ] || die "checksum mismatch for $asset"
}

build_local_cli() {
  [ -f "$script_dir/go.mod" ] || die '--local must run from the Purpory source directory'
  command -v go >/dev/null 2>&1 || die 'Go is required for a local CLI build'
  (cd "$script_dir" && go build -o "$temp_dir/purpory" ./cmd/purpory)
}

fetch_cli() {
  if [ "$mode" = local ]; then
    build_local_cli
    return
  fi
  asset=purpory-cli-$platform-$arch.tar.gz
  download "$asset" "$temp_dir/$asset"
  tar -xzf "$temp_dir/$asset" -C "$temp_dir"
  [ -x "$temp_dir/purpory" ] || die "invalid CLI archive: $asset"
}

install_cli() {
  fetch_cli
  mkdir -p -- "$bin_dir"
  install -m 755 "$temp_dir/purpory" "$bin_dir/purpory"
  printf 'Installed CLI: %s\n' "$bin_dir/purpory"
  case :$PATH: in
    *:"$bin_dir":*) ;;
    *) printf 'Add this to your shell profile: export PATH="%s:$PATH"\n' "$bin_dir" ;;
  esac
}

build_local_app() {
  [ -f "$script_dir/wails.json" ] || die '--local must run from the Purpory source directory'
  command -v wails >/dev/null 2>&1 || die 'Wails v2 is required for a local app build'
  if [ "$platform" = linux ]; then
    (cd "$script_dir" && wails build -tags webkit2_41)
  else
    (cd "$script_dir" && wails build)
  fi
}

fetch_app() {
  if [ "$mode" = local ]; then
    build_local_app
    if [ "$platform" = darwin ]; then
      local_app=$script_dir/build/bin/purpory.app
      [ -x "$local_app/Contents/MacOS/purpory" ] || die 'local app build is missing'
      ditto "$local_app" "$temp_dir/Purpory.app"
    else
      [ -x "$script_dir/build/bin/purpory" ] || die 'local app build is missing'
      install -m 755 "$script_dir/build/bin/purpory" "$temp_dir/purpory-desktop"
      cp "$script_dir/build/appicon.png" "$temp_dir/purpory.png"
    fi
    return
  fi
  if [ "$platform" = darwin ]; then
    asset=purpory-desktop-darwin-universal.zip
    download "$asset" "$temp_dir/$asset"
    ditto -x -k "$temp_dir/$asset" "$temp_dir/app"
    extracted=$(find "$temp_dir/app" -maxdepth 2 -type d -name '*.app' -print | head -n 1)
    [ -n "$extracted" ] && [ -x "$extracted/Contents/MacOS/purpory" ] || die "invalid app archive: $asset"
    ditto "$extracted" "$temp_dir/Purpory.app"
  else
    asset=purpory-desktop-linux-$arch.tar.gz
    download "$asset" "$temp_dir/$asset"
    tar -xzf "$temp_dir/$asset" -C "$temp_dir"
    [ -x "$temp_dir/purpory-desktop" ] || die "invalid app archive: $asset"
  fi
}

install_app() {
  fetch_app
  if [ "$platform" = darwin ]; then
    mkdir -p -- "$app_dir"
    [ "$app_target" != / ] || die 'refusing to replace root directory'
    rm -rf -- "$app_target"
    ditto "$temp_dir/Purpory.app" "$app_target"
  else
    mkdir -p -- "$app_dir" "$(dirname -- "$desktop_target")" "$(dirname -- "$icon_target")"
    install -m 755 "$temp_dir/purpory-desktop" "$app_target"
    install -m 644 "$temp_dir/purpory.png" "$icon_target"
    {
      printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=Purpory'
      printf 'Exec="%s"\nIcon=%s\n' "$app_target" "$icon_target"
      printf '%s\n' 'Terminal=false' 'Categories=Development;Utility;'
    } > "$desktop_target"
  fi
  printf 'Installed app: %s\n' "$app_target"
}

case "$component" in
  cli) install_cli ;;
  app) install_app ;;
  all) install_cli; install_app ;;
esac
