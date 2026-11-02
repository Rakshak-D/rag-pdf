# Phase 6.14C — effdet Configuration Runtime Compatibility

**Review date:** 2026-11-02<br>
**Starting revision:** `8638a8d4ecee0837a4dc64af8c59a79b17d368ea`<br>
**Method:** temporary package overlay over the existing `rag-env` interpreter<br>
**Classification:** **C — Inconclusive.**<br>
**Runtime attempts:** baseline 1/1; candidate 1/1; retries 0.

This comparison did not reach an `effdet` configuration entry point. The baseline failed while importing OmegaConf because the existing OmegaConf/ANTLR pins conflict. The candidate correctly imported OmegaConf 2.4.0 from the temporary overlay, but the diagnostic network guard blocked Windows asyncio's local socketpair during the separate async-timeout check. Both child processes were reaped. No additional runtime attempt was made because the authorized budget was exhausted.

## Repository and dependency boundaries

Phase 6.14B was committed and pushed before this study:

- Commit: `8638a8d4ecee0837a4dc64af8c59a79b17d368ea`
- Parent: `49506f9628a8a91529d4e13e545fd03af30f5961`
- Changed file: `docs/audit/dependency-manifest-feasibility-614b.md`

No dependency file, production module, research file, installed project package, or Chroma index was changed. Candidate wheels were extracted into a task-owned temporary overlay; no pip install was performed.

The only known isolated venv from Phase 6.14A was its four-wheel subset. It had no `effdet` distribution metadata, so it could not provide the required dependent-package runtime. The comparison therefore used separate child processes launched by the existing `rag-env` CPython 3.10.11 interpreter. The candidate child received a `PYTHONPATH` overlay containing the two previously verified pure-Python wheels. The candidate child confirmed these imports and metadata paths before failing:

| Distribution | Version | Candidate module source |
|---|---:|---|
| OmegaConf | 2.4.0 | Temporary `candidate-overlay/omegaconf/` |
| async-timeout | 4.0.3 | Temporary `candidate-overlay/async_timeout/` |

The wheel SHA-256 values were rechecked before extraction against the Phase 6.14A manifest:

| Wheel | Size | SHA-256 |
|---|---:|---|
| `async_timeout-4.0.3-py3-none-any.whl` | 5,721 B | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` |
| `omegaconf-2.4.0-py3-none-any.whl` | 254,152 B | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` |

The baseline child used the installed `omegaconf==2.3.0` and `async-timeout==5.0.1`. Other relevant installed versions were `effdet==0.4.1`, `antlr4-python3-runtime==4.13.2`, `torch==2.5.1+cu121`, `torchvision==0.20.1+cu121`, `timm==1.0.24`, and `pycocotools==2.0.11`. These versions were read from installed distribution metadata. The candidate overlay did not replace `effdet` or any of those dependencies.

## Installed effdet source inspection

The inspected distribution was the actual installed `effdet==0.4.1` at `rag-env/Lib/site-packages/effdet`, with metadata in `rag-env/Lib/site-packages/effdet-0.4.1.dist-info`. The `RECORD` hashes matched the source bytes for the inspected files. Selected SHA-256 values:

| Installed source | SHA-256 |
|---|---|
| `effdet/__init__.py` | `31180f4ac768c35b8f3fb5a2a4e2c8d807140a2fa59f6923ef9915551751c934` |
| `effdet/config/fpn_config.py` | `7a09bb53b3b2366fa85705a1eb147c07e46ca6e7d0efbb562b3bcc34933464a4` |
| `effdet/config/model_config.py` | `3ef4d96ebd23cf2e59a3fa819910a50a6f47f189587c0d0cd94c267556a41f3e` |
| `effdet/config/train_config.py` | `701dd59ff018c7bbbc96d6755464b1a29f040be6db51f3ca9d190a828f81757c` |
| `effdet/data/input_config.py` | `6f82bea6a12ecb394740707b030468bef81312c933482d3674f7d427981e2358` |

The distribution requires Torch, torchvision, timm, pycocotools, and OmegaConf. Its package root imports EfficientDet, benches, data, evaluator, factory, and helper modules, which in turn load the installed model-stack libraries. No model constructor was called. The inspected helpers contain checkpoint/URL loading functions, but those functions were not invoked.

The configuration operations selected before the attempts were:

- `default_detection_model_configs()` and `default_detection_train_config()`.
- `get_efficientdet_config()` for `efficientdet_d0`, `cspresdext50pan`, and `efficientdet_q0`, covering source-defined BiFPN-default, PAN, and QU-FPN configuration names.
- `get_fpn_config()` for the model-selected FPN names. The installed function constructs BiFPN, PAN, and QU-FPN variants before selecting its return value; the builders append nodes to an OmegaConf config.
- `resolve_input_config()` and `get_feat_sizes()`, which consume image-size sequences and convert the configured image dimensions to a tuple.
- The installed `set_config_readonly()` and `set_config_writeable()` helpers.
- YAML round-trip and FPN `inputs_offsets` ordering/reference checks.

