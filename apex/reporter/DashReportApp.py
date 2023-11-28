import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
from dash import dash_table
import webbrowser
from threading import Timer
from apex.core.common_prop import return_prop_class

NO_GRAPH_LIST = ['elastic', 'vacancy']


def generate_test_datasets():
    datasets = {
        '/Users/zhuoyuan/labspace/ti-mo_test/Ti_test/DP_test': {
            'confs/std-hcp': {
                'eos_00': {
                    "10.407143058573908": -6.019576,
                    "11.274404980121734": -6.458249,
                    "12.14166690166956": -6.883705,
                    "13.008928823217385": -7.25439,
                    "13.87619074476521": -7.499602,
                    "14.743452666313036": -7.6612955,
                    "15.610714587860862": -7.7632485,
                    "16.477976509408688": -7.817405,
                    "17.345238430956513": -7.8335905,
                    "18.21250035250434": -7.8194775,
                    "19.079762274052165": -7.7812295,
                    "19.94702419559999": -7.723846,
                    "20.81428611714782": -7.651926,
                    "21.68154803869564": -7.5676175,
                    "22.548809960243467": -7.473754,
                    "23.416071881791293": -7.370028
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
        self.app = dash.Dash(__name__)
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

        self.app.callback(
            Output('props-dropdown', 'options'),
            [Input('confs-radio', 'value')]
        )(self.update_dropdown_options)

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
                dcc.RadioItems(
                    id='confs-radio',
                    options=[{'label': name, 'value': name} for name in self.all_dimensions],
                    value=default_dimension
                ),
                dcc.Dropdown(
                    id='props-dropdown',
                    options=[{'label': name, 'value': name} for name in self.all_datasets],
                    value=default_dataset
                ),
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
        tables = []
        prop_type = DashReportApp.return_prop_type(selected_prop)
        for w_dimension, dataset in self.datasets.items():
            try:
                data = dataset[selected_confs][selected_prop]['result']
            except KeyError:
                pass
            else:
                propCls = return_prop_class(prop_type)
                table_title = html.H3(f"{w_dimension} - {selected_confs} - {selected_prop}")
                table = propCls.dash_table(data)
                tables.append(html.Div([table_title, table],
                                       style={'width': '50%', 'display': 'inline-block'}))

        return html.Div(tables, style={'display': 'flex', 'flex-wrap': 'wrap'})

    def run(self):
        Timer(1.5, self.open_webpage).start()
        print('Dash server running... (See the report at http://127.0.0.1:8050/)')
        self.app.run_server(debug=True)

    @staticmethod
    def open_webpage():
        webbrowser.open_new('http://127.0.0.1:8050/')


if __name__ == "__main__":
    DashReportApp(datasets=generate_test_datasets()).run()
