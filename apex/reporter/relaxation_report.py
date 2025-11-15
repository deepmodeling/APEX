import logging
import json
import numpy as np
import plotly.graph_objs as go
from dash import dash_table
import pandas as pd
from monty.json import MontyEncoder

from apex.core.lib.utils import round_format, round_2d_format

TABLE_WIDTH = '100%'
TABLE_MIN_WIDTH = '100%'


class RelaxationReport:
    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        conf_list = []
        equi_en = []
        cell_vec_a = []
        cell_vec_b = []
        cell_vec_c = []
        space_group_symbol = []
        space_group_number = []
        point_group_symbol = []
        crystal_system = []
        lattice_type = []
        for conf, dataset in res_data.items():
            try:
                class_data = dataset['relaxation']['result']
                struct_info = dataset['relaxation']['structure_info']
            except KeyError:
                pass
            else:
                data = json.dumps(class_data, cls=MontyEncoder, indent=4)
                data = json.loads(data)
                conf_list.append(conf)
                equi_en.append(data["data"]["energies"]["data"][-1])
                vec_a_length = np.linalg.norm(data["data"]["cells"]["data"][-1][0])
                vec_b_length = np.linalg.norm(data["data"]["cells"]["data"][-1][1])
                vec_c_length = np.linalg.norm(data["data"]["cells"]["data"][-1][2])
                cell_vec_a.append(vec_a_length)
                cell_vec_b.append(vec_b_length)
                cell_vec_c.append(vec_c_length)
                space_group_symbol.append(struct_info["space_group_symbol"])
                space_group_number.append(struct_info["space_group_number"])
                point_group_symbol.append(struct_info["point_group_symbol"])
                crystal_system.append(struct_info["crystal_system"])
                lattice_type.append(struct_info["lattice_type"])

        # round numbers in table
        # rounded_tensor = round_2d_format(data, decimal)

        df = pd.DataFrame({
            "Conf": conf_list,
            "Equi E (eV)": equi_en,
            "Cell Vector length a (Å)": cell_vec_a,
            "Cell Vector length b (Å)": cell_vec_b,
            "Cell Vector length c (Å)": cell_vec_c,
            "Space Group Symbol": space_group_symbol,
            "Space Group Number": space_group_number,
            "Point Group Symbol": point_group_symbol,
            "Crystal System": crystal_system,
            "Lattice Type": lattice_type,
        })

        table = dash_table.DataTable(
            data=df.to_dict('records'),
            columns=[{'name': i, 'id': i} for i in df.columns],
            style_table={'width': TABLE_WIDTH,
                         'minWidth': TABLE_MIN_WIDTH,
                         'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'width': '150px'}
        )

        return table
