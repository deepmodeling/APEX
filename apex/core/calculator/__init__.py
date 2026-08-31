LAMMPS_INTER_TYPE = [
    'deepmd',
    'eam_alloy',
    'eam_fs',
    'meam',
    'meam_spline',
    'snap',
    'gap',
    'rann',
    'mace',
    'nep'
]


def lammps_model_files_for_cleanup(inter_param):
    """Return staged model paths that APEX may remove after retrieval.

    Image-resident models are immutable runtime assets rather than staged task
    files.  Never turn their absolute paths into cleanup commands.
    """
    if inter_param.get("model_in_image") is True:
        return []
    model = inter_param.get("model")
    if isinstance(model, str):
        return [model]
    if isinstance(model, list):
        return list(model)
    return []
