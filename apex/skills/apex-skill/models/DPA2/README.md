# DPA2

- `DPA2.pb` — frozen TensorFlow graph, **ready for APEX** (`interaction.model`)

Large multi-head `dpa-2.4-7M.pt` is **not** bundled (too large for the skill zip).
Only download if the user needs another head:

```bash
python ../../scripts/fetch_models.py --dpa2-pt
dp --pt freeze -c dpa-2.4-7M.pt -o DPA2_alloy.pth --head Domains_Alloy
```
