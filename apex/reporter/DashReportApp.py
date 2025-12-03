import dash
from dash import dcc, html, State
from dash.dependencies import Input, Output
try:
    import dash_bootstrap_components as dbc
except Exception:
    dbc = None
import plotly.graph_objects as go
import webbrowser
from threading import Timer
from .relaxation_report import RelaxationReport
from .property_report import *


NO_GRAPH_LIST = ['relaxation']
UI_FRONTSIZE = 18
PLOT_FRONTSIZE = 18
LINE_SIZE = 3
MARKER_SIZE = 8
REF_LINE_SIZE = 4
REF_MARKER_SIZE = 9


def return_prop_class(prop_type: str):
    if prop_type == 'eos':
        return EOSReport
    elif prop_type == 'cohesive':
        return CohesiveReport
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
    elif prop_type == 'gamma_surface':
        return GammaSurfaceReport
    elif prop_type == 'phonon':
        return PhononReport
    elif prop_type == 'decohesive':
        return DecohesiveReport
    elif prop_type == 'finitetlatt':
        return FiniteTlattReport


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
        # Avoid relying on external CDN styles to work in offline environments
        # dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css"
        self.datasets = datasets
        self.all_confs = set()
        self.all_props = set()
        if dbc is not None:
            # Avoid external CDN links in restricted environments
            self.app = dash.Dash(
                __name__,
                suppress_callback_exceptions=True,
                external_stylesheets=[]
            )
        else:
            self.app = dash.Dash(
                __name__,
                suppress_callback_exceptions=True,
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
            self.all_confs.update(w.keys())
            for conf in w.values():
                self.all_props.update(conf.keys())

        # find the first default combination of configuration and property exist
        default_conf = None
        default_prop = None
        for w_key, w in self.datasets.items():
            if not w:
                continue
            for d_key, d in w.items():
                if d:
                    default_conf = d_key
                    default_prop = next(iter(d.keys()))
                    break
            if default_prop:
                break

        radio_inline = False
        if len(self.all_confs) > 10:
            radio_inline = True
        layout = html.Div(
            [
                html.H1("APEX Results Visualization Report", style={'textAlign': 'center'}),
                html.Label('Configuration:', style={'font-weight': 'bold', "fontSize": UI_FRONTSIZE}),
                dcc.RadioItems(
                    id='confs-radio',
                    options=[{'label': name, 'value': name} for name in self.all_confs],
                    value=default_conf, inline=radio_inline,
                    style={"fontSize": UI_FRONTSIZE}
                ),
                html.Br(),
                html.Label('Property:', style={'font-weight': 'bold', "fontSize": UI_FRONTSIZE}),
                dcc.Dropdown(
                    id='props-dropdown',
                    options=[{'label': name, 'value': name} for name in self.all_props],
                    value=default_prop,
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
            for w_conf, dataset in self.datasets.items():
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
        all_props = set()
        for w in self.datasets.values():
            if selected_confs in w:
                all_props.update(w[selected_confs].keys())

        return [{'label': name, 'value': name} for name in all_props]

    def update_graph(self, selected_prop, selected_confs):
        fig = go.Figure()
        prop_type = return_prop_type(selected_prop)
        color_generator = self.plotly_color_cycle()
        if prop_type not in NO_GRAPH_LIST:
            for w_conf, dataset in self.datasets.items():
                try:
                    data = dataset[selected_confs][selected_prop]['result']
                except KeyError:
                    pass
                else:
                    propCls = return_prop_class(prop_type)
                    trace_name = w_conf
                    extra = {}
                    if prop_type == 'Lat':
                        try:
                            cell = dataset[selected_confs]['relaxation']['result']['data']['cells'][-1]
                            a0 = (cell[0][0]**2 + cell[0][1]**2 + cell[0][2]**2)**0.5
                            b0 = (cell[1][0]**2 + cell[1][1]**2 + cell[1][2]**2)**0.5
                            c0 = (cell[2][0]**2 + cell[2][1]**2 + cell[2][2]**2)**0.5
                            extra['relax_abc'] = (a0, b0, c0)
                        except Exception:
                            pass
                    traces, layout = propCls.plotly_graph(
                        data, trace_name,
                        color=next(color_generator), **extra
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
            for w_conf, dataset in self.datasets.items():
                table_title = html.H3(f"{w_conf} - {selected_prop}")
                clip_id = f"clip-{table_index}"
                clipboard = dcc.Clipboard(id=clip_id, style={"fontSize": UI_FRONTSIZE})
                table = RelaxationReport.dash_table(dataset)
                table.id = f"table-{table_index}"
                tables.append(html.Div([table_title, clipboard, table],
                                       style={'width': '100%', 'display': 'inline-block'}))
                table_index += 1
        else:
            for w_conf, dataset in self.datasets.items():
                try:
                    data = dataset[selected_confs][selected_prop]['result']
                except KeyError:
                    pass
                else:
                    propCls = return_prop_class(prop_type)
                    table_title = html.H3(
                        f"{w_conf} - {selected_confs} - {selected_prop}",
                        style={"fontSize": UI_FRONTSIZE}
                    )
                    extra = {}
                    if prop_type == 'Lat':
                        try:
                            cell = dataset[selected_confs]['relaxation']['result']['data']['cells'][-1]
                            a0 = (cell[0][0]**2 + cell[0][1]**2 + cell[0][2]**2)**0.5
                            b0 = (cell[1][0]**2 + cell[1][1]**2 + cell[1][2]**2)**0.5
                            c0 = (cell[2][0]**2 + cell[2][1]**2 + cell[2][2]**2)**0.5
                            extra['relax_abc'] = (a0, b0, c0)
                        except Exception:
                            pass
                    table, df = propCls.dash_table(data, **extra)
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
        # Normalize run_server args
        host = kwargs.pop('host', '127.0.0.1')
        port = kwargs.pop('port', 8050)
        debug = kwargs.pop('debug', False)
        use_reloader = kwargs.pop('use_reloader', False)

        # Only try to open a browser when explicitly allowed by env
        import os
        if os.getenv('APEX_OPEN_BROWSER', '0') in ('1', 'true', 'True'):
            try:
                Timer(1.2, self.open_webpage).start()
            except Exception as e:
                print(f"Skip auto-open browser due to: {e}")
        print('Dash server running... (See the report at http://127.0.0.1:8050/)')
        print('NOTE: If two Dash pages are automatically opened in your browser, you can close the first one.')
        print('NOTE: If the clipboard buttons do not function well, try to reload the page one time.')
        print('NOTE: Do not over-refresh the page as duplicate errors may occur. '
              'If did, stop the server and re-execute the apex report command.')
        # Bind explicitly to localhost; avoid relying on hostname
        self.app.run_server(host=host, port=port, debug=debug, use_reloader=use_reloader)

    @staticmethod
    def open_webpage():
        try:
            webbrowser.open('http://127.0.0.1:8050/')
        except Exception as e:
            # Avoid crashing on headless / restricted systems
            print(f"Skip auto-open browser due to: {e}")


if __name__ == "__main__":
    DashReportApp(datasets=generate_test_datasets()).run()
