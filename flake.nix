{
  description = "github-status";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "nixpkgs/nixos-unstable";
  };

  outputs = { self, flake-utils, nixpkgs }:
    flake-utils.lib.eachSystem [ "x86_64-linux" ] (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ ];
        };

        github-status =
          let
            python-env = pkgs.python3.withPackages (ps: [
              ps.babel
              ps.dateutil
              ps.pyyaml
              ps.requests
              ps.rich
            ]);
          in
          pkgs.writeShellApplication {
            name = "github-status";
            runtimeInputs = [ python-env pkgs.gh ];
            text = ''
              python3 ${./github-status.py} "$@"
            '';
          };
      in
      {
        packages = {
          inherit github-status;
          default = github-status;
        };
      }
    );
}
