import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from dash import dash_table
import webbrowser
from threading import Timer

np.random.seed(0)

# create data
dataA = pd.DataFrame({
    'x': np.random.normal(1, 2, 100),
    'y': np.random.normal(2, 3, 100),
})

dataB = pd.DataFrame({
    'x': np.random.normal(3, 4, 100),
    'y': np.random.normal(4, 5, 100),
})

dataC = pd.DataFrame({
    'x': np.random.normal(5, 6, 100),
    'y': np.random.normal(6, 7, 100),
})

# create dash app
app = dash.Dash(__name__)

# define frontend layout
app.layout = html.Div([
    dcc.Dropdown(
        id='dropdown',
        options=[
            {'label': 'Dataset A (Table)', 'value': 'A'},
            {'label': 'Dataset B (Table + Scatter Plot)', 'value': 'B'},
            {'label': 'Dataset C (Table + Line Plot)', 'value': 'C'},
        ],
        value='A'
    ),
    dcc.Graph(id='graph', style={'display': 'none'}),
    html.Div(id='table')
])


# define graphic update function
@app.callback(
    Output('graph', 'figure'),
    Output('graph', 'style'),
    Input('dropdown', 'value')
)
def update_graph(selected_dataset):
    fig = go.Figure()
    style = {'display': 'none'}
    if selected_dataset == 'B':
        fig.add_trace(go.Scatter(x=dataB['x'], y=dataB['y'], mode='markers'))
        style = {'display': 'block'}
    elif selected_dataset == 'C':
        fig.add_trace(go.Scatter(x=dataC['x'], y=dataC['y'], mode='lines'))
        style = {'display': 'block'}

    return fig, style


# define table update function
@app.callback(
    Output('table', 'children'),
    Output('table', 'style'),
    Input('dropdown', 'value')
)
def update_table(selected_dataset):
    style = {'display': 'block'}
    if selected_dataset == 'A':
        data = dataA
    elif selected_dataset == 'B':
        data = dataB
    else:
        data = dataC

    table = dash_table.DataTable(
        data=data.to_dict('records'),
        columns=[{'name': i, 'id': i} for i in data.columns],
    )

    return table, style


def open_webpage():
    webbrowser.open_new('http://127.0.0.1:8050/')


if __name__ == '__main__':
    # Open webpage after app is started
    Timer(1.5, open_webpage).start()
    app.run_server(debug=True)
