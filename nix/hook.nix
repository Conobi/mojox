# The lib's shellHook: environment first, then the consumer's extraEnv, then
# the self-check, then the consumer's own hook. Everything is exported from
# the hook (mkShell attributes were silently dropped for SSL_CERT_FILE in a
# probe), so extraEnv overrides the lib's values simply by running later.
{ lib, pkgs, venv, python, mojoxVersion, llvmPackages, extraEnv, consumerHook }:
let
  libOwned = name: lib.hasPrefix "MOJOX_" name;
  validName = name: builtins.match "[A-Za-z_][A-Za-z0-9_]*" name != null;
  exports = lib.concatStringsSep "\n"
    (lib.mapAttrsToList (name: value:
      assert lib.assertMsg (validName name)
        "mkDevShell: extraEnv name '${name}' is not a valid shell identifier ([A-Za-z_][A-Za-z0-9_]*)";
      assert lib.assertMsg (!libOwned name)
        "mkDevShell: extraEnv name '${name}' is reserved: MOJOX_* variables are set by the lib";
      "export ${name}=${lib.escapeShellArg (toString value)}") extraEnv);
  expectLlvm = lib.optionalString (llvmPackages != null)
    "export MOJOX_EXPECT_LLVM=${lib.versions.major llvmPackages.llvm.version}";
in
''
  unset PYTHONPATH LD_LIBRARY_PATH RUSTUP_TOOLCHAIN
  # `nix develop` sets $out to a project-local directory and the compiler
  # wrapper has already turned it into a bogus `-rpath $out/lib`.
  if [ -n "''${out:-}" ]; then
    NIX_LDFLAGS="''${NIX_LDFLAGS//-rpath $out\/lib /}"
    unset out
  fi
  export UV_NO_SYNC=1
  export UV_PYTHON_DOWNLOADS=never
  export UV_PYTHON=${venv}/bin/python
  export SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt
  export GIT_SSL_CAINFO=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt
  REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  export REPO_ROOT
  ${exports}
  export MOJOX_VENV=${venv}
  export MOJOX_SITE_PACKAGES=${python.sitePackages}
  export MOJOX_FLAKE_VERSION=${mojoxVersion}
  ${expectLlvm}
  ${builtins.readFile ./selfcheck.bash}
  mojox_selfcheck || exit 1
  case $- in
    *i*) echo "mojox: 'uv sync' is unsupported in this shell (the venv is read-only); edit pyproject.toml, run 'uv lock' and re-enter." >&2 ;;
  esac
  ${consumerHook}
''
