#!/bin/bash

#strict mode
set -euo pipefail
IFS=$'\n\t'

#generate configs used by wraxl from nxconfigs

if [ ! -d configs ]; then
    git clone git://ala-git.wrs.com/users/buildadmin/configs
fi

(cd configs; git fetch --quiet --all; git reset --hard origin/master)

cleanup_configs() {
    randconfig=configs/$1
    output=$2
    grep -v '#' "$randconfig" | grep -v '^$' | grep -v world | sed 's/--enable-bootimage=noimage//' | tr -s ' ' > "${output}.tmp"
    if [ ! -f "$output" ]; then
        mv "${output}.tmp" "$output"
    elif ! diff -q "${output}.tmp" "$output" > /dev/null ; then
        mv "${output}.tmp" "$output"
    else
        rm -f "${output}.tmp"
    fi
}

world_configs() {
    randconfig=configs/$1
    output=$2
    grep -v '#' "$randconfig" | grep -v '^$' | grep world | sed 's/--enable-bootimage=noimage//' | tr -s ' ' > "${output}.tmp"
    if [ ! -f "$output" ]; then
        mv "${output}.tmp" "$output"
    elif ! diff -q "${output}.tmp" "$output" > /dev/null ; then
        mv "${output}.tmp" "$output"
    else
        rm -f "${output}.tmp"
    fi
}

cleanup_configs "randconfigs-WRLINUX_5_0_1_HEAD" "randconfigs-5"
cleanup_configs "randconfigs-WRLINUX_6_0_HEAD" "randconfigs-6"
cleanup_configs "randconfigs-WRLINUX_7_0_HEAD" "randconfigs-7"
cleanup_configs "randconfigs-WRLINUX_8_0_HEAD" "randconfigs-8"
cleanup_configs "randconfigs-master" "randconfigs-9"

world_configs "randconfigs-WRLINUX_5_0_1_HEAD" "worldconfigs-5"
world_configs "randconfigs-WRLINUX_6_0_HEAD" "worldconfigs-6"
world_configs "randconfigs-WRLINUX_7_0_HEAD" "worldconfigs-7"
world_configs "randconfigs-WRLINUX_8_0_HEAD" "worldconfigs-8"
world_configs "randconfigs-master" "worldconfigs-9"
