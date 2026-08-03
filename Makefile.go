.PHONY: build run test lint migrate clean docker-build docker-push

BINARY=orchestrator
GO=go
IMAGE=hivemind-orchestrator-go
VERSION=$(shell cat .version 2>/dev/null || echo "0.1.0")

build:
	$(GO) build -ldflags="-s -w" -o $(BINARY) ./cmd/orchestrator

run: build
	./$(BINARY) -serve

test:
	$(GO) test ./... -v -count=1

lint:
	$(GO) vet ./...
	golangci-lint run ./...

migrate:
	$(GO) run ./cmd/orchestrator -migrate

clean:
	rm -f $(BINARY)

docker-build:
	docker build -f Dockerfile.go -t $(IMAGE):$(VERSION) .
	docker tag $(IMAGE):$(VERSION) $(IMAGE):latest

docker-push:
	docker push $(IMAGE):$(VERSION)
	docker push $(IMAGE):latest

tidy:
	$(GO) mod tidy

generate:
	$(GO) generate ./...

.DEFAULT_GOAL := build