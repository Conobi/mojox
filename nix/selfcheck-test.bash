# Behavioural test for selfcheck.bash with a fake hostcheck interpreter.
# Inputs: SELFCHECK (path to selfcheck.bash), WRAPPED_LLD and BARE_LLD
# (directories holding an ld.lld), a PATH that already has uv.

set -u
# shellcheck source=/dev/null
source "$SELFCHECK"

work="$(mktemp -d)"
fake="$work/fake-python"
cat >"$fake" <<'SCRIPT'
#!/bin/sh
printf '%s\n' "$FAKE_REPORT"
exit "$FAKE_STATUS"
SCRIPT
chmod +x "$fake"

# Stand-ins for a host toolchain that shadows the Nix one.
mkdir "$work/host-cargo" "$work/host-rustc"
printf '#!/bin/sh\n' >"$work/host-cargo/cargo"
printf '#!/bin/sh\n' >"$work/host-rustc/rustc"
chmod +x "$work/host-cargo/cargo" "$work/host-rustc/rustc"

export UV_PYTHON="$fake" REPO_ROOT="$work" MOJOX_FLAKE_VERSION=0.7.0
good_report='mojo=/nix/store/mojo/bin/mojo
cc=/nix/store/cc/bin/cc
mojox=0.7.0'
base_path="$PATH"
failures=0

# run_case <name> <status> <report> <lld dir> <expected rc> <required text|-> <forbidden text|->
run_case() {
    local name="$1" rc out
    FAKE_STATUS="$2" FAKE_REPORT="$3" PATH="$4:$base_path"
    export FAKE_STATUS FAKE_REPORT PATH
    out="$(mojox_selfcheck 2>&1)"
    rc=$?
    PATH="$base_path"
    [ "$rc" -eq "$5" ] || { echo "FAIL $name: rc=$rc, wanted $5: $out"; failures=$((failures + 1)); return; }
    [ "$6" = - ] || case "$out" in *"$6"*) ;; *) echo "FAIL $name: missing '$6' in: $out"; failures=$((failures + 1)); return ;; esac
    [ "$7" = - ] || case "$out" in *"$7"*) echo "FAIL $name: unexpected '$7' in: $out"; failures=$((failures + 1)); return ;; esac
    echo "ok $name"
}

run_case exit2-no-hint 2 "bad root" "$WRAPPED_LLD" 1 "could not report" "Add mojox"
run_case exit1-hint 1 "no module" "$WRAPPED_LLD" 1 "Add mojox>=0.7" -
run_case wrapped-lld-passes 0 "$good_report" "$WRAPPED_LLD" 0 - "self-check"
run_case bare-lld-fails 0 "$good_report" "$BARE_LLD" 1 "not the wrapped linker" -
run_case host-cargo-fails 0 "$good_report" "$work/host-cargo:$WRAPPED_LLD" 1 "cargo resolves to" -
run_case host-rustc-fails 0 "$good_report" "$work/host-rustc:$WRAPPED_LLD" 1 "rustc resolves to" -

exit "$failures"
