import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest

from apex.core.lib import mfp_eosfit


def test_eos_lists_include_expected_models():
    eos_names = mfp_eosfit.get_eos_list()

    assert "birch" in eos_names
    assert "murnaghan" in eos_names
    assert "BM5" in eos_names
    assert "morse_6p" in eos_names
    assert mfp_eosfit.__version__() == "1.2.5"


@pytest.mark.parametrize(
    "model",
    [
        mfp_eosfit.murnaghan,
        mfp_eosfit.birch,
        mfp_eosfit.BM4,
        mfp_eosfit.rBM4,
        mfp_eosfit.LOG4,
        mfp_eosfit.rPT4,
        mfp_eosfit.vinet,
        mfp_eosfit.universal,
    ],
)
def test_four_parameter_eos_models_equal_e0_at_v0(model):
    pars = [-3.0, 0.5, 4.0, 10.0]

    assert model(10.0, pars) == pytest.approx(-3.0)


def test_birch_residual_is_zero_for_matching_data():
    pars = [-3.0, 0.5, 4.0, 10.0]
    volumes = np.array([9.5, 10.0, 10.5])
    energies = mfp_eosfit.birch(volumes, pars)

    np.testing.assert_allclose(mfp_eosfit.res_birch(pars, energies, volumes), 0.0)


@pytest.mark.parametrize(
    ("model", "residual"),
    [
        (mfp_eosfit.murnaghan, mfp_eosfit.res_murnaghan),
        (mfp_eosfit.mBM4, mfp_eosfit.res_mBM4),
        (mfp_eosfit.BM4, mfp_eosfit.res_BM4),
        (mfp_eosfit.rBM4, mfp_eosfit.res_rBM4),
        (mfp_eosfit.universal, mfp_eosfit.res_universal),
        (mfp_eosfit.LOG4, mfp_eosfit.res_LOG4),
        (mfp_eosfit.rPT4, mfp_eosfit.res_rPT4),
        (mfp_eosfit.vinet, mfp_eosfit.res_vinet),
        (mfp_eosfit.Li4p, mfp_eosfit.res_Li4p),
    ],
)
def test_four_parameter_residual_wrappers_zero_for_matching_data(model, residual):
    pars = [-3.0, 0.5, 4.0, 10.0]
    volumes = np.array([9.5, 10.0, 10.5])
    energies = model(volumes, pars)

    np.testing.assert_allclose(residual(pars, energies, volumes), 0.0)


@pytest.mark.parametrize(
    "model",
    [
        mfp_eosfit.mBM5,
        mfp_eosfit.BM5,
        mfp_eosfit.rBM5,
        mfp_eosfit.LOG5,
        mfp_eosfit.rPT5,
    ],
)
def test_five_parameter_eos_models_equal_e0_at_v0(model):
    pars = [-3.0, 0.5, 4.0, 10.0, 0.01]

    assert model(10.0, pars) == pytest.approx(-3.0)


def test_eos_property_calculators_return_expected_shapes():
    pars4 = [-3.0, 0.5, 4.0, 10.0]
    pars5 = [-3.0, 0.5, 4.0, 10.0, 0.01]

    assert mfp_eosfit.calc_props_mBM4(pars4)[:4] == pars4
    assert mfp_eosfit.calc_props_BM4(pars4)[:4] == pars4
    assert mfp_eosfit.calc_props_LOG4(pars4)[:4] == pars4
    assert mfp_eosfit.calc_props_vinet(pars4)[:4] == pars4
    assert len(mfp_eosfit.calc_props_SJX_5p(pars5)) == 5


@pytest.mark.parametrize(
    ("model", "residual", "pars"),
    [
        (mfp_eosfit.morse, mfp_eosfit.res_morse, [-3.0, 0.5, 4.0, 10.0]),
        (mfp_eosfit.morse_AB, mfp_eosfit.res_morse_AB, [-3.0, 0.5, 1.0, 10.0]),
        (mfp_eosfit.morse_3p, mfp_eosfit.res_morse_3p, [-3.0, 0.5, 10.0]),
        (mfp_eosfit.mie, mfp_eosfit.res_mie, [-3.0, 0.5, 4.0, 10.0]),
        (mfp_eosfit.mie_simple, mfp_eosfit.res_mie_simple, [-3.0, 0.5, 10.0, 2.0]),
    ],
)
def test_other_eos_residual_wrappers_zero_for_matching_data(model, residual, pars):
    volumes = np.array([9.5, 10.0, 10.5])
    energies = model(volumes, pars)

    np.testing.assert_allclose(residual(pars, energies, volumes), 0.0)


