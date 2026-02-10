variable "REGISTRY" { default = "docker.io" }
variable "NAMESPACE"  { default = "binarycodes" }
variable "IMAGE_NAME" { default = "github-runner" }

variable "GH_RUNNER_VERSION" { default = "2.331.0" }

group "default" {
  targets = ["image"]
}

target "image" {
  context    = "."
  dockerfile = "Dockerfile"

  args = {
    RUNNER_VERSION = GH_RUNNER_VERSION
  }

  labels = {
    "org.opencontainers.image.title" = "homelab-github-runner"
    "org.opencontainers.image.description" = "Ephemeral GitHub Actions runner used in homelab"
    "org.opencontainers.image.version" = "${GH_RUNNER_VERSION}"
  }

  tags = [
    "${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:${GH_RUNNER_VERSION}",
    "${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:latest",
  ]

  platforms = ["linux/amd64"]
}

target "image-all" {
  inherits = ["image"]
  platforms = ["linux/amd64", "linux/arm64"]
}
