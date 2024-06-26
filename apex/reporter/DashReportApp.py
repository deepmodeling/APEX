import dash
from dash import dcc, html, State
from dash.dependencies import Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import webbrowser
from threading import Timer
from .relaxation_report import RelaxationReport
from .property_report import *


NO_GRAPH_LIST = ['relaxation']
UI_FRONTSIZE = 18
PLOT_FRONTSIZE = 18
LINE_SIZE = 2
MARKER_SIZE = 6
REF_LINE_SIZE = 4
REF_MARKER_SIZE = 9


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
    elif prop_type == 'DecohesionEnergy':
        return DecohesionEnergyReport


def return_prop_type(prop: str):
    try:
        prop_type = prop.split('_')[0]
    except AttributeError:
        return None
    return prop_type


def generate_test_datasets():
    datasets = {
        '/Users/zhuoyuan/labspace/ti-mo_test/Ti_test/DP_test': {
            'confs/std-hcp': {
                'result': {
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
    }
    return datasets


class DashReportApp:
    def __init__(self, datasets):
        dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css"
        self.datasets = datasets
        self.all_dimensions = set()
        self.all_datasets = set()
        self.app = dash.Dash(
            __name__,
            suppress_callback_exceptions=True,
            external_stylesheets=[dbc.themes.MATERIA, dbc_css]
        )
        #load_figure_template("materia")
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
    def plotly_color_cycle():
        # https://plotly.com/python/discrete-color/
        colors = [
            '#636EFA',  # blue
            '#EF553B',  # red
            '#00CC96',  # green
            '#AB63FA',  # purple
            '#FFA15A',  # orange
            '#19D3F3',  # cyan
            '#FF6692',  # pink
            '#B6E880',  # lime
            '#FF97FF',  # magenta
            '#FECB52',  # yellow
        ]
        while True:
            for color in colors:
                yield color

    def generate_layout(self):
        for w in self.datasets.values():
            self.all_dimensions.update(w.keys())
            for dimension in w.values():
                self.all_datasets.update(dimension.keys())

        # find the first default combination of configuration and property exist
        default_dimension = None
        default_dataset = None
        for w_key, w in self.datasets.items():
            if not w:
                continue
            for d_key, d in w.items():
                if d:
                    default_dimension = d_key
                    default_dataset = next(iter(d.keys()))
                    break
            if default_dataset:
                break

        radio_inline = False
        if len(self.all_dimensions) > 10:
            radio_inline = True
        layout = html.Div(
            [
                html.H1("APEX Results Visualization Report", style={'textAlign': 'center'}),
                html.Label('Configuration:', style={'font-weight': 'bold', "fontSize": UI_FRONTSIZE}),
                dcc.RadioItems(
                    id='confs-radio',
                    options=[{'label': name, 'value': name} for name in self.all_dimensions],
                    value=default_dimension, inline=radio_inline,
                    style={"fontSize": UI_FRONTSIZE}
                ),
                html.Br(),
                html.Label('Property:', style={'font-weight': 'bold', "fontSize": UI_FRONTSIZE}),
                dcc.Dropdown(
                    id='props-dropdown',
                    options=[{'label': name, 'value': name} for name in self.all_datasets],
                    value=default_dataset,
                    style={"fontSize": UI_FRONTSIZE}
                ),
                html.Br(),
                dcc.Graph(id='graph', style={'display': 'block', "fontSize": UI_FRONTSIZE},
                          className='graph-container'),
                html.Div(id='table')
            ],
            style={'margin': '0 auto', 'maxWidth': '900px'}
        )
        return layout

    def update_graph_visibility(self, selected_prop, selected_confs):
        prop_type = return_prop_type(selected_prop)
        valid_count = 0
        if prop_type not in NO_GRAPH_LIST:
            for w_dimension, dataset in self.datasets.items():
                try:
                    _ = dataset[selected_confs][selected_prop]
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
        prop_type = return_prop_type(selected_prop)
        color_generator = self.plotly_color_cycle()
        if prop_type not in NO_GRAPH_LIST:
            for w_dimension, dataset in self.datasets.items():
                try:
                    data = dataset[selected_confs][selected_prop]['result']
                except KeyError:
                    pass
                else:
                    propCls = return_prop_class(prop_type)
                    # trace_name = f"{w_dimension} - {selected_confs} - {selected_prop}"
                    trace_name = w_dimension
                    traces, layout = propCls.plotly_graph(
                        data, trace_name,
                        color=next(color_generator)
                    )
                    # set color and width of reference lines
                    if prop_type != 'vacancy':
                        for trace in iter(traces):
                            if trace_name.split('/')[-1] in ['DFT', 'REF']:
                                trace.update({'line': {'color': 'black', 'width': REF_LINE_SIZE},
                                              'marker': {'color': 'black', 'size': REF_MARKER_SIZE}})
                            else:
                                trace.update({'line': {'width': LINE_SIZE}}, marker={'size': MARKER_SIZE})
                    fig.add_traces(traces)
                    fig.layout = layout
                    fig.update_layout(
                        font=dict(
                            family="Arial, sans-serif",
                            size=PLOT_FRONTSIZE,
                            color="Black"
                        ),
                        plot_bgcolor='rgba(0, 0, 0, 0)',
                        #plot_bgcolor='rgba(229, 229, 229, 100)',
                        #paper_bgcolor='rgba(0, 0, 0, 0)',
                        xaxis_title=dict(font=dict(size=PLOT_FRONTSIZE)),
                        yaxis_title=dict(font=dict(size=PLOT_FRONTSIZE)),
                        xaxis=dict(
                            mirror=True,
                            ticks='inside',
                            tickwidth=2,
                            showline=True,
                            linewidth=2,
                            linecolor='black',
                            gridcolor='lightgrey',
                            zerolinecolor='lightgrey',
                            zerolinewidth=0.2
                        ),
                        yaxis=dict(
                            mirror=True,
                            ticks='inside',
                            tickwidth=2,
                            showline=True,
                            linewidth=2,
                            linecolor='black',
                            gridcolor='lightgrey',
                            zerolinecolor='lightgrey',
                            zerolinewidth=0.2
                        ),
                        polar=dict(
                            bgcolor='rgba(0, 0, 0, 0)',
                            radialaxis=dict(
                                visible=True,
                                autorange=True,
                                ticks='inside',
                                tickwidth=2,
                                showline=True,
                                linewidth=2,
                                linecolor='black',
                                gridcolor='lightgrey',
                            ),
                            angularaxis=dict(
                                visible=True,
                                ticks='inside',
                                tickwidth=2,
                                showline=True,
                                linewidth=2,
                                linecolor='black',
                                gridcolor='lightgrey',
                            ),
                        ),
                        autotypenumbers='convert types'
                    )
        return fig

    def update_table(self, selected_prop, selected_confs):
        table_index = 0
        tables = []
        prop_type = return_prop_type(selected_prop)
        if prop_type == 'relaxation':
            for w_dimension, dataset in self.datasets.items():
                table_title = html.H3(f"{w_dimension} - {selected_prop}")
                clip_id = f"clip-{table_index}"
                clipboard = dcc.Clipboard(id=clip_id, style={"fontSize": UI_FRONTSIZE})
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
                        style={"fontSize": UI_FRONTSIZE}
                    )
                    table, df = propCls.dash_table(data)
                    table.id = f"table-{table_index}"
                    # add strips to table
                    table.style_data_conditional = [
                        {'if': {'row_index': 'odd'},
                            'backgroundColor': 'rgb(248, 248, 248)'}
                    ]
                    # add clipboards
                    clip_id = f"clip-{table_index}"
                    clipboard = dcc.Clipboard(id=clip_id, style={"fontSize": UI_FRONTSIZE})
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
        Timer(1.2, self.open_webpage).start()
        print('Dash server running... (See the report at http://127.0.0.1:8050/)')
        print('NOTE: If two Dash pages are automatically opened in your browser, you can close the first one.')
        print('NOTE: If the clipboard buttons do not function well, try to reload the page one time.')
        print('NOTE: Do not over-refresh the page as duplicate errors may occur. '
              'If did, stop the server and re-execute the apex report command.')
        self.app.run(**kwargs)

    @staticmethod
    def open_webpage():
        webbrowser.open('http://127.0.0.1:8050/')


if __name__ == "__main__":
    DashReportApp(datasets=generate_test_datasets()).run()
