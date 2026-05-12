#!/bin/bash

SELF=$(readlink -f "$0")
HERE=${SELF%/*}
ARCH=$(uname -m)

##libs

export PATH="${HERE}/bin:$PATH"

export LD_LIBRARY_PATH="${HERE}/ext-libs/lib"

if [ "${XDG_SESSION_TYPE}" = "wayland" ]; then
    exec code --ozone-platform=x11 "$@"
else
    exec code "$@"
fi
