#!/usr/bin/env bash
# Installs the free SysML v2 parser (OMG Pilot Implementation, LGPL) and a Java runtime into one folder.
# Nothing is installed system-wide and no sudo is used. Run once; the model checks find it through SYSML_HOME.
#   SYSML_HOME=<folder> scripts/sysml/install_parser.sh
set -euo pipefail
HOME_DIR="${SYSML_HOME:-$HOME/.cache/entelim-sysml}"
RELEASE="2026-09.1"
KERNEL="jupyter-sysml-kernel-0.63.1.zip"
mkdir -p "$HOME_DIR"
cd "$HOME_DIR"
if ! ls jdk*/bin/javac >/dev/null 2>&1; then
  curl -fsSL -o jdk.tgz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"
  tar xzf jdk.tgz
  rm jdk.tgz
fi
if [ ! -d kernel/sysml ]; then
  curl -fsSL -o kernel.zip "https://github.com/Systems-Modeling/SysML-v2-Pilot-Implementation/releases/download/$RELEASE/$KERNEL"
  unzip -q kernel.zip -d kernel
  rm kernel.zip
fi
echo "SysML parser ready in $HOME_DIR"
