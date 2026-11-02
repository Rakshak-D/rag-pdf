# Phase 6.14D — Bounded Candidate Runtime Confirmation

**Review date:** 2026-11-02<br>
**Phase 6.14D execution revision:** `82ea8df647f01ca9b2e6feac532c70089b9cbd2a`<br>
**Phase 6.14C report checkpoint:** `82ea8df647f01ca9b2e6feac532c70089b9cbd2a` (parent `8638a8d4ecee0837a4dc64af8c59a79b17d368ea`)<br>
**Method:** one candidate-only runtime execution using a temporary package overlay over the existing project interpreter<br>
**Classification:** **A — Bounded candidate configuration contract passed.**

This result is limited to the installed `effdet==0.4.1` configuration operations listed below under the candidate OmegaConf 2.4.0 overlay. It does not establish version-to-version equivalence, a clean installation, full application compatibility, model inference compatibility, security qualification, or deployment readiness.

## Stage A checkpoint

The Phase 6.14C report was reviewed and committed before this candidate study. Commit `82ea8df647f01ca9b2e6feac532c70089b9cbd2a` has parent `8638a8d4ecee0837a4dc64af8c59a79b17d368ea` and changes only [the Phase 6.14C runtime-compatibility report](dependency-runtime-compatibility-614c.md). The feature branch was pushed to `origin/production-evaluation-workbench`; the remote feature head matched the commit. Protected main and the research fixture were unchanged.

## Original Phase 6.14C results remain inconclusive

The original attempts were not repeated. Their classifications remain **C — Inconclusive**:

| Original attempt | Outcome | Configuration entry point reached |
|---|---|---|
| OmegaConf 2.3.0 baseline | Import failed with `Could not deserialize ATN with version 3 (expected 4)` under installed `antlr4-python3-runtime==4.13.2`. This matches the known OmegaConf 2.3/ANTLR metadata conflict. | None |
| OmegaConf 2.4.0 candidate overlay | Candidate OmegaConf and async-timeout modules loaded from the overlay. The old guard rejected a Windows loopback connection during `asyncio.run()`. | None |

The retained candidate traceback shows the Windows Proactor loop initializing its self-pipe through `socket.socketpair()`. That implementation connected to `127.0.0.1`; the old guard rejected every `socket.connect()` regardless of destination. The error originated in that local asyncio setup, not OmegaConf or effdet. Both original child processes were reaped. Evidence remains under `%LOCALAPPDATA%\Temp\rag-pdf-dependency-614c-b324ff045ec94c1dbaf63de73aade9ee`; it was neither overwritten nor reused. The original account is preserved in the [Phase 6.14C report](dependency-runtime-compatibility-614c.md).

The baseline was not rerun: its declared OmegaConf/ANTLR pair is already known to be incompatible, and repairing it would change the comparison and exceed this task's authorization. No baseline-versus-candidate semantic parity is claimed.

## Network-guard preflight

The replacement guard was exercised in a separate standard-library-only child before the candidate attempt. It permits AF_UNIX and IP loopback destinations, including IPv4-mapped IPv6 loopback, and rejects unresolved hostnames and non-loopback IP destinations before calling the underlying resolver or connection function. The guard was installed only inside its child process; no host networking or firewall setting changed.

Preflight evidence records:

- `asyncio.run()` completed a no-network coroutine.
- Windows `socket.socketpair()` succeeded. Its ends exchanged the bounded payload `614d-loopback`, then both sockets closed.
- The guard accepted IPv4 and IPv6 loopback and rejected test-only IPv4/IPv6 external addresses and `example.invalid` without DNS resolution.
- Synthetic `getaddrinfo` and `create_connection` probes were blocked before a real lookup or connection. Real external requests: **0**.
- The child reported exit code 0; the runner completed its Popen wait and marked that process reaped. The interpreter-reported PID and Popen PID were recorded separately; a post-run process query found neither active. No exception was recorded.

The preflight parent recorded two allowed loopback connects and two blocked synthetic probes. The candidate child later recorded one allowed loopback connection and no blocked external attempt. This is a diagnostic guard for the Python socket interfaces exercised by these checks, not an OS-level sandbox against arbitrary native code. The tested configuration paths did not request external resources; model and dataset downloads were disabled through offline environment settings.

## Candidate overlay and package identity

The study used the CPython 3.10.11 interpreter in the existing `rag-env` as a read-only base for installed effdet and model-stack dependencies. This is **overlay compatibility testing**, not a clean virtual-environment installation or a full dependency resolution. No package was installed into `rag-env`.

The runner rehashed each wheel against both recorded Phase 6.14A digests before extraction, checked ZIP integrity and member paths, and extracted into a new temporary overlay:

