# Pure checks over a consumer's pyproject.toml and uv.lock. mkDevShell turns
# any problem into an evaluation error that names the fix.
{ lib, pyproject-nix }:
let
  inherit (lib) any elem filter concatStringsSep isString isAttrs sort unique hasInfix;

  # PEP 503 name normalisation.
  normalize = import ./normalize.nix lib;

  # Order-insensitive "op+version" fragments of a requirement.
  specKeyOfConditions = conditions:
    concatStringsSep "," (sort builtins.lessThan
      (map (c: c.op + builtins.replaceStrings [ " " ] [ "" ] c.version.str) conditions));

  specKeyOfText = text:
    concatStringsSep "," (sort builtins.lessThan
      (map (s: builtins.replaceStrings [ " " ] [ "" ] s) (lib.splitString "," text)));

  # A URL requirement ("name @ git+...") is compared by name only (spec = null).
  fromPyproject = requirement:
    let parsed = pyproject-nix.lib.pep508.parseString requirement; in {
      name = normalize parsed.name;
      spec = if parsed.url or null != null then null else specKeyOfConditions parsed.conditions;
    };

  fromLock = entry: {
    name = normalize entry.name;
    spec = if entry ? specifier then specKeyOfText entry.specifier else null;
  };

  isExtraEntry = entry: hasInfix "extra ==" (entry.marker or "");

  covers = recorded: declared:
    any (r: r.name == declared.name && (declared.spec == null || r.spec == null || r.spec == declared.spec)) recorded;

  describe = entry: if entry.spec == null then entry.name else "${entry.name} (${entry.spec})";

  compare = label: declared: recorded:
    (map (d: "${label}: '${describe d}' is in pyproject.toml but not recorded in uv.lock as written")
      (filter (d: !(covers recorded d)) declared))
    ++ (map (r: "${label}: '${describe r}' is recorded in uv.lock but not in pyproject.toml")
      (filter (r: !(covers declared r)) recorded));

  findProject = lock: projectName:
    let hits = filter (p: normalize p.name == normalize projectName) (lock.package or [ ]);
    in if hits == [ ] then null else builtins.head hits;

  devGroup = pyproject: (pyproject."dependency-groups" or { }).dev or [ ];
in
rec {
  inherit normalize findProject;

  # Why uv.lock no longer matches pyproject.toml (empty when in sync).
  staleReasons = { pyproject, lock, projectName }:
    let project = findProject lock projectName; in
    if project == null then [ "project '${projectName}' is missing from uv.lock" ]
    else
      let
        meta = project.metadata or { };
        recorded = map fromLock (filter (e: !(isExtraEntry e)) (meta.requires-dist or [ ]));
        recordedDev = map fromLock ((meta.requires-dev or { }).dev or [ ]);
        declared = map fromPyproject (pyproject.project.dependencies or [ ]);
        declaredDev = map fromPyproject (filter isString (devGroup pyproject));
      in
      (compare "dependencies" declared recorded)
      ++ (compare "dependency group 'dev'" declaredDev recordedDev)
      ++ (map (_: "dependency group 'dev' uses include-group, which the Nix shell does not support")
        (filter isAttrs (devGroup pyproject)));

  # Locked git packages (other than the project) that mojoxBuiltDeps does not list.
  gitDepsMissing = { lock, projectName, mojoxBuiltDeps }:
    let
      built = map normalize mojoxBuiltDeps;
      gitNames = map (p: normalize p.name)
        (filter (p: (p.source or { }) ? git && normalize p.name != normalize projectName) (lock.package or [ ]));
    in
    unique (filter (n: !(elem n built)) gitNames);

  # path / editable / workspace / directory sources, which cannot resolve inside the store copy.
  unsupportedSources = { pyproject, lock, projectName }:
    let
      declared = ((pyproject.tool or { }).uv or { }).sources or { };
      bad = filter (n: let v = declared.${n}; in isAttrs v && (v ? path || v ? editable || v ? workspace))
        (builtins.attrNames declared);
      locked = filter
        (p: normalize p.name != normalize projectName
          && (let s = p.source or { }; in s ? editable || s ? directory || s ? path || s ? virtual))
        (lock.package or [ ]);
    in
    unique ((map normalize bad) ++ (map (p: normalize p.name) locked));

  # resolveBuildSystem needs these in the lock when anything is built with mojox-build.
  missingBuildSystemPackages = { lock, mojoxBuiltDeps }:
    let have = map (p: normalize p.name) (lock.package or [ ]); in
    if mojoxBuiltDeps == [ ] then [ ]
    else filter (n: !(elem n have)) [ "mojox-build" "mojox-core" "mojo-compiler" "mojo-compiler-mojo-libs" ];

  unknownBuiltDeps = { lock, mojoxBuiltDeps }:
    let have = map (p: normalize p.name) (lock.package or [ ]); in
    filter (n: !(elem (normalize n) have)) mojoxBuiltDeps;

  # uv2nix aborts on any other schema `version` with an opaque type error, so
  # this is checked first. `revision` is not checked: uv bumps it for
  # backwards-compatible additions.
  supportedSchemaVersion = 1;

  # Every problem, as a message that names the fix.
  problems = { pyproject, lock, projectName, mojoxBuiltDeps }:
    if (lock.version or null) != supportedSchemaVersion then
      [ "uv.lock schema version ${toString (lock.version or "missing")} is not supported (this mojox flake reads version ${toString supportedSchemaVersion}). Re-lock with a compatible uv or update the mojox flake." ]
    else
    (map (r: "uv.lock is out of sync with pyproject.toml: ${r}. Run `uv lock`, `git add uv.lock`, then re-enter the shell.")
      (staleReasons { inherit pyproject lock projectName; }))
    ++ (map (n: "uv.lock has the git-sourced package '${n}' but mojoxBuiltDeps does not list it. Add \"${n}\" to mojoxBuiltDeps in flake.nix.")
      (gitDepsMissing { inherit lock projectName mojoxBuiltDeps; }))
    ++ (map (n: "'${n}' is a path, editable, workspace or directory source. The Nix shell supports git and registry sources only.")
      (unsupportedSources { inherit pyproject lock projectName; }))
    ++ (map (n: "mojoxBuiltDeps is non-empty but uv.lock has no '${n}'. Add mojox-build, mojox-core and mojox>=0.7 to the dev group, then run `uv lock`.")
      (missingBuildSystemPackages { inherit lock mojoxBuiltDeps; }))
    ++ (map (n: "mojoxBuiltDeps lists '${n}', which is not in uv.lock.")
      (unknownBuiltDeps { inherit lock mojoxBuiltDeps; }));
}
