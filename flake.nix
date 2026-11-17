{
  description = "Shared Nix development shell for the Mojo projects";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      mojoxVersion = (builtins.fromTOML (builtins.readFile ./packages/mojox/pyproject.toml)).project.version;
    in
    {
      lib.mkDevShell = import ./nix/mk-dev-shell.nix {
        inherit pkgs pyproject-nix uv2nix pyproject-build-systems mojoxVersion;
      };
      devShells.${system}.default = pkgs.mkShell { packages = import ./nix/packages.nix pkgs; };
      checks.${system} = import ./nix/checks.nix { inherit pkgs pyproject-nix; };
    };
}
