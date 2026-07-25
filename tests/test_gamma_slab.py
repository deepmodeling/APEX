import math

import numpy as np
import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.core.surface import SlabGenerator

from apex.core.property.gamma_slab import get_first_gamma_slab
from apex.core.property.gamma_slab import make_gamma_slab_generator
from apex.core.property.gamma_slab import validate_gamma_slab_settings
from apex.core.property.gamma_slab import validate_generated_gamma_slab


def rounding_regression_structure():
    return Structure(
        Lattice(
            [
                [4.48871691776465, 0.0, 0.0],
                [0.9779202953637698, 2.861234792942396, 0.0],
                [-0.6795759322843109, 0.22507920854606156, 2.1757680318455335],
            ]
        ),
        ["Al"],
        [[0.0, 0.0, 0.0]],
    )


def test_plane_units_prevent_floating_point_layer_promotion():
    structure = rounding_regression_structure()
    miller = (1, 0, 1)
    d_hkl = structure.lattice.d_hkl(miller)

    legacy = SlabGenerator(
        structure,
        miller_index=miller,
        min_slab_size=d_hkl * 2,
        min_vacuum_size=0,
        center_slab=True,
        in_unit_planes=False,
        lll_reduce=True,
        reorient_lattice=False,
        primitive=False,
    )
    assert d_hkl * 2 / legacy._proj_height == 2.0000000000000004
    assert math.ceil(d_hkl * 2 / legacy._proj_height) == 3
    assert len(legacy.get_slab(shift=0)) == 3

    protected, metadata = make_gamma_slab_generator(
        structure, miller, plane_target=2, min_slab_height=None
    )
    slab = protected.get_slab(shift=0)

    assert len(slab) == 2
    assert metadata["oriented_cell_repeats"] == 2
    assert metadata["expected_base_atoms"] == 2


def test_minimum_height_promotes_only_to_required_repeat():
    structure = rounding_regression_structure()
    miller = (1, 0, 1)
    initial, _ = make_gamma_slab_generator(
        structure, miller, plane_target=1, min_slab_height=None
    )
    target_height = initial._proj_height + 1.0e-6

    protected, metadata = make_gamma_slab_generator(
        structure,
        miller,
        plane_target=1,
        min_slab_height=target_height,
    )

    assert metadata["oriented_cell_repeats"] == 2
    assert metadata["slab_height"] >= target_height
    assert len(protected.get_slab(shift=0)) == 2


def test_first_termination_matches_pymatgen_without_materializing_all():
    structure = Structure(
        Lattice.cubic(4.0),
        ["Al", "Al"],
        [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
    )
    generator = SlabGenerator(
        structure,
        miller_index=(1, 0, 0),
        min_slab_size=2,
        min_vacuum_size=0,
        center_slab=True,
        in_unit_planes=True,
        lll_reduce=True,
        reorient_lattice=False,
        primitive=False,
    )

    expected = generator.get_slabs(ftol=0.001)[0]
    actual = get_first_gamma_slab(generator, ftol=0.001)

    np.testing.assert_allclose(actual.lattice.matrix, expected.lattice.matrix)
    np.testing.assert_allclose(actual.frac_coords, expected.frac_coords)


def test_atom_count_and_overlap_guards_fail_clearly():
    structure = Structure(
        Lattice.cubic(3.0),
        ["Al", "Al"],
        [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0]],
    )
    metadata = {
        "expected_base_atoms": 2,
        "oriented_cell_repeats": 1,
    }

    with pytest.raises(RuntimeError, match="exceeding max_atoms"):
        validate_generated_gamma_slab(
            structure,
            metadata,
            inplane_size=(1, 1),
            max_atoms=1,
            min_distance=0,
            property_name="Gamma",
        )

    with pytest.raises(RuntimeError, match="overlapping atoms"):
        validate_generated_gamma_slab(
            structure,
            metadata,
            inplane_size=(1, 1),
            max_atoms=None,
            min_distance=0.2,
            property_name="Gamma",
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {"supercell_size": [1, 1], "min_slab_height": None,
             "max_atoms": None, "min_distance": 0.2},
            "three values",
        ),
        (
            {"supercell_size": [1, 1, 2], "min_slab_height": -1,
             "max_atoms": None, "min_distance": 0.2},
            "min_slab_height",
        ),
        (
            {"supercell_size": [1, 1, 2], "min_slab_height": None,
             "max_atoms": 0, "min_distance": 0.2},
            "max_atoms",
        ),
        (
            {"supercell_size": [1, 1, 2], "min_slab_height": None,
             "max_atoms": None, "min_distance": -0.1},
            "min_distance",
        ),
    ],
)
def test_invalid_gamma_slab_settings(kwargs, message):
    with pytest.raises(ValueError, match=message):
        validate_gamma_slab_settings(**kwargs)
