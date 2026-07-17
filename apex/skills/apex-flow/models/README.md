# Bundled DPA model

> **Naming:** APEX **backend** = `lammps` / `abacus` / `vasp`.
> DPA-3.2-5M is a DeePMD model used by the LAMMPS backend.

The skill ships one ready-to-run frozen model:

| Path | Size | Branch | Ready for APEX? |
|------|------|--------|-----------------|
| `DPA-3.2-5M/DPA-3.2-5M-OMat24.pth` | ~23MB | `OMat24` | Yes |

The bundled model is a single-task PyTorch model frozen from the official
DPA-3.2-5M checkpoint with DeePMD-kit 3.1.3. Its `OMat24` branch has 89
observed elements, including oxygen. The model `type_map` spans all 118 element
symbols, but an element should be considered supported only when it appears in
the branch's observed-element list.

## Agent workflow

1. For LAMMPS + DeePMD/DPA, copy
   `models/DPA-3.2-5M/DPA-3.2-5M-OMat24.pth` into the job directory.
2. Set `"type": "deepmd"`, `"model": "DPA-3.2-5M-OMat24.pth"`, and
   `"type_map": "auto"`. APEX infers a zero-based, contiguous map from the
   structure during submission. Do not use atomic numbers or model-internal
   indices.
3. If the user explicitly needs another DPA-3.2 task head, download the source
   checkpoint outside the skill zip:
   ```bash
   python scripts/fetch_models.py --source-checkpoint
   # or:
   dp --pt pretrained download DPA-3.2-5M
   ```
4. Freeze the selected head before LAMMPS/APEX use:
   ```bash
   dp --pt freeze -c DPA-3.2-5M.pt -o DPA-3.2-5M-Alloy_APEX.pth --head Alloy_APEX
   ```

Never pass the multi-task `.pt` checkpoint directly to LAMMPS. Do not invent a
model filename. If the OMat24 observed-element coverage or training domain is
unsuitable, explain the limitation and obtain the user's task-head choice.

## Source

- Official checkpoint: [DPA-3.2-5M](https://huggingface.co/deepmodelingcommunity/DPA-3.2-5M)
