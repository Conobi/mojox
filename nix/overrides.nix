# Wheel patches every consumer needs, and the build-system override for
# dependencies that mojox-build compiles from a git checkout.
{ pkgs, lib, python }:
let
  normalize = import ./normalize.nix lib;
  modularLib = "${python.sitePackages}/modular/lib";

  # autoPatchelfFlags `--libs` REPLACES the hook's buildInputs search and a
  # second `--libs` does not accumulate, so every directory goes in ONE list.
  searchDirs = final: extra: [
    "--libs"
    "${pkgs.stdenv.cc.cc.lib}/lib"
    "${lib.getLib pkgs.ncurses}/lib"
    "${lib.getLib pkgs.libbsd}/lib"
    "${final.mojo-compiler}/${modularLib}"
  ] ++ extra;
in
{
  wheels = final: prev:
    lib.optionalAttrs (prev ? mojo-lldb-libs) {
      mojo-lldb-libs = prev.mojo-lldb-libs.overrideAttrs (old: {
        buildInputs = (old.buildInputs or [ ]) ++ [ final.mojo-compiler ];
        autoPatchelfFlags = searchDirs final [ ];
      });
    }
    // lib.optionalAttrs (prev ? mojo && prev ? mojo-lldb-libs) {
      mojo = prev.mojo.overrideAttrs (old: {
        buildInputs = (old.buildInputs or [ ]) ++ [ final.mojo-compiler final.mojo-lldb-libs ];
        autoPatchelfFlags = searchDirs final [ "${final.mojo-lldb-libs}/${modularLib}" ];
      });
    }
    // lib.optionalAttrs (prev ? mojo-compiler && prev ? mojo-compiler-mojo-libs) {
      # The std library ships in a separate wheel; a sandboxed dependency
      # build runs mojo from this store path alone and needs it alongside.
      mojo-compiler = prev.mojo-compiler.overrideAttrs (old: {
        postFixup = (old.postFixup or "") + ''
          ln -s ${final.mojo-compiler-mojo-libs}/${modularLib}/mojo $out/${modularLib}/mojo
        '';
      });
    };

  # Exactly the listed packages: the build-system requires of a mojox-build
  # package are not in uv.lock, so they are supplied here, with a C compiler
  # and a writable HOME (mojo's crash handler creates ~/.local/share/modular).
  mojoxBuilt = names: final: prev:
    lib.genAttrs (map normalize names) (name:
      prev.${name}.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ])
          ++ final.resolveBuildSystem {
            mojox-build = [ ];
            mojox-core = [ ];
            mojo-compiler = [ ];
            mojo-compiler-mojo-libs = [ ];
          }
          ++ [ pkgs.gcc ];
        env = (old.env or { }) // { HOME = "/tmp/home"; };
      }));
}
