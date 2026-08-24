{
  description = "HiveMind — autonomous AI agent orchestration platform";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        version = builtins.readFile ./.version;
        src = ./.;

        buildGoModule = pkgs.buildGoModule;
        vendorHash = null;

        orchestrator = buildGoModule {
          pname = "hivemind-orchestrator";
          inherit version src vendorHash;
          subPackages = [ "cmd/orchestrator" ];
          ldflags = [ "-s" "-w" ];
          CGO_ENABLED = 0;
        };

        dockerImage = pkgs.dockerTools.buildImage {
          name = "hivemind-orchestrator";
          tag = version;
          contents = [ orchestrator pkgs.cacert ];
          config = {
            Entrypoint = [ "${orchestrator}/bin/orchestrator" ];
            Cmd = [ "-serve" ];
            ExposedPorts = { "8080/tcp" = {}; };
            User = "1000:1000";
          };
        };
      in
      {
        packages = {
          inherit orchestrator dockerImage;
          default = orchestrator;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            go_1_23
            just
            kubectl
            kind
            docker
            golangci-lint
            jq
            yq-go
            postgresql
            redis
          ];
        };
      });
}