Source inspection found tuple-valued `image_size` and `aspect_ratios`, nested config values, `deepcopy`, and repeated `p.nodes.append({...})` operations. `effdet` converts image sizes and node offsets to tuples at consumption points; the inspected paths did not mutate those tuple values. No `OmegaConf.merge`, `OmegaConf.to_container`, or `OmegaConf.to_yaml` calls were found in the inspected config modules. Those operations were planned as observation/serialization checks, not claimed as effdet calls.

None of the planned model/FPN constructors, conversions, YAML checks, node ordering checks, or `effdet` imports were reached in either child. Consequently, there are no baseline/candidate normalized configuration outputs or type comparisons from this study.

## Bounded child outcomes

Each child was launched once with a 120-second bound, offline model/cache environment variables, bytecode writing disabled, and a temporary cache/output root. A child-local socket guard was intended to prevent external connections. Both processes exited normally with code 1 within one second and were reaped; neither timed out.

| Attempt | PID | OmegaConf state | Last stage reached | Outcome |
|---|---:|---|---|---|
| `614c-baseline-3de73aade9ee` | 26484 | Installed 2.3.0 import began | Import OmegaConf and async-timeout | Failed: `Could not deserialize ATN with version 3 (expected 4).` |
| `614c-candidate-3de73aade9ee` | 8364 | 2.4.0 imported from overlay; 4.0.3 async-timeout imported from overlay | Async-timeout asyncio check | Failed: guard raised `NetworkDisabled` for `('127.0.0.1', 52302)` while Windows `asyncio` created its local Proactor event-loop socketpair |

### Baseline attribution

The baseline traceback enters OmegaConf 2.3.0's generated grammar parser and fails in the installed ANTLR runtime deserializer. The environment contains `antlr4-python3-runtime==4.13.2`, while OmegaConf 2.3.0 declares `antlr4-python3-runtime==4.9.*`. This is the already-known baseline metadata conflict, observed here as an import failure. It is not evidence of an `effdet` failure or of OmegaConf 2.4.0 incompatibility.

### Candidate and instrumentation attribution

The candidate wheel identity and shadowing were established: `omegaconf` resolved to the temporary 2.4.0 overlay, and `async_timeout` resolved to the temporary 4.0.3 overlay. OmegaConf import itself succeeded. The check then called `asyncio.run`; on Windows, event-loop initialization uses a loopback socketpair. The guard treated that local socket connection as prohibited network traffic and stopped the attempt before any `effdet` import. This is an instrumentation defect, not a candidate package failure. The stderr also recorded the event loop's cleanup warning after the blocked setup.

In both children, lightweight imports of `aiohttp==3.13.3` and `langchain_classic==1.0.1` completed. This only demonstrates those top-level imports in the two partial processes; the baseline async-timeout operation did not run, and the candidate asyncio timeout operation did not complete. No external network connection, model download, inference, or Chroma operation occurred.

## Retained evidence and classification

Task-owned evidence root:

```text
%LOCALAPPDATA%\Temp\rag-pdf-dependency-614c-b324ff045ec94c1dbaf63de73aade9ee\
```

It contains the overlay wheels' extracted package files, the exact child and orchestration scripts, `comparison-result.json`, and separate `baseline/` and `candidate/` directories with child JSON, stdout, and stderr. The JSON files retain exception details and tracebacks. No experiment output was written into the repository.

**Classification C — Inconclusive.** The baseline could not import its pinned OmegaConf with the environment's conflicting ANTLR runtime, and the candidate did not reach effdet because the network guard blocked a local asyncio socketpair. The comparison provides no evidence for or against effdet 0.4.1 configuration compatibility with OmegaConf 2.4.0.

The two-pin candidate remains metadata-consistent per [Phase 6.14B](dependency-manifest-feasibility-614b.md), but its actual effdet runtime contract remains unverified. A future separately authorized comparison would need a baseline with a verified compatible OmegaConf 2.3/ANTLR 4.9.* combination and a network policy that permits local loopback required by asyncio while preventing external connections. If a compatible baseline artifact is available only as an sdist, no source build should be performed without separate approval. Then run the same bounded config paths once per version, preserving the current project environment.

No dependency adoption is approved. The project remains on Chroma 1.5.0; HNSW reliability and the natural artifact-publisher incident remain unresolved. This configuration-only study does not qualify inference, training, the full application, security, or deployment. Unattended deployment remains **NO-GO**.
