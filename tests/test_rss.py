import json
import tempfile
import sys
import random
import unittest
import warnings
from pathlib import Path
from collections import Counter
from unittest.mock import patch

from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp import Poscar

from apex.core.lib.crys import (
    bcc,
    diamond,
    fcc,
    hcp,
    sc,
    suggest_supercell,
    tetragonal,
)
from apex.core.lib.rss import (
    RSSInputError,
    _assign_initial_species,
    _build_neighbor_shells,
    _build_sublattice_indices,
    _compute_warren_cowley_sro,
    _default_shell_cutoffs,
    _integerize_composition_counts,
    _normalize_shell_weights,
    _normalize_sro_targets,
    _normalize_validate_compositions,
    _parse_pair_key,
    _pick_swap,
    generate_rss,
)
from apex.rss import (
    _auto_assign_sublattices,
    rss_from_args,
    run_rss_config,
)
from apex.main import parse_args


class TestRSS(unittest.TestCase):
    def test_parent_lattice_builders_accept_none_placeholder_element(self):
        builders = [
            (fcc, {"a": 3.6}),
            (bcc, {"a": 3.0}),
            (sc, {"a": 3.0}),
            (hcp, {"a": 3.0, "c": 4.9}),
            (tetragonal, {"a": 3.0, "c": 3.2}),
            (diamond, {"a": 3.6}),
        ]
        for builder, kwargs in builders:
            with self.subTest(builder=builder.__name__):
                structure = builder(None, **kwargs)
                self.assertGreater(len(structure), 0)

    def test_rss_cli_parser(self):
        argv = ["apex", "rss", "examples/rss/rss.json"]
        with patch.object(sys, "argv", argv):
            _, args = parse_args()

        self.assertEqual(args.cmd, "rss")
        self.assertEqual(args.rss_json, "examples/rss/rss.json")

    def test_single_sublattice_equimolar_count_conservation(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 2, 5])  # 20 sites

        compositions = {
            "all": {
                "Co": 0.2,
                "Cr": 0.2,
                "Fe": 0.2,
                "Mn": 0.2,
                "Ni": 0.2,
            }
        }

        out, meta = generate_rss(
            structure=st,
            compositions=compositions,
            shell_cutoffs=[2.8],
            max_steps=1500,
            seed=7,
            return_metadata=True,
        )

        counts = Counter(str(site.specie) for site in out.sites)
        self.assertEqual(len(out), 20)
        self.assertEqual(counts["Co"], 4)
        self.assertEqual(counts["Cr"], 4)
        self.assertEqual(counts["Fe"], 4)
        self.assertEqual(counts["Mn"], 4)
        self.assertEqual(counts["Ni"], 4)

        self.assertIn("initial_objective", meta)
        self.assertIn("best_objective", meta)
        self.assertIn("acceptance_ratio", meta)

    def test_suggest_supercell_respects_maximum_num_atoms(self):
        compositions = {
            "corner": {
                "Al": 0.50287,
                "Co": 0.12201,
                "Cr": 0.07537,
                "Fe": 0.04768,
                "Mn": 0.14051,
                "Ni": 0.11156,
            },
            "body": {
                "Al": 0.00103,
                "Co": 0.41705,
                "Cr": 0.03401,
                "Fe": 0.08123,
                "Mn": 0.15832,
                "Ni": 0.30836,
            },
        }

        supercell = suggest_supercell(
            "B2",
            compositions,
            composition_tolerance=0.01,
            shape_mode="xy_equal_z_free",
            maximum_num_atoms=128,
        )

        self.assertEqual(supercell, [4, 4, 4])
        self.assertLessEqual(2 * supercell[0] * supercell[1] * supercell[2], 128)

    def test_multi_sublattice_oxide_cation_changes_anion_unchanged(self):
        st = Structure(
            Lattice.cubic(4.2),
            ["Na", "O"],
            [[0, 0, 0], [0.5, 0.5, 0.5]],
        )
        st.make_supercell([2, 2, 2])

        cation_indices = [i for i, site in enumerate(st.sites) if str(site.specie) == "Na"]
        anion_indices = [i for i, site in enumerate(st.sites) if str(site.specie) == "O"]

        out = generate_rss(
            structure=st,
            compositions={
                "cation": {"Co": 0.5, "Ni": 0.5},
                "anion": {"O": 1.0},
            },
            sublattices=[
                {"name": "cation", "site_indices": cation_indices},
                {"name": "anion", "site_indices": anion_indices},
            ],
            shell_cutoffs=[4.3],
            max_steps=2000,
            seed=11,
        )

        out_species = [str(site.specie) for site in out.sites]
        cation_species = [out_species[i] for i in cation_indices]
        anion_species = [out_species[i] for i in anion_indices]

        cation_counts = Counter(cation_species)
        self.assertEqual(cation_counts["Co"], 4)
        self.assertEqual(cation_counts["Ni"], 4)
        self.assertTrue(all(sp == "O" for sp in anion_species))

    def test_reproducibility_with_same_seed(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 2, 2])

        kwargs = {
            "structure": st,
            "compositions": {"all": {"Co": 0.5, "Ni": 0.5}},
            "shell_cutoffs": [2.6],
            "max_steps": 1000,
            "seed": 123,
        }

        out1 = generate_rss(**kwargs)
        out2 = generate_rss(**kwargs)

        sp1 = [str(site.specie) for site in out1.sites]
        sp2 = [str(site.specie) for site in out2.sites]
        self.assertEqual(sp1, sp2)

    def test_objective_decreases_for_toy_case(self):
        st = Structure(
            Lattice.orthorhombic(1.0, 10.0, 10.0),
            ["Na"],
            [[0, 0, 0]],
        )
        st.make_supercell([8, 1, 1])

        _, meta = generate_rss(
            structure=st,
            compositions={"all": {"Co": 0.5, "Ni": 0.5}},
            sro_targets={
                0: {
                    ("Co", "Ni"): -1.0,
                    ("Co", "Co"): 1.0,
                    ("Ni", "Ni"): 1.0,
                }
            },
            shell_cutoffs=[1.1],
            max_steps=4000,
            temperature=0.03,
            seed=9,
            return_metadata=True,
        )

        self.assertLess(meta["best_objective"], meta["initial_objective"])

    def test_invalid_composition_sum_raises(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 2, 1])

        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Co": 0.6, "Ni": 0.5}},
                shell_cutoffs=[2.6],
            )

    def test_incompatible_composition_and_site_count_warns_and_rounds(self):
        st = Structure(
            Lattice.orthorhombic(1.0, 10.0, 10.0),
            ["Na"],
            [[0, 0, 0]],
        )
        st.make_supercell([7, 1, 1])

        with self.assertWarns(UserWarning):
            out = generate_rss(
                structure=st,
                compositions={"all": {"Co": 0.5, "Ni": 0.5}},
                shell_cutoffs=[1.1],
                max_steps=0,
            )

        counts = Counter(str(site.specie) for site in out.sites)
        self.assertEqual(counts["Co"], 4)
        self.assertEqual(counts["Ni"], 3)

    def test_num_configs_and_interval_return_multiple_structures(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 2, 2])

        outputs, meta = generate_rss(
            structure=st,
            compositions={"all": {"Co": 0.5, "Ni": 0.5}},
            shell_cutoffs=[2.8],
            max_steps=500,
            temperature=0.05,
            seed=17,
            num_configs=3,
            interval=20,
            return_metadata=True,
        )

        self.assertIsInstance(outputs, list)
        self.assertEqual(len(outputs), 3)
        self.assertIn("sampling", meta)
        self.assertEqual(meta["sampling"]["num_configs"], 3)
        self.assertEqual(meta["sampling"]["interval"], 20)
        self.assertEqual(len(meta["sampling"]["sampled_rmses"]), 3)
        species_signatures = {
            tuple(str(site.specie) for site in structure.sites)
            for structure in outputs
        }
        self.assertEqual(len(species_signatures), len(outputs))

    def test_num_configs_cache_keeps_best_unique_configs_by_rmse(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 2, 1])
        swap_sequence = [(0, 1), (1, 2), (2, 3), (0, 3), (0, 2), (1, 3)]
        rmse_sequence = [
            {"rmse": 0.50, "max_abs": 0.50},
            {"rmse": 0.40, "max_abs": 0.40},
            {"rmse": 0.20, "max_abs": 0.20},
            {"rmse": 0.30, "max_abs": 0.30},
            {"rmse": 0.15, "max_abs": 0.15},
            {"rmse": 0.25, "max_abs": 0.25},
            {"rmse": 0.10, "max_abs": 0.10},
        ]

        def deterministic_assign(state_species, indices, counts, rng):
            expanded = []
            for species, count in sorted(counts.items()):
                expanded.extend([species] * count)
            for site_index, species in zip(indices, expanded):
                state_species[site_index] = species

        with patch("apex.core.lib.rss._assign_initial_species", side_effect=deterministic_assign):
            with patch("apex.core.lib.rss._pick_swap", side_effect=swap_sequence):
                with patch("apex.core.lib.rss._compute_warren_cowley_sro", return_value={}):
                    with patch("apex.core.lib.rss._objective_function", return_value=0.0):
                        with patch("apex.core.lib.rss._sro_gap_metrics", side_effect=rmse_sequence):
                            outputs, meta = generate_rss(
                                structure=st,
                                compositions={"all": {"Co": 0.5, "Ni": 0.5}},
                                shell_cutoffs=[2.8],
                                max_steps=len(swap_sequence),
                                interval=1,
                                num_configs=2,
                                tol=0.05,
                                return_metadata=True,
                            )

        self.assertEqual(len(outputs), 2)
        self.assertEqual(meta["sampling"]["sampled_steps"], [6, 4])
        self.assertEqual(meta["sampling"]["sampled_rmses"], [0.10, 0.15])

    def test_num_configs_cache_updates_duplicate_config_with_lower_rmse(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 2, 1])
        swap_sequence = [(0, 1), (0, 1), (2, 3), (2, 3)]
        rmse_sequence = [
            {"rmse": 0.50, "max_abs": 0.50},
            {"rmse": 0.40, "max_abs": 0.40},
            {"rmse": 0.30, "max_abs": 0.30},
            {"rmse": 0.20, "max_abs": 0.20},
            {"rmse": 0.10, "max_abs": 0.10},
        ]

        def deterministic_assign(state_species, indices, counts, rng):
            expanded = []
            for species, count in sorted(counts.items()):
                expanded.extend([species] * count)
            for site_index, species in zip(indices, expanded):
                state_species[site_index] = species

        with patch("apex.core.lib.rss._assign_initial_species", side_effect=deterministic_assign):
            with patch("apex.core.lib.rss._pick_swap", side_effect=swap_sequence):
                with patch("apex.core.lib.rss._compute_warren_cowley_sro", return_value={}):
                    with patch("apex.core.lib.rss._objective_function", return_value=0.0):
                        with patch("apex.core.lib.rss._sro_gap_metrics", side_effect=rmse_sequence):
                            outputs, meta = generate_rss(
                                structure=st,
                                compositions={"all": {"Co": 0.5, "Ni": 0.5}},
                                shell_cutoffs=[2.8],
                                max_steps=len(swap_sequence),
                                interval=1,
                                num_configs=2,
                                tol=0.05,
                                return_metadata=True,
        )

        self.assertEqual(len(outputs), 2)
        self.assertEqual(meta["sampling"]["sampled_steps"], [4, -1])
        self.assertEqual(meta["sampling"]["sampled_rmses"], [0.10, 0.50])

    def test_invalid_structure_type_raises(self):
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure="not-a-structure",
                compositions={"all": {"Ni": 1.0}},
            )

    def test_invalid_sampling_and_thermo_parameters_raise(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 1, 1])

        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                max_steps=-1,
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                temperature=-0.1,
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                num_configs=0,
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                interval=0,
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                patience=0,
            )

    def test_shell_cutoff_and_weight_validation_raises(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 1, 1])

        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[],
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[2.0, 1.0],
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[2.0],
                shell_weights=[1.0, 1.0],
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[2.0],
                shell_weights=[-1.0],
            )

    def test_sro_target_validation_raises(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 1, 1])

        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[2.0],
                sro_targets={2: {"Ni-Ni": 0.0}},
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[2.0],
                sro_targets={0: 1.0},
            )
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[2.0],
                sro_targets={0: {"bad-pair-key-format-1-2": 0.0}},
            )

    def test_colon_pair_key_and_shell_string_work(self):
        st = fcc("Ni", a=3.6)
        st.make_supercell([2, 1, 1])

        out, meta = generate_rss(
            structure=st,
            compositions={"all": {"Co": 0.5, "Ni": 0.5}},
            shell_cutoffs=[2.8],
            sro_targets={"shell0": {"Co:Ni": 0.0}},
            max_steps=200,
            seed=5,
            return_metadata=True,
        )

        self.assertEqual(len(out), len(st))
        self.assertIn(("Co", "Ni"), meta["target_sro"][0])

    def test_multisublattice_requires_sublattices_mapping(self):
        st = Structure(
            Lattice.cubic(4.2),
            ["Na", "O"],
            [[0, 0, 0], [0.5, 0.5, 0.5]],
        )
        st.make_supercell([2, 1, 1])

        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"cation": {"Co": 1.0}, "anion": {"O": 1.0}},
                sublattices=None,
                shell_cutoffs=[4.3],
            )

    def test_invalid_sublattice_definition_raises(self):
        st = Structure(
            Lattice.cubic(4.2),
            ["Na", "O"],
            [[0, 0, 0], [0.5, 0.5, 0.5]],
        )
        st.make_supercell([2, 1, 1])

        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"cation": {"Co": 1.0}, "anion": {"O": 1.0}},
                sublattices=[
                    {"name": "cation", "site_indices": [0, 1]},
                    {"name": "anion", "site_indices": [1, 2]},
                ],
                shell_cutoffs=[4.3],
            )

    def test_allow_vacancies_maps_aliases_to_x(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 2, 1])

        out = generate_rss(
            structure=st,
            compositions={"all": {"Ni": 0.5, "vac": 0.5}},
            shell_cutoffs=[2.6],
            allow_vacancies=True,
            seed=2,
            max_steps=100,
        )
        species = {str(site.specie) for site in out.sites}
        self.assertTrue(any(sp.startswith("X") for sp in species))

    def test_show_progress_without_tqdm_warns(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 1, 1])

        with patch("apex.core.lib.rss.tqdm", None):
            with self.assertWarns(UserWarning):
                generate_rss(
                    structure=st,
                    compositions={"all": {"Fe": 1.0}},
                    shell_cutoffs=[2.6],
                    show_progress=True,
                    max_steps=10,
                )

    def test_default_sro_targets_and_zero_step_num_configs_are_distinct(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 2, 1])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            outputs, meta = generate_rss(
                structure=st,
                compositions={"all": {"Co": 0.5, "Ni": 0.5}},
                shell_cutoffs=[2.6],
                max_steps=0,
                seed=19,
                num_configs=3,
                return_metadata=True,
            )

        self.assertEqual(len(outputs), 3)
        self.assertEqual(
            set(meta["target_sro"][0]),
            {("Co", "Co"), ("Co", "Ni"), ("Ni", "Ni")},
        )
        self.assertTrue(
            all(value == 0.0 for value in meta["target_sro"][0].values())
        )
        species_signatures = {
            tuple(str(site.specie) for site in structure.sites)
            for structure in outputs
        }
        self.assertEqual(len(species_signatures), len(outputs))
        self.assertEqual(meta["sampling"]["sampled_steps"], [0, 0, 0])
        self.assertEqual(meta["attempted_moves"], 0)
        self.assertEqual(meta["accepted_moves"], 0)