| Candidate wheel | Size | SHA-256 |
|---|---:|---|
| `async_timeout-4.0.3-py3-none-any.whl` | 5,721 bytes | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` |
| `omegaconf-2.4.0-py3-none-any.whl` | 254,152 bytes | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` |

These are the previously verified official artifacts recorded in [Phase 6.14A](dependency-consistency-614a.md). Phase 6.14D did not download new artifacts or run an installer. Wheel identity verification is not a security audit.

The child disabled user-site loading and bytecode writes, set the overlay ahead of installed packages, removed inherited `PYTHONPATH`/`PYTHONHOME`, and confirmed OmegaConf was not already in `sys.modules`. Runtime identities and module paths were:

| Distribution | Version | Loaded from |
|---|---:|---|
| OmegaConf | 2.4.0 | Task-owned candidate overlay |
| async-timeout | 4.0.3 | Task-owned candidate overlay |
| effdet | 0.4.1 | Existing `rag-env` site-packages |
| torch / torchvision | 2.5.1+cu121 / 0.20.1+cu121 | Existing `rag-env` site-packages |
| timm / pycocotools | 1.0.24 / 2.0.11 | Existing `rag-env` site-packages |

The installed effdet source path was checked against the expected package directory. SHA-256 values for all nine exercised/relevant effdet source files matched its installed `RECORD`. The installed source and dependencies were not changed. `aiohttp==3.13.3` and `langchain_classic==1.0.1` top-level imports also completed. The run exercised an `async-timeout` 4.0.3 context inside `asyncio.run()`; it did not exercise broader aiohttp or LangChain network/client behavior.

## One candidate attempt

| Attempt | Interpreter-reported PID | Popen-reported PID | Result | Duration |
|---|---:|---:|---|---:|
| `614d-effdet-candidate-8543d6bcf953` | 32004 | 20156 | Exit 0; core status `passed`; no exception; no timeout | 9.5 seconds |

The child used CPython 3.10.11, Windows AMD64. The interpreter's `os.getpid()` and the runner's `Popen.pid` differ; the evidence records them separately and does not infer their ancestry. The runner's `communicate()` completed and marked its Popen process reaped; a post-run process query found neither recorded PID alive. The preflight similarly recorded interpreter PID 32664 and Popen PID 15092, with normal completion and no remaining process at the later query. That later query is an observation at one point in time, not a general process-lifecycle guarantee. The candidate-only budget was consumed as authorized: baseline attempts **0**, candidate attempts **1**, candidate retries **0**. No Chroma, user data, model weights, inference, training, or external service was used.

## effdet configuration operations reached

The child imported the actual installed effdet package and verified its `RECORD`, then executed:

- `default_detection_model_configs()` and `default_detection_train_config()`.
- `get_efficientdet_config()` for `efficientdet_d0`, `cspresdext50pan`, and `efficientdet_q0`.
- `get_fpn_config()` for `bifpn_fa`, `pan_fa`, and `qufpn_fa`, selected from those model configs. The actual FPN builders' nested `nodes.append()` operations ran.
- Within each model summary, `resolve_input_config()` and `get_feat_sizes()` consumed the configured image dimensions.
- effdet's `set_config_readonly()` and `set_config_writeable()` helpers, deep-copy behavior, OmegaConf conversion, and deterministic YAML round-trips.
- A no-network async-timeout context, plus lightweight top-level imports of aiohttp and LangChain Classic.

No model instance, checkpoint, pretrained weight, CUDA allocation for inference, training loop, or dataset was created. Importing effdet loaded the installed Torch/model dependency modules but did not invoke model construction or download code.

### Model configuration observations

| Configuration | Resolved name | Image size | Backbone | OmegaConf types observed |
|---|---|---:|---|---|
| Default | `tf_efficientdet_d1` | 640 × 640 | `tf_efficientnet_b1` | root `DictConfig`; image size `TupleConfig`; aspect ratios `ListConfig`; first aspect entry `TupleConfig`; norm kwargs `DictConfig` |
| `efficientdet_d0` | `efficientdet_d0` | 512 × 512 | `efficientnet_b0` | root `DictConfig`; image size `TupleConfig`; aspect ratios `ListConfig`; first entry `TupleConfig`; backbone args and norm kwargs `DictConfig` |
| `cspresdext50pan` | `cspresdext50pan` | 640 × 640 | `cspresnext50` | root `DictConfig`; image size `TupleConfig`; aspect ratios `ListConfig` with scalar float entries; backbone args and norm kwargs `DictConfig` |
| `efficientdet_q0` | `efficientdet_q0` | 512 × 512 | `efficientnet_b0` | root `DictConfig`; image size `TupleConfig`; aspect ratios `ListConfig`; first entry `TupleConfig`; backbone args and norm kwargs `DictConfig` |

