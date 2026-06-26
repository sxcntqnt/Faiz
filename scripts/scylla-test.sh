#!/usr/bin/env bash
scylla_test() {
    tailscale status | awk '
    $2 ~ /^scylla/ {
        found=1

        if ($0 ~ /offline/) {
            printf "FAIL %s offline\n", $2
            offline++
        } else {
            printf "OK %s online\n", $2
            online++
        }
    }

    END {
        if (found == 0) {
            print "FAIL no scylla-node hosts found"
            exit 2
        }

        printf "Summary: %d online, %d offline\n", online, offline

        # Healthy if any node is online
        if (online > 0)
            exit 0

        exit 1
    }'
}

scylla_test
