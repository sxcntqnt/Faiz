#!/usr/bin/env bash
scylla_health() {
    tailscale status | awk '
    $2 ~ /^scylla/ {
        found=1
        total++

        if ($0 ~ /offline/) {
            offline++
        } else {
            online++
            last_ok=$2
        }
    }

    END {
        if (!found) {
            print "CRITICAL: no scylla nodes found"
            exit 2
        }

        if (online > 0) {
            printf "OK: scylla reachable (%d/%d online, last: %s)\n",
                   online, total, last_ok
            exit 0
        }

        printf "CRITICAL: scylla down (%d nodes, all offline)\n", total
        exit 1
    }'
}

scylla_health