def test_read_ve_and_init_guess_from_data(tmp_path):
    ve_file = tmp_path / "ve.dat"
    ve_file.write_text("\n9.0 -2.9 extra\n10.0 -3.0\n11.0 -2.9\n")

    volumes, energies = mfp_eosfit.read_ve(str(ve_file))
    guess = mfp_eosfit.init_guess_from_data(volumes, energies)

    assert volumes == [9.0, 10.0, 11.0]
    assert energies == [-2.9, -3.0, -2.9]
    assert guess[0] == pytest.approx(-3.0)
    assert guess[3] == pytest.approx(10.0)


def test_read_velp_and_vlp_parse_selected_ranges(tmp_path):
    velp_file = tmp_path / "velp.dat"
    velp_file.write_text(
        "9.0 -2.9 1.0 2.0 3.0 90.0 91.0\n"
        "10.0 -3.0 1.1 2.1 3.1 90.1 91.1\n"
        "11.0 -2.9 1.2 2.2 3.2 90.2 91.2\n"
    )
    vlp_file = tmp_path / "vlp.dat"
    vlp_file.write_text(
        "9.0 1.0 2.0 3.0 90.0 91.0\n"
        "10.0 1.1 2.1 3.1 90.1 91.1\n"
        "11.0 1.2 2.2 3.2 90.2 91.2\n"
    )

    vol, eng, cella, cellb, cellc, cellba, cellca = mfp_eosfit.read_velp(
        str(velp_file), 2, 3
    )
    vlp_vol, vlp_a, vlp_b, vlp_c, vlp_ba, vlp_ca = mfp_eosfit.read_vlp(
        str(vlp_file), 2, 3
    )

    assert vol == [10.0, 11.0]
    assert eng == [-3.0, -2.9]
    assert cella == [1.1, 1.2]
    assert cellb == [2.1, 2.2]
    assert cellc == [3.1, 3.2]
    assert cellba == [90.1, 90.2]
    assert cellca == [91.1, 91.2]
    assert vlp_vol == [9.0, 10.0, 11.0]
    assert vlp_a == [1.0, 1.1, 1.2]
    assert vlp_b == [2.0, 2.1, 2.2]
    assert vlp_c == [3.0, 3.1, 3.2]
    assert vlp_ba == [90.0, 90.1, 90.2]
    assert vlp_ca == [91.0, 91.1, 91.2]


def test_fit_birch_murnaghan_free_and_fixed_bp_with_synthetic_data():
    true_pars = [-3.0, 0.4, 4.0, 10.0]
    volumes = np.array([8.8, 9.2, 9.6, 10.0, 10.4, 10.8, 11.2])
    energies = mfp_eosfit.birch(volumes, true_pars)

    free_fit = mfp_eosfit.fit_birch_murnaghan(volumes[::-1], energies[::-1])
    fixed_fit = mfp_eosfit.fit_birch_murnaghan(volumes, energies, fixed_bp=4.0)

    assert free_fit["fit_variant"] == "free_bp"
    assert free_fit["used_point_count"] == len(volumes)
    assert free_fit["E0_eV"] == pytest.approx(true_pars[0], abs=1e-8)
    assert free_fit["B0_eV_per_A3"] == pytest.approx(true_pars[1], rel=1e-7)
    assert free_fit["B0_prime"] == pytest.approx(true_pars[2], rel=1e-7)
    assert free_fit["V0_A3"] == pytest.approx(true_pars[3], rel=1e-8)
    assert free_fit["K_T_GPa"] == pytest.approx(true_pars[1] * mfp_eosfit.eV2GPa)
    assert free_fit["residual_sum_squares"] == pytest.approx(0.0, abs=1e-20)

    assert fixed_fit["fit_variant"] == "fixed_bp"
    assert fixed_fit["fit_function"] == "birch_fixed_bp"
    assert fixed_fit["B0_prime"] == 4.0
    assert fixed_fit["E0_eV"] == pytest.approx(true_pars[0], abs=1e-8)
    assert fixed_fit["V0_A3"] == pytest.approx(true_pars[3], rel=1e-8)


