#!/usr/bin/env bash
# Installs the free SysML v2 parser (OMG Pilot Implementation, Eclipse Public License 2.0) and a Java runtime into one folder.
# Nothing is installed system-wide and no sudo is used. Run once; the model checks find it through SYSML_HOME.
#   SYSML_HOME=<folder> scripts/sysml/install_parser.sh
# Each download is checked against the SHA-256 pinned below and refused when it differs.
set -euo pipefail
HOME_DIR="${SYSML_HOME:-$HOME/.cache/entelim-sysml}"
RELEASE="2026-09.1"
KERNEL="jupyter-sysml-kernel-0.63.1.zip"
KERNEL_SHA256="2ab938037b8d3a02a3518019b6e81b356a994f4a5214727bfedcfd64bad25415"
JDK_URL="https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz"
JDK_SHA256="d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c"

verify() { # file expected-sha256
  local got
  got="$(sha256sum "$1" | cut -d' ' -f1)"
  if [ "$got" != "$2" ]; then
    echo "REFUSED: $1 has SHA-256 $got, expected $2. Nothing was unpacked." >&2
    rm -f "$1"
    exit 1
  fi
}

mkdir -p "$HOME_DIR"
cd "$HOME_DIR"
if ! ls jdk*/bin/javac >/dev/null 2>&1; then
  curl -fsSL -o jdk.tgz "$JDK_URL"
  verify jdk.tgz "$JDK_SHA256"
  tar xzf jdk.tgz
  rm jdk.tgz
fi
if [ ! -d kernel/sysml ]; then
  curl -fsSL -o kernel.zip "https://github.com/Systems-Modeling/SysML-v2-Pilot-Implementation/releases/download/$RELEASE/$KERNEL"
  verify kernel.zip "$KERNEL_SHA256"
  unzip -q kernel.zip -d kernel
  rm kernel.zip
fi
echo "SysML parser ready in $HOME_DIR"
