# Unit tests for overrides.nix against stand-in packages.
{ pkgs, lib }:
let
  python = pkgs.python314;
  overrides = import ./overrides.nix { inherit pkgs lib python; };
  modularLib = "${python.sitePackages}/modular/lib";

  # overrideAttrs applies the function to the stand-in's own attributes.
  stub = base: { overrideAttrs = f: base // f base; };
  final = {
    mojo-compiler = "COMPILER";
    mojo-lldb-libs = "LLDB";
    mojo-compiler-mojo-libs = "LIBS";
    resolveBuildSystem = _: [ "build-system" ];
  };
  prev = {
    mojo-lldb-libs = stub { };
    mojo = stub { };
    mojo-compiler = stub { postFixup = "base\n"; };
    mojo-compiler-mojo-libs = stub { };
    foo-bar-baz = stub { };
    bouclette = stub { nativeBuildInputs = [ "orig" ]; env = { A = "1"; }; };
  };
  wheels = overrides.wheels final prev;
  built = overrides.mojoxBuilt [ "Bouclette" ] final prev;
  dotted = overrides.mojoxBuilt [ "Foo_Bar.baz" ] final prev;
  libsFlags = flags: lib.count (flag: flag == "--libs") flags;
in
{
  testLldbLibsPassOneLibsFlag = {
    expr = libsFlags wheels.mojo-lldb-libs.autoPatchelfFlags;
    expected = 1;
  };
  testMojoPassesOneLibsFlag = {
    expr = libsFlags wheels.mojo.autoPatchelfFlags;
    expected = 1;
  };
  testLibsFlagComesFirst = {
    expr = lib.head wheels.mojo.autoPatchelfFlags;
    expected = "--libs";
  };
  testMojoSearchesTheSiblingWheels = {
    expr = map (dir: lib.elem dir wheels.mojo.autoPatchelfFlags) [ "COMPILER/${modularLib}" "LLDB/${modularLib}" ];
    expected = [ true true ];
  };
  testCompilerGetsTheStdSymlinkAfterItsOwnFixup = {
    expr = lib.hasPrefix "base\nln -s LIBS/${modularLib}/mojo " wheels.mojo-compiler.postFixup;
    expected = true;
  };
  testNothingIsOverriddenWhenTheWheelsAreAbsent = {
    expr = overrides.wheels final { };
    expected = { };
  };
  testOnlyTheListedPackagesAreOverridden = {
    expr = builtins.attrNames built;
    expected = [ "bouclette" ];
  };
  testBuiltDepGetsBuildSystemAndACompiler = {
    expr = map builtins.toString (lib.take 2 built.bouclette.nativeBuildInputs);
    expected = [ "orig" "build-system" ];
  };
  testBuiltDepKeepsItsEnvAndGetsAWritableHome = {
    expr = built.bouclette.env;
    expected = { A = "1"; HOME = "/tmp/home"; };
  };
  testBuiltNamesAreMatchedByPep503Normalisation = {
    expr = builtins.attrNames dotted;
    expected = [ "foo-bar-baz" ];
  };
}
