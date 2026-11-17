# lib.mkDevShell: a Nix shell whose Python environment is the consumer's
# uv.lock minus the project itself, with the Mojo wheels patched in the store.
#
# Arguments:
#   src               the consumer's ./. (read for pyproject.toml and uv.lock)
#   projectName       the consumer project's single name, PEP 503-normalised;
#                     excluded from the venv (it is compiled from source)
#   mojoxBuiltDeps    locked deps compiled with mojox-build, e.g. [ "bouclette" ];
#                     exactly these names, nothing is detected from uv.lock.
#                     Every locked git-sourced package must be listed.
#   extraPackages     pkgs -> [ packages ], added to the shell
#   extraEnv          { NAME = "value"; }, exported after the lib's variables
#                     (so it overrides them) and before the self-check
#   shellHook         the consumer's hook; runs after the lib's hook
#   llvmPackages      e.g. pkgs.llvmPackages_20; null (default) means no LLVM
#                     tools. When set, the self-check compares the LLVM major
#                     with the one the locked Mojo embeds.
#   python            interpreter of the venv (default python314)
#   pythonOverrides   final: prev: { ... }, applied after the lib's overrides
#
# Evaluation fails, naming the fix, when uv.lock has an unsupported schema
# version or is out of sync with pyproject.toml, a git dependency is missing
# from mojoxBuiltDeps, or a path, editable, workspace, directory or virtual
# source is declared or locked. Evaluation fetches git
# dependencies with builtins.fetchGit (public repos), so it needs network
# once; pure evaluation is unsupported.
{ pkgs, pyproject-nix, uv2nix, pyproject-build-systems, mojoxVersion }:
let
  lib = pkgs.lib;
  lockChecks = import ./lock.nix { inherit lib pyproject-nix; };
in
{ src
, projectName
, mojoxBuiltDeps ? [ ]
, extraPackages ? (_: [ ])
, extraEnv ? { }
, shellHook ? ""
, llvmPackages ? null
, python ? pkgs.python314
, pythonOverrides ? (final: prev: { })
}:
let
  pyproject = builtins.fromTOML (builtins.readFile (src + "/pyproject.toml"));
  lock = builtins.fromTOML (builtins.readFile (src + "/uv.lock"));
  found = lockChecks.problems { inherit pyproject lock projectName mojoxBuiltDeps; };

  overrides = import ./overrides.nix { inherit pkgs lib python; };
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = src; };
  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
      (lib.composeManyExtensions [
        pyproject-build-systems.overlays.wheel
        (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
        overrides.wheels
        (overrides.mojoxBuilt mojoxBuiltDeps)
        pythonOverrides
      ]);

  project = lockChecks.findProject lock projectName;
  direct = map (d: d.name) (
    (project.dependencies or [ ]) ++ ((project."dev-dependencies" or { }).dev or [ ])
  );
  venv = pythonSet.mkVirtualEnv "${projectName}-dev-env" (lib.genAttrs direct (_: [ ]));
in
if found != [ ] then
  throw "mojox.lib.mkDevShell (${projectName}):\n  - ${lib.concatStringsSep "\n  - " found}"
else
  pkgs.mkShell {
    packages =
      [ venv ]
      ++ import ./packages.nix pkgs
      ++ lib.optionals (llvmPackages != null) [ llvmPackages.llvm (lib.lowPrio llvmPackages.clang) ]
      ++ extraPackages pkgs;
    shellHook = import ./hook.nix {
      inherit lib pkgs venv python mojoxVersion llvmPackages extraEnv;
      consumerHook = shellHook;
    };
    passthru = { inherit venv pythonSet; };
  }
