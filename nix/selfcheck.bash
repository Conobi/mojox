# Shell self-check, inlined into the shellHook by hook.nix.
# Inputs: MOJOX_VENV, MOJOX_SITE_PACKAGES, MOJOX_FLAKE_VERSION and, only when
# the consumer asked for LLVM tools, MOJOX_EXPECT_LLVM (a major version).

mojox_selfcheck() {
    local report key value tool resolved
    local mojo_path="" cc_path="" mojox_version="" failed=0

    local status=0
    report="$("${UV_PYTHON:-python}" -m mojox_core.io.hostcheck --root "$REPO_ROOT" 2>&1)" || status=$?
    if [ "$status" -ne 0 ]; then
        echo "mojox shell self-check: mojox-core could not report the toolchain:" >&2
        echo "$report" >&2
        # Exit 2 is hostcheck's own configuration error; anything else means the module did not run.
        if [ "$status" -ne 2 ]; then
            echo "Add mojox>=0.7 and mojox-core>=0.7 to the dev dependency group and run 'uv lock'." >&2
        fi
        return 1
    fi
    while IFS='=' read -r key value; do
        case "$key" in
            mojo) mojo_path="$value" ;;
            cc) cc_path="$value" ;;
            mojox) mojox_version="$value" ;;
        esac
    done <<<"$report"

    for tool in \
        "mojo:$mojo_path" \
        "cc:$cc_path" \
        "uv:$(readlink -f "$(command -v uv)")" \
        "rustc:$(readlink -f "$(command -v rustc)")" \
        "cargo:$(readlink -f "$(command -v cargo)")" \
        "ld.lld:$(readlink -f "$(command -v ld.lld)")"; do
        resolved="${tool#*:}"
        case "$resolved" in
            /nix/store/*) ;;
            *)
                echo "mojox shell self-check: ${tool%%:*} resolves to '${resolved:-nothing}', outside /nix/store" >&2
                failed=1
                ;;
        esac
    done

    # Plain lld is also a store path; only the wrapper (a script) records library RUNPATHs.
    if [ "$(head -c 2 "$(readlink -f "$(command -v ld.lld)")" 2>/dev/null)" != '#!' ]; then
        echo "mojox shell self-check: ld.lld is not the wrapped linker; dlopened shims would lose their library RUNPATH" >&2
        failed=1
    fi

    if [ -n "$mojox_version" ] && [ "$mojox_version" != "$MOJOX_FLAKE_VERSION" ]; then
        echo "mojox shell self-check: warning: the flake is mojox $MOJOX_FLAKE_VERSION but the venv has mojox $mojox_version" >&2
    fi

    if [ -n "${MOJOX_EXPECT_LLVM:-}" ]; then
        local mojo_binary="$MOJOX_VENV/$MOJOX_SITE_PACKAGES/modular/bin/mojo" wanted have
        wanted="$(grep -a -o -m1 'LLVM version [0-9]*' "$mojo_binary" | grep -o '[0-9]*$')"
        have="$(llc --version | grep -o -m1 'LLVM version [0-9]*' | grep -o '[0-9]*$')"
        if [ -z "$wanted" ] || [ "$wanted" != "$have" ]; then
            echo "mojox shell self-check: the locked Mojo embeds LLVM '${wanted:-unknown}' but llc is LLVM '${have:-unknown}'" >&2
            failed=1
        fi
    fi

    return "$failed"
}
