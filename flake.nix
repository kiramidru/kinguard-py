{
  description = "Python Flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/fbcf476f790d8a217c3eab4e12033dc4";
    flake-utils.url = "github:numtide/flake-utils/11707dc2f618dd54ca8739b309ec4fc024de578b";
  };

  outputs =
    {
      nixpkgs,
      flake-utils,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            (python3.withPackages (
              pythonPackages: with pythonPackages; [
                pip
              ]
            ))
          ];
          shellHook = ''
            export LD_LIBRARY_PATH="${
              pkgs.lib.makeLibraryPath [
                pkgs.stdenv.cc.cc.lib
                pkgs.zlib
                pkgs.glib
                pkgs.xorg.libxcb
                pkgs.libGL
                pkgs.libglvnd
              ]
            }:$LD_LIBRARY_PATH"
          '';
        };
      }
    );
}
