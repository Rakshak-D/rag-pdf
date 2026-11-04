# Phase 6.17I — Corrected MSVC/SDK activation verification

**Date:** 2026-11-04
**Classification:** `MSVC_SDK_ENVIRONMENT_VERIFIED`

## Checkpoint and scope

The run began on `production-evaluation-workbench` at `0d44e163d7ca81abd447b4ebb0d167adebdfa567`. The remote feature branch matched. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`, and the protected research fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`.

The only pre-existing worktree change was the unstaged two-pin `requirements.txt` proposal, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`. Its bytes remained unchanged and it was not staged.

This phase performed one corrected developer-environment activation in a disposable `cmd.exe` child. It did not provision tools, invoke Cargo, compile or link code, generate protobuf files, install dependencies, or run tests.

## Corrected activation

The selected installation was Visual Studio 2022 Build Tools. The single command was:

```cmd
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 10.0.26100.0 -vcvars_ver=14.44.35207
```

The SDK version was supplied as the positional Windows SDK version argument documented for `vcvarsall.bat`; this corrects the unsupported `-winsdk=` option used in Phase 6.17H. The command ran once in an external, task-owned temporary workspace with a sanitized child PATH and task-local `TEMP`/`TMP`.

Activation exit code: **0**. Child process exit code: **0**. Duration: **1.942 seconds**. The child did not time out and was no longer running after completion. Captured standard output was 1,938 bytes; standard error was empty. No second activation occurred.

## Selected environment

| Setting | Observed value | Result |
|---|---|---|
| `VCToolsVersion` | `14.44.35207` | Match |
| `VCToolsInstallDir` | VS 2022 Build Tools `VC\Tools\MSVC\14.44.35207\` | Match |
| `WindowsSdkDir` | `C:\Program Files (x86)\Windows Kits\10\` | Match |
| `WindowsSDKVersion` | `10.0.26100.0\` (normalized: `10.0.26100.0`) | Match |
| Host architecture | `x64` | Match |
| Target architecture | `x64` | Match |
| `Platform` | `x64` | Match |

All `where.exe` results resolved to exactly one expected location per tool:

| Tool | Resolved path | File version and metadata |
|---|---|---|
| `cl.exe` | `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\cl.exe` | `19.44.35222.0`, Microsoft C/C++ Compiler Driver |
| `link.exe` | `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\link.exe` | `14.44.35222.0`, Microsoft Incremental Linker |
| `lib.exe` | `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\lib.exe` | `14.44.35222.0`, Microsoft Linker Stub |
| `rc.exe` | `C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\rc.exe` | `10.0.26100.7705`, Microsoft Resource Compiler |

The active `INCLUDE` contained the selected MSVC include directory and SDK UCRT, UM, and shared include directories. `LIB` contained the selected MSVC x64 library directory and SDK UCRT/UM x64 library directories. The required `stdio.h`, `Windows.h`, `ucrt.lib`, and `kernel32.lib` files were present. The SDK x64 `rc.exe` resolved from the requested SDK.

No tool resolved from Visual Studio 18, Git, MSYS, or another toolchain. The checks inspected paths and file metadata only; `cl.exe`, `link.exe`, `lib.exe`, and `rc.exe` were not invoked.

## Preservation and evidence

The pre- and post-activation SHA-256 values matched for the selected `vcvarsall.bat`, MSVC tool executables, SDK resource compiler, SDK headers and libraries, isolated Rust 1.92.0 component executables, and `protoc.exe`. The default Rust 1.90.0 `rustc.exe` and `cargo.exe` hashes also matched. Persistent process, user, and system PATH hashes were unchanged.

The existing Python environment remained CPython 3.10.11 with Chroma 1.5.0, OmegaConf 2.3.0, async-timeout 5.0.1, antlr4-python3-runtime 4.13.2, and PyYAML 6.0.3. The research baseline verifier passed both before and after activation: **nine protected research files unchanged**.

Detailed logs and machine-readable snapshots are retained outside the repository under `%LOCALAPPDATA%\Temp\rag-pdf-617i-699ac83699ae48b7979f7f348ba64398`. The child stdout SHA-256 is `23C0066ADD89D28DB03920FBA667E5EF8B969D268E3A011B4846F0398A70AE2B`; stderr SHA-256 is `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`.

## Limitations and next decision

`MSVC_SDK_ENVIRONMENT_VERIFIED` means the requested VS 2022 MSVC/SDK pairing was selected in the isolated child and its paths and versions matched. It does **not** establish that Rust or Chroma compilation will succeed.

The Phase 6.17H Rustup toolchain installation command's exit code and continuous peak-resource record remain unavailable. This activation does not repair that historical evidence gap or upgrade 6.17H's `PARTIAL_PROVISIONING` result. Rust and protoc files were rechecked as present and unchanged.

Any Cargo dependency resolution, compilation, native wheel build, installation, or runtime diagnostic requires a separate authorization and resource review. No such action is requested or authorized here.

## Execution accounting

- `vcvarsall.bat` activations: **1**
- Cargo invocations: **0**
- Rust/C++ compilation attempts: **0**
- Toolchain installations: **0**
- Dependency downloads: **0**
- Protobuf generation: **0**
- Publisher/retrieval tests: **0**
- Production changes: **0**
