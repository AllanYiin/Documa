#!/bin/bash
# Run inside the matching official PyPA manylinux_2_28 container.
# /source is an extracted release sdist, /output is a writable artifact directory.
set -euo pipefail
export PATH="/root/.cargo/bin:$PATH"
export DOCUMA_TOKEN_COUNTER=tiktoken:o200k_base
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"
export PIP_DEFAULT_TIMEOUT=180
export CARGO_NET_RETRY=5
export CARGO_HTTP_TIMEOUT=180
mkdir -p /output
uname -a > /output/build-platform.txt
if ! command -v cargo >/dev/null; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/documa-rustup.sh
  sh /tmp/documa-rustup.sh -y --profile minimal --default-toolchain stable
fi
rustc --version >> /output/build-platform.txt
cd /source
for abi in ${DOCUMA_PYTHON_ABIS:-cp310 cp311 cp312}; do
  py="/opt/python/$abi-$abi/bin/python"
  existing=(/output/documa-*"$abi-$abi"*.whl)
  if [ "${DOCUMA_RESUME:-0}" != 1 ] || [ ! -f "${existing[0]}" ]; then
    "$py" -m pip wheel --no-deps --wheel-dir "/tmp/wheels-$abi" . > "/output/$abi-build.log" 2>&1
    auditwheel repair --plat "manylinux_2_28_$(uname -m)" --wheel-dir /output /tmp/wheels-"$abi"/*.whl > "/output/$abi-auditwheel.log" 2>&1
  fi
  "$py" -m venv "/tmp/test-$abi"
  testpy="/tmp/test-$abi/bin/python"
  "$testpy" -m pip install /output/documa-*"$abi-$abi"*.whl > "/output/$abi-install.log" 2>&1
  "$testpy" -I /checks/smoke_installed_release.py > "/output/$abi-smoke.json"
  "$testpy" -m pip check > "/output/$abi-pip-check.txt"
done
(cd /output && sha256sum ./*.whl > SHA256SUMS.txt)
