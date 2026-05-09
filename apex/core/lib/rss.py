"""Random Solid Solution (RSS) generator for occupational disorder.

Intended use:
- HEA/HEO/high-entropy intermetallic occupational disorder generation
- Decorating a parent lattice with target sublattice compositions
- Tuning short-range order with Warren-Cowley SRO targets
"""

from __future__ import annotations

import math
import random
import warnings
from collections import Counter
from itertools import combinations_with_replacement
from typing import Dict, List, Optional, Sequence, Tuple

from pymatgen.core import Structure

try:
    from tqdm.auto import tqdm
except Exception:  # pragma: no cover
    tqdm = None


class RSSInputError(ValueError):
    """Raised when RSS generator inputs are invalid."""


def resolve_parent_lattice_auto(
    parent_lattice: dict,
    compositions: dict,
    composition_tolerance: float = 0.005,
    shape_mode: str = "near_cubic",
    maximum_num_atoms=None,
) -> None:
    """Resolve optional auto lattice parameters and supercell in-place."""
    from apex.core.lib.crys import estimate_lattice_constant, suggest_supercell

    if not parent_lattice:
        return

    parent_type = parent_lattice["type"]
    supported_auto_types = {"bcc", "fcc", "hcp", "tetragonal", "B2", "L12", "L10"}
    if "species" not in parent_lattice:
        if parent_type == "B2":
            parent_lattice["species"] = [
                next(iter(compositions["corner"])),
                next(iter(compositions["body"])),
            ]
        elif parent_type == "L12":
            face_species = next(iter(compositions["face"]))
            parent_lattice["species"] = [
                next(iter(compositions["corner"])),
                face_species,
                face_species,
                face_species,
            ]
        elif parent_type == "L10":
            parent_lattice["species"] = [
                next(iter(compositions["layer_A"])),
                next(iter(compositions["layer_B"])),
            ]

    should_resolve_a = (
        parent_lattice.get("a") == "auto"
        or ("a" not in parent_lattice and parent_type in supported_auto_types)
        or (parent_lattice.get("a") is None and parent_type in supported_auto_types)
    )
    if should_resolve_a:
        resolved_lattice = estimate_lattice_constant(
            parent_type,
            compositions,
            c_over_a=parent_lattice.get("c_over_a"),
        )
        parent_lattice["a"] = resolved_lattice["a"]
        if (
            "c" in resolved_lattice
            and ("c" not in parent_lattice or parent_lattice.get("c") == "auto")
        ):
            parent_lattice["c"] = resolved_lattice["c"]

    should_suggest_supercell = (
        parent_lattice.get("supercell") == "auto"
        or (
            "supercell" not in parent_lattice
            and parent_type in supported_auto_types
        )
        or (
            parent_lattice.get("supercell") is None
            and parent_type in supported_auto_types
        )
    )
    if should_suggest_supercell:
        parent_lattice["supercell"] = suggest_supercell(
            parent_type,
            compositions,
            composition_tolerance=composition_tolerance,
            shape_mode=shape_mode,
            maximum_num_atoms=maximum_num_atoms,
        )


