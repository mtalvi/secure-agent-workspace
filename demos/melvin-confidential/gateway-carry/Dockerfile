FROM registry.redhat.io/rust-builder-image/rust-rhel9:1.99.0@sha256:b00aaa63f7d6f5b857c170e4aed9d77f013f507ba97e72ebfcac48a7d3760310 AS builder
USER 0
ENV PATH=/opt/rh/gcc-toolset-15/root/usr/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/rh/gcc-toolset-15/root/usr/lib64:/opt/rh/gcc-toolset-15/root/usr/lib \
    CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse \
    RUSTUP_TOOLCHAIN=1.99.0 \
    OPENSHELL_GIT_VERSION=0.1.10-rhaiv.0
WORKDIR /build
RUN git init -q && git remote add origin https://github.com/opendatahub-io/openshell.git \
    && git fetch -q --depth 1 origin c2775b8df05619cdd57cb354ea8cd829ec79ee40 \
    && git checkout -q --detach FETCH_HEAD
COPY guest-annotations.patch /tmp/guest-annotations.patch
RUN git apply --check /tmp/guest-annotations.patch && git apply /tmp/guest-annotations.patch && rm -rf .git
RUN cargo build --locked --release --package openshell-gateway --no-default-features \
    --features defaults-without-telemetry,vendored-z3
FROM quay.io/opendatahub/odh-openshell-gateway@sha256:e4b89770ef55238cc45d9880614eda64b8ab9a76be5e6c81c63a350889eb0717
COPY --from=builder --chmod=0555 /build/target/release/openshell-gateway /usr/local/bin/openshell-gateway
LABEL io.openshell.saw.carry="melvin-demo-guest-annotations" \
    io.openshell.saw.carry.source="opendatahub-io/openshell c2775b8df05619cdd57cb354ea8cd829ec79ee40"