class TestRSSRunner(unittest.TestCase):
    def test_run_rss_config_with_parent_lattice_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "parent_lattice": {
                    "type": "fcc",
                    "element": "Ni",
                    "a": 3.6,
                    "supercell": [2, 1, 1],
                },
                "compositions": {
                    "all": {
                        "Co": 0.5,
                        "Ni": 0.5,
                    }
                },
                "shell_cutoffs": [2.8],
                "num_configs": 2,
                "interval": 10,
                "max_steps": 100,
                "metadata": True,
                "seed": 1,
                "output_structure": "RSS",
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            run_rss_config(str(config_path))

            self.assertTrue((root / "RSS" / "conf_001" / "POSCAR").exists())
            self.assertTrue((root / "RSS" / "conf_002" / "POSCAR").exists())
            self.assertTrue((root / "RSS" / "rss_metadata.json").exists())

    def test_run_rss_config_with_parent_structure_and_no_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = fcc("Ni", a=3.6)
            parent.make_supercell([2, 1, 1])
            parent_path = root / "parent.vasp"
            Poscar(parent).write_file(str(parent_path))

            cfg = {
                "parent_structure": "parent.vasp",
                "compositions": {"all": {"Co": 0.5, "Ni": 0.5}},
                "shell_cutoffs": [2.8],
                "max_steps": 100,
                "metadata": False,
                "output_structure": "RSS",
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            run_rss_config(str(config_path))

            self.assertTrue((root / "RSS" / "conf_001" / "POSCAR").exists())
            self.assertFalse((root / "RSS" / "rss_metadata.json").exists())

    def test_run_rss_config_auto_assign_and_sublattice_sro_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = Structure(
                Lattice.cubic(4.2),
                ["Na", "O"],
                [[0, 0, 0], [0.5, 0.5, 0.5]],
            )
            parent_path = root / "parent.vasp"
            Poscar(parent).write_file(str(parent_path))

            cfg = {
                "parent_structure": "parent.vasp",
                "supercell": [2, 2, 1],
                "compositions": {
                    "cation": {"Mg": 0.5, "Co": 0.5},
                    "anion": {"O": 1.0},
                },
                "sro_targets": {"shell0": {"Mg-Mg": 0.0, "Mg-Co": 0.0, "Co-Co": 0.0, "O-O": 0.0}},
                "shell_cutoffs": [4.3],
                "max_steps": 100,
                "metadata": True,
                "output_structure": "RSS",
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            run_rss_config(str(config_path))

            metadata_path = root / "RSS" / "rss_metadata.json"
            self.assertTrue(metadata_path.exists())
            data = json.loads(metadata_path.read_text())
            self.assertIn("0", data["target_sro"])
            self.assertIn("Co-Mg", data["target_sro"]["0"])

    def test_run_rss_config_parent_lattice_b2_auto_supercell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "parent_lattice": {
                    "type": "B2",
                    "a": "auto",
                    "supercell": "auto",
                },
                "compositions": {
                    "corner": {
                        "Al": 0.25,
                        "Co": 0.25,
                        "Cr": 0.25,
                        "Fe": 0.25,
                    },
                    "body": {
                        "Al": 0.25,
                        "Co": 0.25,
                        "Cr": 0.25,
                        "Fe": 0.25,
                    },
                },
                "composition_tolerance": 0.001,
                "supercell_shape": "near_cubic",
                "maxmium_nums_atoms": 128,
                "shell_cutoffs": [4.0],
                "num_configs": 1,
                "max_steps": 0,
                "metadata": True,
                "show_progress": False,
                "output_structure": "RSS_B2",
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            run_rss_config(str(config_path))

            self.assertTrue((root / "RSS_B2" / "conf_001" / "POSCAR").exists())
            metadata = json.loads((root / "RSS_B2" / "rss_metadata.json").read_text())
            self.assertIn("composition_ratios", metadata)
            self.assertIsNone(metadata["ratio_precision"])
            self.assertAlmostEqual(metadata["composition_ratios"]["corner"]["Al"], 0.25)
            self.assertAlmostEqual(metadata["composition_ratios"]["body"]["Co"], 0.25)
    
    def test_run_rss_config_bcc_auto_supercell_respects_target_atom_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "parent_lattice": {
                    "type": "bcc",
                    "a": "auto",
                    "supercell": "auto",
                },
                "composition_tolerance": 0.01,
                "supercell_shape": "near_cubic",
                "maximum_num_atoms": 198,
                "compositions": {
                    "all": {
                        "Ni": 0.21212121212121213,
                        "Al": 0.24242424242424243,
                        "Co": 0.2727272727272727,
                        "Cr": 0.05555555555555555,
                        "Fe": 0.06565656565656566,
                        "Mn": 0.15151515151515152,
                    }
                },
                "shell_cutoffs": [2.6],
                "max_steps": 0,
                "num_configs": 1,
                "metadata": True,
                "show_progress": False,
                "output_structure": "RSS_BCC",
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            run_rss_config(str(config_path))

            metadata = json.loads((root / "RSS_BCC" / "rss_metadata.json").read_text())
            counts = metadata["composition_counts"]["all"]
            self.assertEqual(counts["Ni"], 42)
            self.assertEqual(counts["Al"], 48)
            self.assertEqual(counts["Co"], 54)
            self.assertEqual(counts["Cr"], 11)
            self.assertEqual(counts["Fe"], 13)
            self.assertEqual(counts["Mn"], 30)

    def test_run_rss_config_rejects_legacy_sro_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = fcc("Ni", a=3.6)
            parent.make_supercell([2, 1, 1])
            parent_path = root / "parent.vasp"
            Poscar(parent).write_file(str(parent_path))

            cfg = {
                "parent_structure": "parent.vasp",
                "compositions": {"all": {"Co": 0.5, "Ni": 0.5}},
                "shell_cutoffs": [2.8],
                "sro_targets": {
                    "cation": {"Co-Ni": 0.0},
                    "anion": {"O-O": 0.0},
                },
                "max_steps": 50,
                "output_structure": "RSS/POSCAR",
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            with self.assertRaises((ValueError, RSSInputError)):
                run_rss_config(str(config_path))

    def test_run_rss_config_requires_parent_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "compositions": {"all": {"Ni": 1.0}},
                "shell_cutoffs": [2.8],
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            with self.assertRaises(ValueError):
                run_rss_config(str(config_path))

    def test_run_rss_config_invalid_parent_lattice_type_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "parent_lattice": {"type": "unknown", "element": "Ni", "a": 3.6},
                "compositions": {"all": {"Ni": 1.0}},
                "shell_cutoffs": [2.8],
            }
            config_path = root / "rss.json"
            config_path.write_text(json.dumps(cfg, indent=2))

            with self.assertRaises(ValueError):
                run_rss_config(str(config_path))

    def test_run_rss_config_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            run_rss_config("/tmp/this_file_does_not_exist_rss.json")

    def test_rss_from_args_delegates_to_runner(self):
        with patch("apex.rss.run_rss_config") as mocked:
            rss_from_args("dummy.json")
            mocked.assert_called_once_with("dummy.json")

    def test_auto_assign_sublattices_helper_branches(self):
        base_parent = Structure(Lattice.cubic(3.0), ["Na", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]])
        expanded_parent = base_parent.copy()
        expanded_parent.make_supercell([2, 1, 1])

        self.assertIsNone(
            _auto_assign_sublattices(
                compositions={"all": {"Na": 1.0}},
                base_parent=base_parent,
                expanded_parent=expanded_parent,
            )
        )

        with self.assertRaises(ValueError):
            _auto_assign_sublattices(
                compositions={},
                base_parent=base_parent,
                expanded_parent=expanded_parent,
            )
        with self.assertRaises(ValueError):
            _auto_assign_sublattices(
                compositions={"a": {"Na": 1.0}, "b": {"Cl": 1.0}, "c": {"X": 1.0}},
                base_parent=base_parent,
                expanded_parent=expanded_parent,
            )
        with self.assertRaises(ValueError):
            _auto_assign_sublattices(
                compositions={"a": {"Na": 1.0}},
                base_parent=base_parent,
                expanded_parent=base_parent,
            )

        invalid_expanded = Structure(
            Lattice.cubic(3.0),
            ["Na", "Cl", "Na"],
            [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]],
        )
        with self.assertRaises(ValueError):
            _auto_assign_sublattices(
                compositions={"a": {"Na": 1.0}, "b": {"Cl": 1.0}},
                base_parent=base_parent,
                expanded_parent=invalid_expanded,
            )

