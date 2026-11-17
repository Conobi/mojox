# Unit tests for lock.nix over small inline pyproject.toml / uv.lock documents.
{ lib, lockChecks }:
let
  pyproject = builtins.fromTOML ''
    [project]
    name = "demo"
    dependencies = ["mojox>=0.7", "bouclette @ git+https://github.com/Conobi/bouclette"]

    [dependency-groups]
    dev = ["pytest>=9", "mojox-build>=0.7"]
  '';

  lock = builtins.fromTOML ''
    version = 1

    [[package]]
    name = "demo"
    version = "0.1.0"
    source = { editable = "." }

    [package.metadata]
    requires-dist = [
        { name = "bouclette", git = "https://github.com/Conobi/bouclette?rev=abc" },
        { name = "mojox", specifier = ">=0.7" },
    ]

    [package.metadata.requires-dev]
    dev = [
        { name = "mojox-build", specifier = ">=0.7" },
        { name = "pytest", specifier = ">=9" },
    ]

    [[package]]
    name = "bouclette"
    version = "0.1.0"
    source = { git = "https://github.com/Conobi/bouclette?rev=abc#abc" }

    [[package]]
    name = "mojox-build"
    version = "0.7.0"
    source = { registry = "https://pypi.org/simple" }

    [[package]]
    name = "mojox-core"
    version = "0.7.0"
    source = { registry = "https://pypi.org/simple" }

    [[package]]
    name = "mojo-compiler"
    version = "1.1.0"
    source = { registry = "https://pypi.org/simple" }

    [[package]]
    name = "mojo-compiler-mojo-libs"
    version = "1.1.0"
    source = { registry = "https://pypi.org/simple" }
  '';

  base = { inherit pyproject lock; projectName = "demo"; mojoxBuiltDeps = [ "bouclette" ]; };
  withDeps = deps: pyproject // { project = pyproject.project // { dependencies = deps; }; };
  withSources = sources: pyproject // { tool.uv.sources = sources; };
in
{
  testInSyncLockHasNoProblems = {
    expr = lockChecks.problems base;
    expected = [ ];
  };
  testNormalizeFollowsPep503 = {
    expr = map lockChecks.normalize [ "Mojo_Compiler" "mojo.compiler" "a--b" ];
    expected = [ "mojo-compiler" "mojo-compiler" "a-b" ];
  };
  testAddedDependencyIsStale = {
    expr = lockChecks.staleReasons {
      inherit lock;
      pyproject = withDeps (pyproject.project.dependencies ++ [ "h11>=0.16" ]);
      projectName = "demo";
    };
    expected = [ "dependencies: 'h11 (>=0.16)' is in pyproject.toml but not recorded in uv.lock as written" ];
  };
  testChangedSpecifierIsStale = {
    expr = builtins.length (lockChecks.staleReasons {
      inherit lock;
      pyproject = withDeps [ "mojox>=0.8" "bouclette @ git+https://github.com/Conobi/bouclette" ];
      projectName = "demo";
    });
    expected = 2;
  };
  testMissingProjectIsStale = {
    expr = lockChecks.staleReasons { inherit pyproject lock; projectName = "other"; };
    expected = [ "project 'other' is missing from uv.lock" ];
  };
  testGitDependencyMustBeListed = {
    expr = lockChecks.gitDepsMissing { inherit lock; projectName = "demo"; mojoxBuiltDeps = [ ]; };
    expected = [ "bouclette" ];
  };
  testListedGitDependencyPasses = {
    expr = lockChecks.gitDepsMissing { inherit lock; projectName = "demo"; mojoxBuiltDeps = [ "Bouclette" ]; };
    expected = [ ];
  };
  testPathSourceIsRejected = {
    expr = lockChecks.unsupportedSources {
      inherit lock;
      pyproject = withSources { sibling = { path = "../sibling"; }; };
      projectName = "demo";
    };
    expected = [ "sibling" ];
  };
  testEditableAndWorkspaceSourcesAreRejected = {
    expr = lockChecks.unsupportedSources {
      inherit lock;
      pyproject = withSources { a = { editable = "../a"; }; b = { workspace = true; }; c = { git = "https://x/c"; }; };
      projectName = "demo";
    };
    expected = [ "a" "b" ];
  };
  testBuildSystemPackagesMustBeLocked = {
    expr = lockChecks.missingBuildSystemPackages {
      lock = lock // { package = lib.filter (p: p.name != "mojox-build") lock.package; };
      mojoxBuiltDeps = [ "bouclette" ];
    };
    expected = [ "mojox-build" ];
  };
  testUnknownBuiltDependencyIsReported = {
    expr = lockChecks.unknownBuiltDeps { inherit lock; mojoxBuiltDeps = [ "bouclette" "ghost" ]; };
    expected = [ "ghost" ];
  };
  testMissingGitDependencyMessageNamesThePackageAndTheFix = {
    expr = lockChecks.problems (base // { mojoxBuiltDeps = [ ]; });
    expected = [
      "uv.lock has the git-sourced package 'bouclette' but mojoxBuiltDeps does not list it. Add \"bouclette\" to mojoxBuiltDeps in flake.nix."
    ];
  };
  testStaleLockMessageTellsToRunUvLock = {
    expr = lockChecks.problems (base // { pyproject = withDeps (pyproject.project.dependencies ++ [ "h11>=0.16" ]); });
    expected = [
      "uv.lock is out of sync with pyproject.toml: dependencies: 'h11 (>=0.16)' is in pyproject.toml but not recorded in uv.lock as written. Run `uv lock`, `git add uv.lock`, then re-enter the shell."
    ];
  };
  testUnsupportedSourceMessageNamesThePackage = {
    expr = lockChecks.problems (base // { pyproject = withSources { sibling = { path = "../sibling"; }; }; });
    expected = [
      "'sibling' is a path, editable, workspace or directory source. The Nix shell supports git and registry sources only."
    ];
  };
  testUnsupportedSchemaVersionIsReportedFirst = {
    expr = lockChecks.problems (base // { lock = lock // { version = 2; }; });
    expected = [
      "uv.lock schema version 2 is not supported (this mojox flake reads version 1). Re-lock with a compatible uv or update the mojox flake."
    ];
  };
  testSupportedSchemaVersionIgnoresRevision = {
    expr = lockChecks.problems (base // { lock = lock // { revision = 9; }; });
    expected = [ ];
  };
}
