# Two-phase coexistence melting point

The APEX `melting_point` property implements a direct solid/liquid
coexistence bracket for LAMMPS potentials.

Each task uses the same relaxed structure and supercell construction:

1. The cell is divided along `interface_axis` (default `z`).
2. The upper `liquid_fraction` is heated to `premelt_temperature` while the
   lower crystal is pinned.
3. The liquid seed is conditioned at the target temperature.
4. Both halves are released for `production_steps` using the requested NPT
   barostat and pressure.
5. LAMMPS records local Steinhardt `q6`, thermodynamics, and seed-resolved MSD.
6. APEX normalizes the released trajectory against the prepared solid/liquid
   q6 gap and fits 2 ps block means with a Theil-Sen slope.

LAMMPS writes alternating binary checkpoints `restart.melting.1` and
`restart.melting.2` every `restart_interval` timesteps, starting during the
premelt stage. A successful task also writes `restart.melting.final`. APEX
retrieves all `restart.melting.*` files together with the trajectory and log;
the restart interval defaults to 10000 timesteps (10 ps at the default
0.001 ps timestep).

A temperature is a solid-side endpoint only when the 95% slope interval lies
above zero and the projected solid-fraction change exceeds the configured
minimum. A liquid-side endpoint uses the corresponding negative criteria.
The highest solid-side and lowest liquid-side endpoints form the bracket.

The workflow does not automatically submit new temperatures. If the bracket
is missing or too wide, use the reported recommended midpoint in a new APEX
property suffix after reviewing and approving the calculation.

For reproducible finite-size studies, keep the structure orientation,
interface axis, liquid fraction, temperature protocol, seeds, timestep,
thermostat/barostat settings, and analysis parameters identical while changing
only `supercell_size`.