class TestRSSInternalHelpers(unittest.TestCase):
    def test_parse_pair_key_and_invalid_key(self):
        self.assertEqual(_parse_pair_key("Ni-Co"), ("Co", "Ni"))
        self.assertEqual(_parse_pair_key("Ni:Co"), ("Co", "Ni"))
        self.assertEqual(_parse_pair_key(["Ni", "Co"]), ("Co", "Ni"))
        with self.assertRaises(RSSInputError):
            _parse_pair_key("Ni")

    def test_normalize_validate_compositions_branches(self):
        with self.assertRaises(RSSInputError):
            _normalize_validate_compositions(compositions=[], tol=1e-3, allow_vacancies=False)
        with self.assertRaises(RSSInputError):
            _normalize_validate_compositions(compositions={"all": []}, tol=1e-3, allow_vacancies=False)
        with self.assertRaises(RSSInputError):
            _normalize_validate_compositions(compositions={"all": {"Ni": "x"}}, tol=1e-3, allow_vacancies=False)
        with self.assertRaises(RSSInputError):
            _normalize_validate_compositions(compositions={"all": {"Ni": -0.1}}, tol=1e-3, allow_vacancies=False)
        with self.assertRaises(RSSInputError):
            _normalize_validate_compositions(compositions={"all": {"Ni": 0.0}}, tol=1e-3, allow_vacancies=False)
        with self.assertRaises(RSSInputError):
            _normalize_validate_compositions(compositions={"all": {"vac": 1.0}}, tol=1e-3, allow_vacancies=False)

    def test_build_sublattice_indices_validation_branches(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 1, 1])

        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(st, {"all": {"Fe": 1.0}}, sublattices={})
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(st, {"all": {"Fe": 1.0}}, sublattices=[1])
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(st, {"all": {"Fe": 1.0}}, sublattices=[{"name": "all"}])
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(
                st,
                {"all": {"Fe": 1.0}},
                sublattices=[
                    {"name": "all", "site_indices": [0]},
                    {"name": "all", "site_indices": [1]},
                ],
            )
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(
                st,
                {"all": {"Fe": 1.0}},
                sublattices=[{"name": "all", "site_indices": []}],
            )
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(
                st,
                {"all": {"Fe": 1.0}},
                sublattices=[{"name": "all", "site_indices": ["a"]}],
            )
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(
                st,
                {"all": {"Fe": 1.0}},
                sublattices=[{"name": "all", "site_indices": [99]}],
            )
        with self.assertRaises(RSSInputError):
            _build_sublattice_indices(
                st,
                {"all": {"Fe": 1.0}, "other": {"Co": 1.0}},
                sublattices=[{"name": "all", "site_indices": [0, 1]}],
            )

    def test_integerize_and_cutoff_neighbor_validation_branches(self):
        with self.assertWarns(UserWarning):
            with self.assertRaises(RSSInputError):
                _integerize_composition_counts(1, {"A": 1.0, "B": 1.0}, 1e-6)

        single_site = Structure(Lattice.cubic(3.0), ["Ni"], [[0, 0, 0]])
        with self.assertRaises(RSSInputError):
            _default_shell_cutoffs(single_site)

        with self.assertRaises(RSSInputError):
            _build_neighbor_shells(single_site, [0.0])

    def test_assign_initial_species_mismatch_raises(self):
        with self.assertRaises(RSSInputError):
            _assign_initial_species(
                state_species=["Ni", "Ni"],
                site_indices=[0, 1],
                counts={"Co": 1},
                rng=random.Random(1),
            )

    def test_default_shell_cutoff_skips_zero_distances(self):
        st = Structure(
            Lattice.cubic(3.0),
            ["Ni", "Ni", "Ni"],
            [[0, 0, 0], [0, 0, 0], [0.5, 0.5, 0.5]],
        )
        cut = _default_shell_cutoffs(st)
        self.assertEqual(len(cut), 1)
        self.assertGreater(cut[0], 0.0)

    def test_normalize_sro_targets_numeric_shell_key_and_invalid_target_type(self):
        pair_keys = [("A", "A")]
        normalized = _normalize_sro_targets({"0": {"A-A": 0.1}}, 1, pair_keys)
        self.assertIn(("A", "A"), normalized[0])

        enriched = _normalize_sro_targets({"0": {"A-B": 0.2}}, 1, pair_keys)
        self.assertIn(("A", "B"), enriched[0])

        with self.assertRaisesRegex(
            RSSInputError,
            r"Configured shell_cutoffs define 1 shell\(s\), so valid keys are: shell0",
        ):
            _normalize_sro_targets({"shell3": {"A-A": 0.1}}, 1, pair_keys)

        with self.assertRaises(RSSInputError):
            _normalize_sro_targets({"shell0": 1.0}, 1, pair_keys)

        with self.assertRaisesRegex(
            RSSInputError,
            r"expected shell0, shell1, \.\.\. or 0, 1, \.\.\.",
        ):
            _normalize_sro_targets({"shellA": {"A-A": 0.1}}, 1, pair_keys)

    def test_normalize_shell_weights_valid_values(self):
        weights = _normalize_shell_weights([1.0, 2.0], [0.5, 1.5])
        self.assertEqual(weights[0], 0.5)
        self.assertEqual(weights[1], 1.5)

        with self.assertRaises(RSSInputError):
            _normalize_shell_weights([1.0], [1.0, 2.0])
        with self.assertRaises(RSSInputError):
            _normalize_shell_weights([1.0], [-0.1])

    def test_compute_warren_cowley_handles_missing_species(self):
        achieved = _compute_warren_cowley_sro(
            species_state=["A", "A"],
            shell_pairs={0: [(0, 1)]},
            pair_keys=[("B", "B")],
            composition_fractions={"A": 1.0},
        )
        self.assertEqual(achieved[0][("B", "B")], 0.0)

    def test_pick_swap_no_valid_names_and_same_species(self):
        self.assertIsNone(_pick_swap(["A"], {"all": [0]}, random.Random(1)))
        self.assertIsNone(_pick_swap(["A", "A"], {"all": [0, 1]}, random.Random(1), max_tries=3))

    def test_generate_rss_no_neighbor_pairs_raises(self):
        st = Structure(Lattice.cubic(20.0), ["Ni", "Ni"], [[0, 0, 0], [0.5, 0.5, 0.5]])
        with self.assertRaises(RSSInputError):
            generate_rss(
                structure=st,
                compositions={"all": {"Ni": 1.0}},
                shell_cutoffs=[1.0],
            )

    def test_generate_rss_shell_cutoff_default_and_patience_break(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 1, 1])

        with patch("apex.core.lib.rss._objective_function", return_value=1.0):
            _, meta = generate_rss(
                structure=st,
                compositions={"all": {"Co": 0.5, "Ni": 0.5}},
                shell_cutoffs=None,
                max_steps=10,
                patience=1,
                seed=3,
                return_metadata=True,
            )
        self.assertEqual(meta["sampling"]["patience"], 1)

    def test_generate_rss_progress_bar_tqdm_branch(self):
        st = bcc("Fe", a=2.85)
        st.make_supercell([2, 1, 1])

        class _DummyTqdm:
            def __init__(self, *args, **kwargs):
                self.updated = 0

            def set_postfix(self, *args, **kwargs):
                return None

            def update(self, n):
                self.updated += n

            def close(self):
                return None

        with patch("apex.core.lib.rss.tqdm", _DummyTqdm):
            out = generate_rss(
                structure=st,
                compositions={"all": {"Fe": 1.0}},
                shell_cutoffs=[2.6],
                show_progress=True,
                max_steps=5,
            )
        self.assertEqual(len(out), len(st))


if __name__ == "__main__":
    unittest.main()
