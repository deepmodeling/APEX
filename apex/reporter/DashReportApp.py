import logging

import dash
from dash import dcc, html, State
from dash.dependencies import Input, Output
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
import plotly.graph_objects as go
import webbrowser
from threading import Timer
from .relaxation_report import RelaxationReport
from .property_report import *


NO_GRAPH_LIST = ['relaxation']


def return_prop_class(prop_type: str):
    if prop_type == 'eos':
        return EOSReport
    elif prop_type == 'elastic':
        return ElasticReport
    elif prop_type == 'surface':
        return SurfaceReport
    elif prop_type == 'interstitial':
        return InterstitialReport
    elif prop_type == 'vacancy':
        return VacancyReport
    elif prop_type == 'gamma':
        return GammaReport
    elif prop_type == 'phonon':
        return PhononReport


def generate_test_datasets():
    datasets = {
        '/Users/zhuoyuan/labspace/ti-mo_test/Ti_test/DP_test': {
            'confs/std-hcp': {
                'eos_00': {
                    "14.743452666313036": -7.6612955,
                    "15.610714587860862": -7.7632485,
                    "16.477976509408688": -7.817405,
                    "17.345238430956513": -7.8335905,
                    "18.21250035250434": -7.8194775,
                    "19.079762274052165": -7.7812295,
                }
            }
        }
    }
    return datasets


class DashReportApp:
    def __init__(self, datasets):
        self.datasets = datasets
        self.all_dimensions = set()
        self.all_datasets = set()
        self.app = dash.Dash(
            __name__,
            suppress_callback_exceptions=True,
            external_stylesheets=[dbc.themes.MATERIA]
        )
        # load_figure_template("materia")
        self.app.layout = self.generate_layout()

        """define callbacks"""
        self.app.callback(
            Output('graph', 'style'),
            [Input('props-dropdown', 'value'),
             Input('confs-radio', 'value')]
        )(self.update_graph_visibility)

        self.app.callback(
            Output('graph', 'figure'),
            [Input('props-dropdown', 'value'),
             Input('confs-radio', 'value')]
        )(self.update_graph)

        self.app.callback(
            Output('table', 'children'),
            [Input('props-dropdown', 'value'),
             Input('confs-radio', 'value')]
        )(self.update_table)

    @staticmethod
    def return_prop_type(prop: str) -> str:
        prop_type = prop.split('_')[0]
        return prop_type

    def generate_layout(self):
        for w in self.datasets.values():
            self.all_dimensions.update(w.keys())
            for dimension in w.values():
                self.all_datasets.update(dimension.keys())

        default_dataset = list(self.all_datasets)[0] if self.all_datasets else None
        default_dimension = list(self.all_dimensions)[0] if self.all_dimensions else None

        layout = html.Div(
            [
                html.H2("APEX Results Visualization Report"),
                html.Label('Configuration:', style={'font-weight': 'bold'}),
                dcc.RadioItems(
                    id='confs-radio',
                    options=[{'label': name, 'value': name} for name in self.all_dimensions],
                    value=default_dimension
                ),
                html.Br(),
                html.Label('Property:', style={'font-weight': 'bold'}),
                dcc.Dropdown(
                    id='props-dropdown',
                    options=[{'label': name, 'value': name} for name in self.all_datasets],
                    value=default_dataset
                ),
                html.Br(),
                dcc.Graph(id='graph', style={'display': 'block'}, className='graph-container'),
                html.Div(id='table')
            ]
        )
        return layout

    def update_graph_visibility(self, selected_prop, selected_confs):
        prop_type = DashReportApp.return_prop_type(selected_prop)
        valid_count = 0
        if prop_type not in NO_GRAPH_LIST:
            for w_dimension, dataset in self.datasets.items():
                try:
                    data = dataset[selected_confs][selected_prop]
                except KeyError:
                    pass
                else:
                    valid_count += 1
        if prop_type in NO_GRAPH_LIST or valid_count == 0:
            return {'display': 'none'}
        else:
            return {'display': 'block'}

    def update_dropdown_options(self, selected_confs):
        all_datasets = set()
        for w in self.datasets.values():
            if selected_confs in w:
                all_datasets.update(w[selected_confs].keys())

        return [{'label': name, 'value': name} for name in all_datasets]

    def update_graph(self, selected_prop, selected_confs):
        fig = go.Figure()
        prop_type = DashReportApp.return_prop_type(selected_prop)
        if prop_type not in NO_GRAPH_LIST:
            for w_dimension, dataset in self.datasets.items():
                try:
                    data = dataset[selected_confs][selected_prop]['result']
                except KeyError:
                    pass
                else:
                    propCls = return_prop_class(prop_type)
                    trace_name = f"{w_dimension} - {selected_confs} - {selected_prop}"
                    traces, layout = propCls.plotly_graph(data, trace_name)
                    fig.add_traces(traces)
                    fig.layout = layout
                    fig.update_layout(autotypenumbers='convert types')
        return fig

    def update_table(self, selected_prop, selected_confs):
        table_index = 0
        tables = []
        prop_type = DashReportApp.return_prop_type(selected_prop)
        if prop_type == 'relaxation':
            for w_dimension, dataset in self.datasets.items():
                table_title = html.H3(f"{w_dimension} - {selected_confs} - {selected_prop}")
                clip_id = f"clip-{table_index}"
                clipboard = dcc.Clipboard(id=clip_id, style={"fontSize": 20})
                table = RelaxationReport.dash_table(dataset)
                table.id = f"table-{table_index}"
                tables.append(html.Div([table_title, clipboard, table],
                                       style={'width': '100%', 'display': 'inline-block'}))
                table_index += 1
        else:
            for w_dimension, dataset in self.datasets.items():
                try:
                    data = dataset[selected_confs][selected_prop]['result']
                except KeyError:
                    pass
                else:
                    propCls = return_prop_class(prop_type)
                    table_title = html.H3(
                        f"{w_dimension} - {selected_confs} - {selected_prop}",
                        style={"fontSize": 16}
                    )
                    table, df = propCls.dash_table(data)
                    table.id = f"table-{table_index}"
                    clip_id = f"clip-{table_index}"
                    clipboard = dcc.Clipboard(id=clip_id, style={"fontSize": 20})
                    tables.append(
                        html.Div([table_title, clipboard, table],
                                 style={'width': '50%', 'display': 'inline-block'})
                    )
                    table_index += 1

            self._generate_dynamic_callbacks(table_index)

        return html.Div(
            tables, style={'display': 'flex', 'flex-wrap': 'wrap'}
        )

    @staticmethod
    def csv_copy(_, data):
        dff = pd.DataFrame(data)
        return dff.to_csv(index=False)  # do not include row names

    def _generate_dynamic_callbacks(self, count):
        for index in range(count):
            self.app.callback(Output(f'clip-{index}', 'content'),
            [Input(f'clip-{index}', 'n_clicks'),
             State(f'table-{index}', 'data')])(self.csv_copy)

    def run(self, **kwargs):
        Timer(1, self.open_webpage).start()
        print('Dash server running... (See the report at http://127.0.0.1:8050/)')
        self.app.run(**kwargs)

    @staticmethod
    def open_webpage():
        webbrowser.open_new('http://127.0.0.1:8050/')


if __name__ == "__main__":
    DashReportApp(datasets=generate_test_datasets()).run()
