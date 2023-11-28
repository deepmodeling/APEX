import pandas as pd
import plotly.graph_objs as go
from dash import dash_table
from apex.core.property.Property import Property
from dflow.python import upload_packages
upload_packages.append(__file__)


class Relaxation(Property):
    """
    pseudo-property type for relaxation only for Dash table and Ploty graph for reporter
    """
    def __init__(self, parameter, inter_param=None):
        pass

    def make_confs(self, path_to_work, path_to_equi, refine=False):
        pass

    def post_process(self, task_list):
        pass

    def task_type(self):
        pass

    def task_param(self):
        pass

    def _compute_lower(self, output_file, all_tasks, all_res):
        pass

    @staticmethod
    def plotly_graph(res_data: dict, **kwargs) -> [go, go.layout]:
        vpa = []
        epa = []
        for k, v in res_data.items():
            vpa.append(k)
            epa.append(v)
        df = pd.DataFrame({
            "VpA(A^3)": vpa,
            "EpA(eV)": epa
        })
        trace = go.Scatter(
            x=df['VpA(A^3)'],
            y=df['EpA(eV)'],
            mode='lines',
        )
        layout = go.Layout(
            xaxis=dict(
                title_text="VpA(A^3)",
                title_font=dict(
                    family="Courier New, monospace",
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis=dict(
                title_text="EpA(eV)",
                title_font=dict(
                    family="Courier New, monospace",
                    size=18,
                    color="#7f7f7f"
                )
            )
        )

        return trace, layout

    @staticmethod
    def dash_table(res_data: dict, **kwargs) -> dash_table.DataTable:
        vpa = []
        epa = []
        for k, v in res_data.items():
            vpa.append(k)
            epa.append(v)
        df = pd.DataFrame({
            "VpA(A^3)": vpa,
            "EpA(eV)": epa
        })

        table = dash_table.DataTable(
            data=df.to_dict('records'),
            columns=[{'name': i, 'id': i} for i in df.columns],
            style_table={'width': '50%'},
            style_cell={'textAlign': 'center'}
        )

        return table
