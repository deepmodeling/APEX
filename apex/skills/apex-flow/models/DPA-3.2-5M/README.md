# DPA-3.2-5M

- `DPA-3.2-5M-OMat24.pth` — frozen single-task PyTorch model, ready for
  `interaction.model` in APEX.
- Source: official DPA-3.2-5M multi-task checkpoint.
- Frozen head: `OMat24`.
- DeePMD-kit version: 3.1.3.
- Observed-element coverage: 89 elements, including O.
- SHA-256:
  `055fbbcb83c9063f7809a74803c600eb34cf0b3f7caf15e0e0d2b86834f30e8e`.

Use `"type_map": "auto"` and copy the `.pth` file into the submitted job
directory. The original multi-task `.pt` checkpoint is intentionally excluded
from the skill zip.