def _canonical_pair(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _parse_pair_key(pair_key) -> Tuple[str, str]:
    if isinstance(pair_key, (tuple, list)) and len(pair_key) == 2:
        return _canonical_pair(str(pair_key[0]), str(pair_key[1]))
    if isinstance(pair_key, str):
        if "-" in pair_key:
            left, right = pair_key.split("-", 1)
            return _canonical_pair(left.strip(), right.strip())
        if ":" in pair_key:
            left, right = pair_key.split(":", 1)
            return _canonical_pair(left.strip(), right.strip())
    raise RSSInputError(f"Invalid species pair key: {pair_key}")


def _normalize_validate_compositions(
    compositions: dict,
    tol: float,
    allow_vacancies: bool,
) -> Dict[str, Dict[str, float]]:
    if not isinstance(compositions, dict) or not compositions:
        raise RSSInputError("compositions must be a non-empty dict")

    normalized = {}
    vacancy_aliases = {"vac", "vacancy", "x", "none"}
    for sub_name, comp in compositions.items():
        if not isinstance(comp, dict) or not comp:
            raise RSSInputError(
                f"Composition for sublattice '{sub_name}' must be a non-empty dict"
            )
        norm_comp = {}
        total = 0.0
        for species, frac in comp.items():
            sp = str(species)
            sp_lower = sp.lower()
            try:
                value = float(frac)
            except (TypeError, ValueError):
                raise RSSInputError(
                    f"Composition fraction for species '{sp}' must be numeric"
                ) from None
            if value < -tol:
                raise RSSInputError(
                    f"Composition fraction for species '{sp}' must be non-negative"
                )
            if abs(value) <= tol:
                continue
            if sp_lower in vacancy_aliases:
                if not allow_vacancies:
                    raise RSSInputError(
                        "Vacancy-like species found but allow_vacancies is False"
                    )
                sp = "X"
            norm_comp[sp] = value
            total += value
        if not norm_comp:
            raise RSSInputError(
                f"Composition for sublattice '{sub_name}' has no positive entries"
            )
        if abs(total - 1.0) > tol:
            raise RSSInputError(
                f"Composition fractions for sublattice '{sub_name}' must sum to 1.0"
            )
        normalized[str(sub_name)] = {k: v / total for k, v in norm_comp.items()}
    return normalized


def _build_sublattice_indices(
    structure: Structure,
    compositions: Dict[str, Dict[str, float]],
    sublattices: Optional[List[dict]],
) -> Dict[str, List[int]]:
    nsites = len(structure)
    if sublattices is None:
        if set(compositions.keys()) != {"all"}:
            raise RSSInputError(
                "When sublattices is None, compositions must use single key 'all'"
            )
        return {"all": list(range(nsites))}

    if not isinstance(sublattices, list) or not sublattices:
        raise RSSInputError("sublattices must be a non-empty list when provided")

    sub_map = {}
    seen = set()
    for item in sublattices:
        if not isinstance(item, dict):
            raise RSSInputError("Each sublattice entry must be a dict")
        if "name" not in item or "site_indices" not in item:
            raise RSSInputError("Each sublattice requires 'name' and 'site_indices'")
        name = str(item["name"])
        if name in sub_map:
            raise RSSInputError(f"Duplicate sublattice name: {name}")
        indices = item["site_indices"]
        if not isinstance(indices, list) or not indices:
            raise RSSInputError(f"sublattice '{name}' has empty site_indices")
        clean_indices = []
        for idx in indices:
            if not isinstance(idx, int):
                raise RSSInputError(f"sublattice '{name}' has non-integer site index")
            if idx < 0 or idx >= nsites:
                raise RSSInputError(f"sublattice '{name}' has out-of-range index {idx}")
            if idx in seen:
                raise RSSInputError(f"Site index {idx} appears in multiple sublattices")
            seen.add(idx)
            clean_indices.append(idx)
        sub_map[name] = clean_indices

    if set(sub_map.keys()) != set(compositions.keys()):
        raise RSSInputError(
            "Sublattice names must match composition keys in multi-sublattice mode"
        )
    return sub_map


def _integerize_composition_counts(
    nsites: int,
    composition: Dict[str, float],
    tol: float,
) -> Dict[str, int]:
    if nsites <= 0:
        raise RSSInputError("nsites must be positive")

    targets = []
    expected_total = 0.0
    for species, frac in composition.items():
        value = float(frac) * nsites
        targets.append((species, value))
        expected_total += value

    if abs(expected_total - nsites) > max(tol, 1e-12) * max(1, nsites):
        warnings.warn(
            "Given supercell/site count cannot realize requested composition "
            f"(target total {expected_total} on {nsites} sites)",
            UserWarning,
        )
        raise RSSInputError(
            f"Composition {composition} incompatible with {nsites} sites"
        )

    counts = {}
    residuals = []
    running_total = 0
    for species, value in targets:
        base = int(math.floor(value + 1e-12))
        counts[species] = base
        running_total += base
        residuals.append((value - base, species))

    remaining = nsites - running_total
    if remaining > 0:
        residuals.sort(key=lambda item: (-item[0], str(item[1])))
        for _, species in residuals[:remaining]:
            counts[species] += 1

    max_error = max(
        (abs(value - counts[species]) for species, value in targets),
        default=0.0,
    )
    if max_error > tol:
        warnings.warn(
            "Given supercell/site count cannot realize requested composition exactly; "
            "using nearest integer counts",
            UserWarning,
        )

    return counts


def _assign_initial_species(
    state_species: List[str],
    site_indices: List[int],
    counts: Dict[str, int],
    rng: random.Random,
) -> None:
    assigned = []
    for species, count in counts.items():
        assigned.extend([species] * count)
    if len(assigned) != len(site_indices):
        raise RSSInputError("Assigned species count does not match sublattice size")
    rng.shuffle(assigned)
    for idx, sp in zip(site_indices, assigned):
        state_species[idx] = sp


def _default_shell_cutoffs(structure: Structure) -> List[float]:
    dmat = structure.distance_matrix
    min_dist = None
    for i in range(len(structure)):
        for j in range(i + 1, len(structure)):
            d = float(dmat[i][j])
            if d <= 1e-12:
                continue
            if min_dist is None or d < min_dist:
                min_dist = d
    if min_dist is None:
        raise RSSInputError("Could not infer default shell cutoff from structure")
    return [1.05 * min_dist]


def _build_neighbor_shells(
    structure: Structure,
    shell_cutoffs: Sequence[float],
) -> Dict[int, List[Tuple[int, int]]]:
    cutoffs = [float(v) for v in shell_cutoffs]
    if any(v <= 0 for v in cutoffs):
        raise RSSInputError("shell_cutoffs must be positive")
    if sorted(cutoffs) != list(cutoffs):
        raise RSSInputError("shell_cutoffs must be sorted ascending")

    shell_pairs = {sid: [] for sid in range(len(cutoffs))}
    dmat = structure.distance_matrix
    nsites = len(structure)

    max_cutoff = cutoffs[-1]
    for i in range(nsites):
        for j in range(i + 1, nsites):
            dist = float(dmat[i][j])
            if dist <= 1e-12 or dist > max_cutoff:
                continue
            for shell_id, cutoff in enumerate(cutoffs):
                if dist <= cutoff:
                    shell_pairs[shell_id].append((i, j))
                    break
    return shell_pairs


def _normalize_shell_weights(
    shell_cutoffs: Sequence[float],
    shell_weights: Optional[Sequence[float]],
) -> Dict[int, float]:
    nshells = len(shell_cutoffs)
    if shell_weights is None:
        return {sid: 1.0 for sid in range(nshells)}
    if len(shell_weights) != nshells:
        raise RSSInputError("shell_weights length must match shell_cutoffs length")
    weights = {}
    for sid, weight in enumerate(shell_weights):
        w = float(weight)
        if w < 0:
            raise RSSInputError("shell_weights must be non-negative")
        weights[sid] = w
    return weights


def _normalize_sro_targets(
    sro_targets: Optional[dict],
    nshells: int,
    pair_keys: Sequence[Tuple[str, str]],
) -> Dict[int, Dict[Tuple[str, str], float]]:
    pair_set = set(pair_keys)
    if sro_targets is None:
        return {
            sid: {pair: 0.0 for pair in pair_keys}
            for sid in range(nshells)
        }

    normalized = {sid: {} for sid in range(nshells)}
    for shell_key, shell_target in sro_targets.items():
        try:
            if isinstance(shell_key, str):
                if shell_key.startswith("shell"):
                    shell_id = int(shell_key[5:])
                else:
                    shell_id = int(shell_key)
            else:
                shell_id = int(shell_key)
        except (TypeError, ValueError) as exc:
            raise RSSInputError(
                "Invalid sro_targets shell key "
                f"{shell_key!r}; expected shell0, shell1, ... or 0, 1, ..."
            ) from exc
        if shell_id < 0 or shell_id >= nshells:
            if nshells == 0:
                valid_shells = "none"
            else:
                valid_shells = ", ".join(f"shell{sid}" for sid in range(nshells))
            raise RSSInputError(
                "Invalid shell index in sro_targets: "
                f"{shell_key}. Configured shell_cutoffs define {nshells} shell(s), "
                f"so valid keys are: {valid_shells}"
            )
        if not isinstance(shell_target, dict):
            raise RSSInputError(f"SRO target for shell {shell_key} must be dict")
        for pair_key, val in shell_target.items():
            pair = _parse_pair_key(pair_key)
            if pair not in pair_set:
                pair_set.add(pair)
            normalized[shell_id][pair] = float(val)

    for sid in range(nshells):
        for pair in pair_set:
            normalized[sid].setdefault(pair, 0.0)
    return normalized


def _composition_fractions_from_state(species_state: Sequence[str]) -> Dict[str, float]:
    total = float(len(species_state))
    counts = Counter(species_state)
    return {sp: counts[sp] / total for sp in counts}


def _compute_warren_cowley_sro(
    species_state: Sequence[str],
    shell_pairs: Dict[int, List[Tuple[int, int]]],
    pair_keys: Sequence[Tuple[str, str]],
    composition_fractions: Dict[str, float],
) -> Dict[int, Dict[Tuple[str, str], float]]:
    achieved = {}
    for shell_id, pairs in shell_pairs.items():
        n_a = Counter()
        n_ab = Counter()
        for i, j in pairs:
            ai = species_state[i]
            aj = species_state[j]
            n_a[ai] += 1
            n_ab[(ai, aj)] += 1
            n_a[aj] += 1
            n_ab[(aj, ai)] += 1

        shell_alpha = {}
        for a, b in pair_keys:
            c_a = composition_fractions.get(a, 0.0)
            c_b = composition_fractions.get(b, 0.0)
            if a == b:
                if n_a[a] == 0 or c_b <= 1e-16:
                    alpha = 0.0
                else:
                    p_ab = n_ab[(a, b)] / float(n_a[a])
                    alpha = 1.0 - p_ab / c_b
            else:
                terms = []
                if n_a[a] > 0 and c_b > 1e-16:
                    p_ab = n_ab[(a, b)] / float(n_a[a])
                    terms.append(1.0 - p_ab / c_b)
                if n_a[b] > 0 and c_a > 1e-16:
                    p_ba = n_ab[(b, a)] / float(n_a[b])
                    terms.append(1.0 - p_ba / c_a)
                alpha = sum(terms) / float(len(terms)) if terms else 0.0
            shell_alpha[(a, b)] = float(alpha)
        achieved[shell_id] = shell_alpha
    return achieved


def _objective_function(
    achieved_sro: Dict[int, Dict[Tuple[str, str], float]],
    target_sro: Dict[int, Dict[Tuple[str, str], float]],
    shell_weights: Dict[int, float],
) -> float:
    value = 0.0
    for shell_id, targets in target_sro.items():
        weight = shell_weights.get(shell_id, 1.0)
        for pair, target in targets.items():
            now = achieved_sro.get(shell_id, {}).get(pair, 0.0)
            diff = now - target
            value += weight * diff * diff
    return float(value)


def _sro_gap_metrics(
    achieved_sro: Dict[int, Dict[Tuple[str, str], float]],
    target_sro: Dict[int, Dict[Tuple[str, str], float]],
    shell_weights: Dict[int, float],
) -> Dict[str, float]:
    weighted_sum = 0.0
    weight_total = 0.0
    max_abs_gap = 0.0
    for shell_id, targets in target_sro.items():
        weight = shell_weights.get(shell_id, 1.0)
        for pair, target in targets.items():
            now = achieved_sro.get(shell_id, {}).get(pair, 0.0)
            gap = abs(now - target)
            weighted_sum += weight * gap * gap
            weight_total += weight
            if gap > max_abs_gap:
                max_abs_gap = gap
    rmse_gap = math.sqrt(weighted_sum / weight_total) if weight_total > 0 else 0.0
    return {
        "rmse": float(rmse_gap),
        "max_abs": float(max_abs_gap),
    }


def _pick_swap(
    species_state: Sequence[str],
    sublattice_indices: Dict[str, List[int]],
    rng: random.Random,
    max_tries: int = 30,
) -> Optional[Tuple[int, int]]:
    valid_names = [
        name
        for name, indices in sublattice_indices.items()
        if len(indices) >= 2
    ]
    if not valid_names:
        return None

    for _ in range(max_tries):
        sub_name = rng.choice(valid_names)
        indices = sublattice_indices[sub_name]
        i, j = rng.sample(indices, 2)
        if species_state[i] != species_state[j]:
            return i, j
    return None


def _reconstruct_structure(parent: Structure, species_state: Sequence[str]) -> Structure:
    site_props = dict(parent.site_properties) if parent.site_properties else None
    return Structure(
        lattice=parent.lattice,
        species=list(species_state),
        coords=parent.frac_coords,
        site_properties=site_props,
        coords_are_cartesian=False,
    )


def generate_rss(
    structure,
    compositions,
    sublattices=None,
    sro_targets=None,
    shell_cutoffs=None,
    shell_weights=None,
    max_steps=20000,
    temperature=0.05,
    seed=None,
    tol=1e-3,
    allow_vacancies=False,
    num_configs=1,
    interval=100,
    show_progress=False,
    patience=None,
    ratio_precision=None,
    return_metadata=False,
):
    """Generate a random solid solution structure with optional SRO targeting.

    Parameters follow the APEX RSS contract: composition control by sublattice,
    shell-based Warren-Cowley targets, and swap-based Metropolis optimization.
    """ 
    if not isinstance(structure, Structure):
        raise RSSInputError("structure must be a pymatgen.core.Structure")

    tol = float(tol)
    max_steps = int(max_steps)
    if max_steps < 0:
        raise RSSInputError("max_steps must be non-negative")
    temperature = float(temperature)
    if temperature < 0:
        raise RSSInputError("temperature must be non-negative")
    num_configs = int(num_configs)
    if num_configs <= 0:
        raise RSSInputError("num_configs must be a positive integer")
    interval = int(interval)
    if interval <= 0:
        raise RSSInputError("interval must be a positive integer")
    if patience is not None:
        patience = int(patience)
        if patience <= 0:
            raise RSSInputError("patience must be a positive integer or None")
    if ratio_precision is not None:
        ratio_precision = int(ratio_precision)
        if ratio_precision < 0:
            raise RSSInputError("ratio_precision must be a non-negative integer or None")

    rng = random.Random(seed)
    parent = structure.copy()

    normalized_compositions = _normalize_validate_compositions(
        compositions=compositions,
        tol=tol,
        allow_vacancies=allow_vacancies,
    )
    sub_map = _build_sublattice_indices(parent, normalized_compositions, sublattices)

    state_species = [str(site.specie) for site in parent.sites]
    composition_counts = {}
    for sub_name, indices in sub_map.items():
        counts = _integerize_composition_counts(
            nsites=len(indices),
            composition=normalized_compositions[sub_name],
            tol=tol,
        )
        composition_counts[sub_name] = counts
        _assign_initial_species(state_species, indices, counts, rng)

    composition_ratios = {}
    for sub_name, counts in composition_counts.items():
        total_sites = len(sub_map[sub_name])
        sub_ratios = {
            species: count / total_sites for species, count in counts.items()
        }
        if ratio_precision is not None:
            sub_ratios = {
                species: round(value, ratio_precision)
                for species, value in sub_ratios.items()
            }
        composition_ratios[sub_name] = sub_ratios

    if shell_cutoffs is None:
        cutoffs = _default_shell_cutoffs(parent)
    else:
        cutoffs = [float(v) for v in shell_cutoffs]
        if not cutoffs:
            raise RSSInputError("shell_cutoffs must be non-empty when provided")

    shell_pairs = _build_neighbor_shells(parent, cutoffs)
    if all(len(pairs) == 0 for pairs in shell_pairs.values()):
        raise RSSInputError("No neighbor pairs found within shell_cutoffs")

    all_species = sorted(
        {
            sp
            for comp in normalized_compositions.values()
            for sp in comp.keys()
        }
    )
    pair_keys = list(combinations_with_replacement(all_species, 2))

    target_sro = _normalize_sro_targets(sro_targets, len(cutoffs), pair_keys)
    target_pair_keys = sorted(
        {
            pair
            for shell_data in target_sro.values()
            for pair in shell_data.keys()
        }
    )

    weights = _normalize_shell_weights(cutoffs, shell_weights)

    composition_fractions = _composition_fractions_from_state(state_species)
    current_sro = _compute_warren_cowley_sro(
        state_species,
        shell_pairs,
        target_pair_keys,
        composition_fractions,
    )
    current_objective = _objective_function(current_sro, target_sro, weights)
    initial_objective = float(current_objective)
    current_gap_metrics = _sro_gap_metrics(current_sro, target_sro, weights)
    initial_gap_metrics = dict(current_gap_metrics)

    best_species = list(state_species)
    best_sro = current_sro
    best_objective = current_objective
    best_gap_metrics = dict(current_gap_metrics)

    accepted_moves = 0
    attempted_moves = 0
    last_improve_step = 0
    sampled_cache = []
    sampled_cache_index = {}
    near_target_threshold = max(5.0 * tol, 1e-2)

    def _store_sample(species_snapshot, step_value, rmse_value):
        key = tuple(species_snapshot)
        existing_index = sampled_cache_index.get(key)
        entry = {
            "species": list(species_snapshot),
            "step": int(step_value),
            "rmse": float(rmse_value),
        }

        if existing_index is not None:
            existing_entry = sampled_cache[existing_index]
            if (
                entry["rmse"] < existing_entry["rmse"] - 1e-12
                or (
                    abs(entry["rmse"] - existing_entry["rmse"]) <= 1e-12
                    and entry["step"] < existing_entry["step"]
                )
            ):
                sampled_cache[existing_index] = entry
            else:
                return
        else:
            sampled_cache.append(entry)

        sampled_cache.sort(key=lambda item: (item["rmse"], item["step"]))
        if len(sampled_cache) > num_configs:
            removed_entry = sampled_cache.pop()
            sampled_cache_index.pop(tuple(removed_entry["species"]), None)
        sampled_cache_index.clear()
        for index, cached_entry in enumerate(sampled_cache):
            sampled_cache_index[tuple(cached_entry["species"])] = index
    progress_bar = None
    if show_progress:
        if tqdm is None:
            warnings.warn(
                "show_progress=True but tqdm is not available; proceeding without progress bar",
                UserWarning,
            )
        else:
            progress_bar = tqdm(
                total=max_steps,
                desc="RSS sampling",
                unit="step",
                dynamic_ncols=True,
            )
            progress_bar.set_postfix(
                {
                    "gap_rmse": f"{current_gap_metrics['rmse']:.4f}",
                    "best_rmse": f"{best_gap_metrics['rmse']:.4f}",
                }
            )

    for step in range(1, max_steps + 1):
        proposal = _pick_swap(state_species, sub_map, rng)
        if proposal is None:
            break

        i, j = proposal
        attempted_moves += 1
        state_species[i], state_species[j] = state_species[j], state_species[i]

        composition_fractions = _composition_fractions_from_state(state_species)
        trial_sro = _compute_warren_cowley_sro(
            state_species,
            shell_pairs,
            target_pair_keys,
            composition_fractions,
        )
        trial_objective = _objective_function(trial_sro, target_sro, weights)
        delta = trial_objective - current_objective

        accept = False
        if delta <= 0:
            accept = True
        elif temperature > 0:
            threshold = math.exp(-delta / max(temperature, 1e-12))
            accept = rng.random() < threshold

        if accept:
            accepted_moves += 1
            current_sro = trial_sro
            current_objective = trial_objective
            current_gap_metrics = _sro_gap_metrics(current_sro, target_sro, weights)
            if trial_objective + tol < best_objective:
                best_objective = trial_objective
                best_sro = trial_sro
                best_species = list(state_species)
                best_gap_metrics = dict(current_gap_metrics)
                last_improve_step = step
        else:
            state_species[i], state_species[j] = state_species[j], state_species[i]

        if step % interval == 0:
            _store_sample(
                state_species,
                step,
                float(current_gap_metrics["rmse"]),
            )

        if progress_bar is not None:
            progress_bar.update(1)
            progress_bar.set_postfix(
                {
                    "gap_rmse": f"{current_gap_metrics['rmse']:.4f}",
                    "best_rmse": f"{best_gap_metrics['rmse']:.4f}",
                },
                refresh=False,
            )

        if patience is not None and step - last_improve_step >= patience and step >= patience:
            break

    if progress_bar is not None:
        progress_bar.close()

    if num_configs == 1:
        decorated = _reconstruct_structure(parent, best_species)
    else:
        sampled_species = [entry["species"] for entry in sampled_cache]
        sampled_steps = [entry["step"] for entry in sampled_cache]
        sampled_rmses = [entry["rmse"] for entry in sampled_cache]
        if not sampled_species:
            sampled_species.append(list(best_species))
            sampled_steps.append(-1)
            sampled_rmses.append(float(best_gap_metrics["rmse"]))
        while len(sampled_species) < num_configs:
            sampled_species.append(list(best_species))
            sampled_steps.append(-1)
            sampled_rmses.append(float(best_gap_metrics["rmse"]))
        decorated = [
            _reconstruct_structure(parent, sp)
            for sp in sampled_species[:num_configs]
        ]
    if num_configs == 1:
        sampled_steps = []
        sampled_rmses = []

    if best_gap_metrics["rmse"] > max(10.0 * tol, 0.1):
        warnings.warn(
            "SRO gap remains large after optimization: "
            f"best_rmse={best_gap_metrics['rmse']:.4f}, "
            f"best_max_abs={best_gap_metrics['max_abs']:.4f}. "
            "Consider increasing max_steps or temperature.",
            UserWarning,
        )

    metadata = {
        "seed": seed,
        "composition_counts": composition_counts,
        "composition_ratios": composition_ratios,
        "ratio_precision": ratio_precision,
        "shell_cutoffs": list(cutoffs),
        "target_sro": target_sro,
        "achieved_sro": best_sro,
        "initial_objective": initial_objective,
        "final_objective": float(current_objective),
        "best_objective": float(best_objective),
        "accepted_moves": int(accepted_moves),
        "attempted_moves": int(attempted_moves),
        "acceptance_ratio": float(accepted_moves / attempted_moves) if attempted_moves else 0.0,
        "sro_gap": {
            "initial_rmse": initial_gap_metrics["rmse"],
            "initial_max_abs": initial_gap_metrics["max_abs"],
            "final_rmse": current_gap_metrics["rmse"],
            "final_max_abs": current_gap_metrics["max_abs"],
            "best_rmse": best_gap_metrics["rmse"],
            "best_max_abs": best_gap_metrics["max_abs"],
        },
        "sampling": {
            "num_configs": num_configs,
            "interval": interval,
            "sampled_steps": sampled_steps,
            "sampled_rmses": sampled_rmses,
            "near_target_threshold": near_target_threshold,
            "patience": patience,
        },
    }

    if return_metadata:
        return decorated, metadata
    return decorated