All four image-size values were positive two-element sequences. Indexing, iteration, tuple conversion, `resolve_input_config()`, and feature-size derivation returned valid results. For 512-pixel configs, derived square feature sizes were 512, 256, 128, 64, 32, 16, 8, and 4; for 640-pixel configs they were 640, 320, 160, 80, 40, 20, 10, and 5. Aspect-ratio entries were present, finite, and positive in the retained normalized output.

The read-only helper changed the observable state from unset (`None`) to read-only (`True`), then back to writable (`False`). Deep-copy outputs retained normalized values. Repeated YAML serialization was byte-stable per object, and reparsed YAML preserved normalized values. No tuple mutation was attempted: the inspected effdet consumers read, iterate, index, or convert these tuple-valued fields rather than mutating them. The run does not cover all OmegaConf APIs or every effdet configuration consumer.

### FPN structure

Each builder returned a non-empty `DictConfig` with `nodes` as `ListConfig`, node entries as `DictConfig`, and `inputs_offsets` as `ListConfig`. Every node's offsets were non-empty and pointed either to an original feature level or an earlier generated node. The independent post-run evidence audit also confirmed each `feat_level` was within the selected model's configured min/max range.

Ordered nodes are represented as `(feat_level: inputs_offsets)`:

- `efficientdet_d0` / `bifpn_fa` — 8 nodes: `(6:3,4)`, `(5:2,5)`, `(4:1,6)`, `(3:0,7)`, `(4:1,7,8)`, `(5:2,6,9)`, `(6:3,5,10)`, `(7:4,11)`.
- `cspresdext50pan` / `pan_fa` — 10 nodes: `(7:4)`, `(6:3,5)`, `(5:2,6)`, `(4:1,7)`, `(3:0,8)`, `(3:9)`, `(4:8,10)`, `(5:7,11)`, `(6:6,12)`, `(7:5,13)`.
- `efficientdet_q0` / `qufpn_fa` — 21 nodes: `(6:3,4)`, `(5:2,5)`, `(4:1,6)`, `(3:0,7)`, `(4:1,7,8)`, `(5:2,6,9)`, `(6:3,5,10)`, `(7:4,11)`, `(4:1,0)`, `(5:2,13)`, `(6:3,14)`, `(7:4,15)`, `(6:3,15,16)`, `(5:2,14,17)`, `(4:1,13,18)`, `(3:0,19)`, `(3:8,20)`, `(4:9,19)`, `(5:10,18)`, `(6:11,17)`, `(7:12,16)`.

All three FPN YAML outputs serialized deterministically and round-tripped to the same normalized values. The candidate result's normalized configuration digest is `cf2756d163c4240fb6353f6df5004dd1c596a39c0ed8e992b16b0ab3e435b05c`. This is an evidence identity for this candidate output, not a cross-version comparison or expected baseline digest.

## Evidence and limitations

Task-owned evidence is retained outside the repository at:

```text
%LOCALAPPDATA%\Temp\rag-pdf-dependency-614d-6b0625f369754539a95a8543d6bcf953\
```

It includes the guard and child scripts, preflight result/stdout/stderr, candidate parent and child JSON, and candidate stdout/stderr. The child JSON records the original exception and traceback if one occurs; none occurred in this candidate attempt. The temporary workspace is separate from the earlier Phase 6.14C evidence root.

The result supports bounded configuration execution with the proposed OmegaConf 2.4.0 and async-timeout 4.0.3 overlay against the installed effdet source. It does **not** prove:

- semantic equivalence with OmegaConf 2.3.0 (the incompatible baseline was intentionally not run);
- complete dependency resolution, clean installation, `pip check` for the full project, or reproducible package-index configuration;
- all effdet code paths, model construction, training, inference, pretrained-weight loading, or application runtime compatibility;
- security qualification or adoption of either candidate pin.

The project remains pinned to `omegaconf==2.3.0`, `async-timeout==5.0.1`, `antlr4-python3-runtime==4.13.2`, and `chromadb==1.5.0`. No dependency file, production module, or research artifact was changed. The metadata feasibility discussion remains in [Phase 6.14B](dependency-manifest-feasibility-614b.md); this report does not authorize its proposed pins.

The historical Chroma HNSW reliability issue and natural artifact-publisher integrity incident remain unresolved. No migration, residual-risk acceptance, production deployment, or unattended use is approved. **Unattended deployment remains NO-GO.**
