# DPA_alloy

Prefer this directory for alloy / HEA / multi-component metal jobs.

- `DPA_alloy.pb` — frozen TensorFlow graph, **ready for APEX**

Agent: copy `DPA_alloy.pb` into the job dir before downloading anything else.
Do not bundle large `.pt` checkpoints into the skill upload zip.
