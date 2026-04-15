{
  description = "Nix Flake for development";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem
      (system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };

          buildInputs = with pkgs; [
            python312
            uv
            nodejs_22
            gcc.cc.lib
          ];

        in
        with pkgs;
        {
          devShells.default = mkShell {
            inherit buildInputs;
            shellHook = ''
              export UV_PYTHON_DOWNLOADS=never
              export UV_PYTHON=${pkgs.python312}/bin/python
              export LD_LIBRARY_PATH=${pkgs.gcc.cc.lib}/lib:$LD_LIBRARY_PATH
              uv sync
            '';
          };
        }
      );
}
