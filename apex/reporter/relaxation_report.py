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
        for conf, dataset in res_data.items():
            try:
                class_data = dataset['relaxation']['result']
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


        # round numbers in table
        # rounded_tensor = round_2d_format(data, decimal)

        df = pd.DataFrame({
            "Conf": conf_list,
            "Equi E (eV)": equi_en,
            "Cell Vector length a (\AA)": cell_vec_a,
            "Cell Vector length b (\AA)": cell_vec_b,
            "Cell Vector length c (\AA)": cell_vec_c,
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