def test_fit_birch_murnaghan_invalid_inputs_raise():
    with pytest.raises(ValueError, match="same shape"):
        mfp_eosfit.fit_birch_murnaghan([1, 2, 3], [1, 2])

    with pytest.raises(ValueError, match="at least 3"):
        mfp_eosfit.fit_birch_murnaghan([1, 2], [1, 2], fixed_bp=4.0)

    with pytest.raises(ValueError, match="at least 4"):
        mfp_eosfit.fit_birch_murnaghan([1, 2, 3], [1, 2, 3])

    with pytest.raises(ValueError, match="positive"):
        mfp_eosfit.fit_birch_murnaghan([0, 1, 2], [1, 2, 3], fixed_bp=4.0)

    with pytest.raises(ValueError, match="at least 3"):
        mfp_eosfit.init_guess_from_data([1, 2], [1, 2])


class TestMfpEosfitCoverage(unittest.TestCase):
    def test_eos_lists_include_expected_models(self):
        test_eos_lists_include_expected_models()

    def test_four_parameter_eos_models_equal_e0_at_v0(self):
        for model in [
            mfp_eosfit.murnaghan,
            mfp_eosfit.birch,
            mfp_eosfit.BM4,
            mfp_eosfit.rBM4,
            mfp_eosfit.LOG4,
            mfp_eosfit.rPT4,
            mfp_eosfit.vinet,
            mfp_eosfit.universal,
        ]:
            with self.subTest(model=model.__name__):
                test_four_parameter_eos_models_equal_e0_at_v0(model)

    def test_birch_residual_is_zero_for_matching_data(self):
        test_birch_residual_is_zero_for_matching_data()

    def test_four_parameter_residual_wrappers_zero_for_matching_data(self):
        for model, residual in [
            (mfp_eosfit.murnaghan, mfp_eosfit.res_murnaghan),
            (mfp_eosfit.mBM4, mfp_eosfit.res_mBM4),
            (mfp_eosfit.BM4, mfp_eosfit.res_BM4),
            (mfp_eosfit.rBM4, mfp_eosfit.res_rBM4),
            (mfp_eosfit.universal, mfp_eosfit.res_universal),
            (mfp_eosfit.LOG4, mfp_eosfit.res_LOG4),
            (mfp_eosfit.rPT4, mfp_eosfit.res_rPT4),
            (mfp_eosfit.vinet, mfp_eosfit.res_vinet),
            (mfp_eosfit.Li4p, mfp_eosfit.res_Li4p),
        ]:
            with self.subTest(model=model.__name__):
                test_four_parameter_residual_wrappers_zero_for_matching_data(
                    model, residual
                )

    def test_five_parameter_eos_models_equal_e0_at_v0(self):
        for model in [
            mfp_eosfit.mBM5,
            mfp_eosfit.BM5,
            mfp_eosfit.rBM5,
            mfp_eosfit.LOG5,
            mfp_eosfit.rPT5,
        ]:
            with self.subTest(model=model.__name__):
                test_five_parameter_eos_models_equal_e0_at_v0(model)

    def test_eos_property_calculators_return_expected_shapes(self):
        test_eos_property_calculators_return_expected_shapes()

    def test_other_eos_residual_wrappers_zero_for_matching_data(self):
        for model, residual, pars in [
            (mfp_eosfit.morse, mfp_eosfit.res_morse, [-3.0, 0.5, 4.0, 10.0]),
            (mfp_eosfit.morse_AB, mfp_eosfit.res_morse_AB, [-3.0, 0.5, 1.0, 10.0]),
            (mfp_eosfit.morse_3p, mfp_eosfit.res_morse_3p, [-3.0, 0.5, 10.0]),
            (mfp_eosfit.mie, mfp_eosfit.res_mie, [-3.0, 0.5, 4.0, 10.0]),
            (mfp_eosfit.mie_simple, mfp_eosfit.res_mie_simple, [-3.0, 0.5, 10.0, 2.0]),
        ]:
            with self.subTest(model=model.__name__):
                test_other_eos_residual_wrappers_zero_for_matching_data(
                    model, residual, pars
                )

    def test_read_ve_and_init_guess_from_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_read_ve_and_init_guess_from_data(Path(tmp))

    def test_read_velp_and_vlp_parse_selected_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_read_velp_and_vlp_parse_selected_ranges(Path(tmp))

    def test_fit_birch_murnaghan_free_and_fixed_bp_with_synthetic_data(self):
        test_fit_birch_murnaghan_free_and_fixed_bp_with_synthetic_data()

    def test_fit_birch_murnaghan_invalid_inputs_raise(self):
        test_fit_birch_murnaghan_invalid_inputs_raise()
