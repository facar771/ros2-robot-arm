#!/usr/bin/env bash
# Run one selftest mode several times and say whether it is stable.
#
#   docker/repeat_selftest.sh <image> <mode> [runs=3]
#   e.g. docker/repeat_selftest.sh ros2-robot-arm:ci gazebo-cmd 3
#
# Each run is `docker run --rm <image> run_selftest <mode>`; its whole output is kept in
# $LOG_DIR/<mode>-<n>.log (default: a temp dir). Verdict:
#   all runs pass          -> ::notice   "STABİL"          exit 0
#   some pass, some fail   -> ::warning  "KARARSIZ (k/n)"  exit 1   <- the flaky case this exists for
#   no run passes          -> ::error    "BAŞARISIZ"       exit 1
# Annotations (::error/::warning lines) that run_selftest.sh printed in a failed run are repeated
# here, prefixed with the run number, because people who review CI often cannot open the raw log
# but can read annotations. A summary table goes to $GITHUB_STEP_SUMMARY when it is set.
# DOCKER=... overrides the docker command (tests use a stub).
set -u
IMAGE=${1:?usage: repeat_selftest.sh <image> <mode> [runs]}
MODE=${2:?usage: repeat_selftest.sh <image> <mode> [runs]}
RUNS=${3:-3}
DOCKER=${DOCKER:-docker}
LOG_DIR=${LOG_DIR:-$(mktemp -d)}
mkdir -p "$LOG_DIR"

passed=0 failed=0 results=()
for n in $(seq 1 "$RUNS"); do
    log="$LOG_DIR/$MODE-$n.log"
    start=$SECONDS
    if $DOCKER run --rm "$IMAGE" run_selftest "$MODE" >"$log" 2>&1; then
        verdict=GEÇTİ; passed=$((passed + 1))
    else
        verdict="BAŞARISIZ (çıkış $?)"; failed=$((failed + 1))
        # repeat the failure annotations of this run, tagged with the run number
        grep '^::' "$log" | sed -E "s/^(::[a-z]+ title=)/\1[koşu $n\/$RUNS] /" || true
    fi
    took=$((SECONDS - start))
    echo "[$MODE] koşu $n/$RUNS: $verdict (${took} s)"
    results+=("$n|$verdict|$took")
done

if [ "$failed" -eq 0 ]; then
    state=STABİL; level=notice; code=0
    detail="$passed/$RUNS koşu geçti"
elif [ "$passed" -eq 0 ]; then
    state=BAŞARISIZ; level=error; code=1
    detail="$RUNS/$RUNS koşu başarısız"
else
    state=KARARSIZ; level=warning; code=1
    detail="$passed/$RUNS geçti, $failed/$RUNS başarısız: aynı kodla bazen geçip bazen düşüyor"
fi
echo "::$level title=gecelik $MODE: $state ($passed/$RUNS)::$detail"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo "### $MODE: $state ($passed/$RUNS)"
        echo
        echo "| koşu | sonuç | süre (s) |"
        echo "|---|---|---|"
        for r in "${results[@]}"; do IFS='|' read -r n v t <<<"$r"; echo "| $n | $v | $t |"; done
        echo
    } >>"$GITHUB_STEP_SUMMARY"
fi
exit $code
