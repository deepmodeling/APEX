# Bundled DPA models

> **Naming:** APEX **backend** = `lammps` / `abacus` / `vasp`.  
> DPA-2 / DPA_alloy / DPA-3 are **model files** used when the backend is LAMMPS + DeePMD.

The skill ships **only small frozen graphs** suitable for MatMaster zip upload:

| Path | Size | Ready for APEX? |
|------|------|-----------------|
| `DPA2/DPA2.pb` | ~6MB | Yes |
| `DPA_alloy/DPA_alloy.pb` | ~6MB | Yes (prefer for alloys / HEA) |

Large multi-head checkpoints (`.pt`, DPA3, …) are **not** bundled.

## Agent workflow

1. For LAMMPS + DeePMD/DPA, **use a bundled frozen model by default** and copy it
   into the job directory:
   - general DPA-2 → `models/DPA2/DPA2.pb`
   - alloys / HEA → `models/DPA_alloy/DPA_alloy.pb`
2. Set `"type": "deepmd"`, `"model": "<copied filename>"`, and
   `"type_map": "auto"`. APEX infers a zero-based, contiguous map from the
   structure during submission. Do not use atomic numbers or model-internal
   indices.
3. **Only if** the user explicitly needs DPA3 or another multi-head checkpoint, download
   outside the skill zip (do not put large files back into the skill for upload):
   ```bash
   python scripts/fetch_models.py --dpa3          # ~62MB, then freeze a head
   python scripts/fetch_models.py --dpa2-pt       # ~76MB, optional
   # or:
   dp pretrained download DPA-3.2-5M
   ```
4. Multi-head `.pt` must be frozen before LAMMPS/APEX:
   ```bash
   dp --pt freeze -c DPA-3.2-5M.pt -o DPA3_Alloy_APEX.pth --head Alloy_APEX
   ```

Do not invent a model filename or download a replacement merely because the
bundled files were overlooked. If neither bundled model supports the requested
elements, explain that incompatibility and obtain the user's model choice before
continuing.

## Sources

- Frozen defaults: shipped with this skill (`DPA2.pb` / `DPA_alloy.pb`)
- Optional DPA2 checkpoint: [DPA-2.4-7M](https://huggingface.co/deepmodelingcommunity/DPA-2.4-7M)
- Optional DPA3 checkpoint: [DPA-3.2-5M](https://huggingface.co/deepmodelingcommunity/DPA-3.2-5M)
