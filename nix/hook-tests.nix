# Unit tests for the rendered shellHook: ordering, escaping and the LLVM expectation.
{ pkgs, lib }:
let
  render = args: import ./hook.nix ({
    inherit lib pkgs;
    venv = "/nix/store/venv";
    python = pkgs.python314;
    mojoxVersion = "0.7.0";
    llvmPackages = null;
    extraEnv = { };
    consumerHook = "echo consumer";
  } // args);

  hook = render { extraEnv = { FOO = "it's"; UV_NO_SYNC = "0"; }; };
  position = needle: builtins.stringLength (builtins.head (lib.splitString needle hook));
  strictlyIncreasing = xs:
    lib.all (i: builtins.elemAt xs i < builtins.elemAt xs (i + 1)) (lib.range 0 (builtins.length xs - 2));
in
{
  testLibHookThenExtraEnvThenSelfCheckThenConsumerHook = {
    expr = strictlyIncreasing [
      (position "export UV_NO_SYNC=1")
      (position "export UV_NO_SYNC=0")
      (position "mojox_selfcheck || exit 1")
      (position "echo consumer")
    ];
    expected = true;
  };
  testExtraEnvValuesAreShellEscaped = {
    expr = lib.hasInfix "export FOO='it'\\''s'" hook;
    expected = true;
  };
  testInheritedLibraryPathsAreUnsetFirst = {
    expr = lib.hasPrefix "unset PYTHONPATH LD_LIBRARY_PATH RUSTUP_TOOLCHAIN\n" hook;
    expected = true;
  };
  testUvPointsAtTheStoreVenv = {
    expr = lib.hasInfix "export UV_PYTHON=/nix/store/venv/bin/python" hook;
    expected = true;
  };
  testFlakeVersionIsExportedForTheMismatchWarning = {
    expr = lib.hasInfix "export MOJOX_FLAKE_VERSION=0.7.0" hook;
    expected = true;
  };
  testUvSyncNoticeIsInteractiveOnly = {
    expr = lib.hasInfix "case $- in\n  *i*)" hook;
    expected = true;
  };
  testNoLlvmExpectationByDefault = {
    expr = lib.hasInfix "export MOJOX_EXPECT_LLVM=" hook;
    expected = false;
  };
  testLlvmExpectationIsTheMajorOfTheSet = {
    expr = lib.hasInfix "export MOJOX_EXPECT_LLVM=20" (render { llvmPackages = pkgs.llvmPackages_20; });
    expected = true;
  };
  testExtraEnvRejectsNamesThatAreNotShellIdentifiers = {
    expr = map (name: (builtins.tryEval (builtins.deepSeq (render { extraEnv.${name} = "x"; }) true)).success)
      [ "A-B" "1A" "A B" "A;rm" "" "A=B" ];
    expected = [ false false false false false false ];
  };
  testExtraEnvRejectsLibOwnedMojoxNames = {
    expr = map (name: (builtins.tryEval (builtins.deepSeq (render { extraEnv.${name} = "x"; }) true)).success)
      [ "MOJOX_VENV" "MOJOX_SITE_PACKAGES" "MOJOX_FLAKE_VERSION" "MOJOX_EXPECT_LLVM" "MOJOX_ANYTHING" ];
    expected = [ false false false false false ];
  };
  testExtraEnvStillOverridesOtherLibKeys = {
    expr = lib.hasInfix "export UV_PYTHON_DOWNLOADS=automatic" (render { extraEnv.UV_PYTHON_DOWNLOADS = "automatic"; });
    expected = true;
  };
  testNixDevelopOutputIsKeptOutOfTheLinkerFlags = {
    expr = [
      (lib.hasInfix "NIX_LDFLAGS=\"\${NIX_LDFLAGS//-rpath $out\\/lib /}\"" hook)
      (lib.hasInfix "  unset out\n" hook)
    ];
    expected = [ true true ];
  };
  testExtraEnvAcceptsUnderscoreAndDigits = {
    expr = (builtins.tryEval (builtins.deepSeq (render { extraEnv._A1_b = "x"; }) true)).success;
    expected = true;
  };
}
