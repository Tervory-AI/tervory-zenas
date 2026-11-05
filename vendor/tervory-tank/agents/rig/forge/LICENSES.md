# Forge software and licences

The image retains upstream notices. Source links below identify corresponding
upstream source; the lock manifests identify the exact downloaded bytes. Blender
is invoked as a separate executable, never imported or linked into this service.

| Component | Pin | Licence / source |
| --- | --- | --- |
| Blender | 4.5.3 LTS | [GPL-3.0-or-later](https://projects.blender.org/blender/blender/src/tag/v4.5.3/COPYING); separate headless program |
| glTF Transform CLI | 4.2.1 | [MIT](https://github.com/donmccurdy/glTF-Transform/tree/v4.2.1) |
| gltfpack / meshoptimizer | 0.25 | [MIT](https://github.com/zeux/meshoptimizer/tree/v0.25) |
| KTX-Software / toktx | 4.4.2 | [Apache-2.0](https://github.com/KhronosGroup/KTX-Software/tree/v4.4.2); bundled Basis Universal notices retained |
| trimesh | 4.9.0 | [MIT](https://github.com/mikedh/trimesh/tree/4.9.0) |
| manifold3d | 3.2.1 | [Apache-2.0](https://github.com/elalish/manifold/tree/v3.2.1) |
| Khronos gltf-validator | 2.0.0-dev.3.10 | [Apache-2.0](https://github.com/KhronosGroup/glTF-Validator) |
| Meshroom / AliceVision | 2023.3.0 / 3.2.0 | [MPL-2.0](https://github.com/alicevision/Meshroom/tree/v2023.3.0); release's third-party notices retained |
| TRELLIS.2 | 75fbf0183001ed9876c8dbb35de6b68552ee08bd | [MIT](https://github.com/microsoft/TRELLIS.2/tree/75fbf0183001ed9876c8dbb35de6b68552ee08bd) |
| nvdiffrast / nvdiffrec | commits in gpu/sources.lock.json | **NVIDIA Source Code License: research or evaluation purposes only; commercial use unresolved.** See [nvdiffrast](https://github.com/NVlabs/nvdiffrast/blob/v0.4.0/LICENSE.txt) and retained notices |
| CuMesh, FlexGEMM, utils3d, O-Voxel | commits in gpu/sources.lock.json | Upstream MIT notices; Eigen is MPL-2.0; retained submodule licences |
| FlashAttention | 2.7.3 | [BSD-3-Clause](https://github.com/Dao-AILab/flash-attention/tree/v2.7.3) |
| CUDA / PyTorch GPU base | image digests in gpu/Dockerfile.* | NVIDIA CUDA EULA / PyTorch BSD-style; image licences retained |
| TRELLIS checkpoint bundles | revisions in gpu/models.lock.json | Microsoft model cards / MIT; DINOv3 has its own [Meta licence and gated access](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) |

**TRELLIS is disabled by default** (`RIG_FORGE_TRELLIS_ENABLED=false`) in both
brain and Forge. Keep it off pending the founder's decision. The MIT licence on
TRELLIS itself does not override its dependencies: **nvdiffrast and nvdiffrec use
the NVIDIA Source Code License, restricted to research or evaluation purposes**.
They are not cleared here for this paid product. **DINOv3 uses the separate Meta
DINOv3 licence with gated access**, not TRELLIS's MIT licence; access to the weights
does not constitute approval of commercial deployment. The founder is deciding
licensing and alternatives. The code and pinned build recipes remain available,
but no TRELLIS job can be reserved or launched through the default configuration.

The default TRELLIS background remover, RMBG-2.0, is **not included or fetched**.
Prepared images are composited on a neutral matte. DINOv3 weights must already be
lawfully available to the operator; the GPU image build verifies the provided
offline bundle. It does not request another service, credential or paid model.

Python dependencies (including NumPy, FastAPI, boto3 and the Hugging Face SDK)
are pinned with upstream PyPI SHA256 in `requirements.lock`; Node dependencies
are pinned with npm integrity metadata in `package-lock.json`. Their installed
distributions retain their own licence files.

## Checksum provenance

- Blender, gltfpack and KTX use published upstream SHA256; Python artifacts use
  upstream PyPI SHA256, enforced with `--require-hashes`.
- npm publishes SHA512 for the CLI and validator. The installer verifies that
  published value **and** the computed SHA256 recorded in `tools.lock.json`.
- Meshroom publishes the release size but no digest. The official release was
  downloaded over verified HTTPS, its size checked, and its SHA256 pinned.
- GitHub/GitLab source archives have published commit IDs and no archive SHA256.
  Every archive, including git submodules, was downloaded over verified HTTPS
  and given a SHA256 in `gpu/sources.lock.json`.
- Model LFS files use upstream SHA256; small files use the published Git blob
  SHA1 plus SHA256 where publicly downloadable. `prepare_models.py` verifies the
  licensed bundle and records a SHA256 for **every** file in the built image
  before adjusting the local pipeline configuration.

These fallbacks follow the founder's approved hash policy; a computed digest is
not described as an independently published upstream SHA256.
