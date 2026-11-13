# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0

{
  description = "UpdateHub CoAPS/DTLS end-to-end harness for Zephyr";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    zephyr.url = "github:zephyrproject-rtos/zephyr/c4647e2491ff956f312fe6935ef3fe98623a12eb";
    zephyr.flake = false;
    zephyr-nix = {
      url = "github:nix-community/zephyr-nix/5ef0903c5fcdbbce55af2732fad1011e90b6ff47";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.zephyr.follows = "zephyr";
    };
  };

  outputs =
    { nixpkgs, zephyr-nix, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      zephyr = zephyr-nix.lib.mkZephyr { inherit pkgs; };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        # native_sim builds at -O0; Nix's fortify hardening would turn that into an error
        hardeningDisable = [
          "fortify"
          "fortify3"
        ];
        packages = [
          (zephyr.sdks."1_0".sdk.override {
            targets = [
              "x86_64-zephyr-elf"
              "arm-zephyr-eabi"
            ];
          })
          # only what a Zephyr build, west and imgtool-signed images need
          (pkgs.python3.withPackages (
            ps: with ps; [
              west
              pyelftools
              pyyaml
              pykwalify
              jsonschema
              packaging
              psutil
              pyserial
              requests
              semver
              tqdm
              anytree
              tabulate
              intelhex
              colorama
              ply
              # twister
              natsort
              junitparser
              python-dotenv
              pytest
            ]
          ))
          zephyr.hosttools
          pkgs.cmake
          pkgs.git
          pkgs.gperf
          pkgs.ninja
          pkgs.go
          pkgs.openssl
          pkgs.mcuboot-imgtool
        ];
      };
    };
}
