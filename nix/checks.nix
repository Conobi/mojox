# `nix flake check` targets: lock-check unit tests, hook script lint and
# render tests, override tests, and the base tool set resolving into /nix/store.
{ pkgs, pyproject-nix }:
let
  lib = pkgs.lib;
  lockChecks = import ./lock.nix { inherit lib pyproject-nix; };
  lockFailures = lib.debug.runTests (import ./lock-tests.nix { inherit lib lockChecks; });
  overrideFailures = lib.debug.runTests (import ./override-tests.nix { inherit pkgs lib; });
  hookFailures = lib.debug.runTests (import ./hook-tests.nix { inherit pkgs lib; });
in
{
  lock-checks =
    assert lockFailures == [ ] || throw "lock.nix unit tests failed: ${builtins.toJSON lockFailures}";
    pkgs.runCommand "lock-checks" { } "touch $out";

  override-checks =
    assert overrideFailures == [ ] || throw "overrides.nix unit tests failed: ${builtins.toJSON overrideFailures}";
    pkgs.runCommand "override-checks" { } "touch $out";

  hook-render =
    assert hookFailures == [ ] || throw "hook.nix unit tests failed: ${builtins.toJSON hookFailures}";
    pkgs.runCommand "hook-render" { } "touch $out";

  hook-script = pkgs.runCommand "hook-script" { nativeBuildInputs = [ pkgs.bash pkgs.shellcheck ]; } ''
    bash -n ${./selfcheck.bash}
    shellcheck --shell=bash ${./selfcheck.bash}
    touch $out
  '';

  selfcheck-behaviour = pkgs.runCommand "selfcheck-behaviour"
    { nativeBuildInputs = (import ./packages.nix pkgs) ++ [ pkgs.bash pkgs.shellcheck ]; } ''
    shellcheck --shell=bash ${./selfcheck-test.bash}
    mkdir wrapped bare
    ln -s "$(command -v ld.lld)" wrapped/ld.lld
    ln -s ${pkgs.lld}/bin/ld.lld bare/ld.lld
    SELFCHECK=${./selfcheck.bash} WRAPPED_LLD="$PWD/wrapped" BARE_LLD="$PWD/bare" \
      bash ${./selfcheck-test.bash}
    touch $out
  '';

  base-tools = pkgs.runCommand "base-tools" { nativeBuildInputs = (import ./packages.nix pkgs) ++ [ pkgs.binutils ]; } ''
    for tool in cc c++ cargo rustc ld.lld pkg-config cmake git uv unzip ip; do
      path="$(command -v "$tool")" || { echo "missing tool: $tool" >&2; exit 1; }
      case "$(readlink -f "$path")" in
        /nix/store/*) ;;
        *) echo "$tool is outside /nix/store: $path" >&2; exit 1 ;;
      esac
    done
    # `-fuse-ld=lld` runs the first ld.lld on PATH. Only the wrapped linker
    # records the RUNPATH of the store libraries a cdylib links against, which
    # a dlopened shim needs; plain lld leaves it out.
    printf 'int zlibVersion(void);\nint probe(void) { return zlibVersion() != 0; }\n' > probe.c
    cc -shared -fPIC -fuse-ld=lld probe.c -o libprobe.so -lz
    runpath="$(LC_ALL=C readelf -d libprobe.so | grep -E 'RUNPATH|RPATH' || true)"
    case "$runpath" in
      *${pkgs.zlib}/lib*) ;;
      *) echo "lld did not record the zlib RUNPATH (is ld.lld the wrapped linker?): $runpath" >&2; exit 1 ;;
    esac
    touch $out
  '';
}
