# Vendored Agent Control Specification distribution

Two install artifacts pulled from the
[`responsibleai/AgentControlSpecification`](https://github.com/responsibleai/AgentControlSpecification)
preview release at version `0.3.1b0` so this demo's "reproduce yourself"
path works without the user cloning the ACS repo.

| File | Use it on |
|---|---|
| `agent_control_specification-0.3.1b0-cp311-abi3-manylinux_2_39_x86_64.whl` | Linux x86_64 with glibc ≥ 2.39 (Ubuntu 24.04+, Debian 13+). Instant install. |
| `agent_control_specification-0.3.1b0.tar.gz` | Everything else (macOS arm64/x86_64, Windows x64, older Linux). Pip will build from source. The build script auto-bootstraps `rustup` + a Rust toolchain if cargo is missing, but a **system C linker is still required** (MSVC `link.exe` on Windows; `clang` from Xcode CLT on macOS). Build takes ~90 s on a recent laptop. |

## One install line that handles every platform

```bash
python -m pip install --find-links examples/bank_manager_agent_control/vendor/acs/ \
    agent-control-specification==0.3.1b0
```

`pip` picks the prebuilt wheel when the platform tags match, otherwise
falls back to building from the sdist.

## Refreshing these files

To pull a newer ACS release, replace both files with the corresponding
`python-release-artifacts` bundle from the
[ACS GitHub Releases page](https://github.com/responsibleai/AgentControlSpecification/releases)
and update the version pin in this README plus the parent
[`README.md`](../../README.md).
