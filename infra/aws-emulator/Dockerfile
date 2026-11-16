# AWS Emulator — Apple Silicon (aarch64) build
# Based on the official getting-started-docker recipe.
# See: https://docs.aws.amazon.com/emulator/latest/userguide/getting-started-docker.html
#
# IMPORTANT: This image is for local development ONLY.
# Do NOT publish or use with real AWS credentials.
# The emulator endpoint is bound to 127.0.0.1:9297 only (see docker-compose or run script).
#
# Emulator state is in-memory and lost on container stop.
# Generated credentials/CA cert are mounted from the host.

# TODO (IF-010): Replace this placeholder once the emulator package
# release and official Dockerfile recipe are confirmed.
# See docs/architecture-review.md §2.8 — AWS Emulator is P1.

FROM public.ecr.aws/amazonlinux/amazonlinux:2023

LABEL org.opencontainers.image.title="IncidentForge AWS Emulator"
LABEL org.opencontainers.image.description="Local AWS Emulator for IncidentForge — aarch64 only"

# Package URL populated at IF-010 once the official aarch64 package URL is verified.
ARG EMULATOR_PACKAGE_URL=""

RUN echo "AWS Emulator Dockerfile — placeholder for IF-010" && \
    echo "Package URL: ${EMULATOR_PACKAGE_URL}" && \
    echo "Complete this Dockerfile per official AWS documentation before use."

# Expected final shape:
# EXPOSE 9297
# ENTRYPOINT ["/usr/local/bin/aws-emulator", "--port", "9297"]
