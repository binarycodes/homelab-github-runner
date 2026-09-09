variable "REGISTRY" { default = "docker.io" }
variable "NAMESPACE"  { default = "binarycodes" }
variable "IMAGE_NAME" { default = "github-runner" }

variable "GH_RUNNER_VERSION" { default = "2.337.0" }
variable "GH_RUNNER_CHECKSUM_X64"   { default = "sha256:70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613" }
variable "GH_RUNNER_CHECKSUM_ARM64" { default = "sha256:9b1dc70626422526e3c94767cf024896beb15da5342a3f4819bf2feac13e0393" }

variable "LOCAL" { default = false }

group "default" {
  targets = ["image"]
}

target "image" {
  context    = "."
  dockerfile = "Dockerfile"

  args = {
    RUNNER_VERSION = GH_RUNNER_VERSION
    RUNNER_CHECKSUM_X64 = GH_RUNNER_CHECKSUM_X64
    RUNNER_CHECKSUM_ARM64 = GH_RUNNER_CHECKSUM_ARM64
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

  platforms = LOCAL ? [] : ["linux/amd64", "linux/arm64"]
}
