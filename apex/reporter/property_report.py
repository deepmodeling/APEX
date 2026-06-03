from abc import ABC, abstractmethod
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objs as go
from dash import dash_table

from apex.core.lib.utils import round_format, round_2d_format

TABLE_WIDTH = "50%"
TABLE_MIN_WIDTH = "95%"
TABLE_STYLE = {"width": TABLE_WIDTH, "minWidth": TABLE_MIN_WIDTH, "overflowX": "auto"}
TABLE_CELL_STYLE = {"textAlign": "left"}


def random_color():
    r = np.random.randint(50, 200)
    g = np.random.randint(50, 200)
    b = np.random.randint(50, 200)
    return f"rgb({r}, {g}, {b})"


def build_table(df: pd.DataFrame, cell_style: Dict = None) -> dash_table.DataTable:
    """Create a dash DataTable with consistent styling."""
    style_cell = TABLE_CELL_STYLE if cell_style is None else {**TABLE_CELL_STYLE, **cell_style}
    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": i, "id": i} for i in df.columns],
        style_table=TABLE_STYLE,
        style_cell=style_cell,
    )


class PropertyReport(ABC):
    @staticmethod
    @abstractmethod
    def plotly_graph(res_data: dict, name: str):
        """
        Plot plotly graph.

        Parameters
        ----------
        res_data : dict
            The dict storing the result of the props
        Returns:
        -------
        list[plotly.graph_objs]
            The list of plotly graph object
        plotly.graph_objs.layout
            the layout
        """
        pass

    @staticmethod
    @abstractmethod
    def dash_table(res_data: dict, decimal: int) -> Tuple[dash_table.DataTable, pd.DataFrame]:
        """
        Make Dash table.

        Parameters
        ----------
        res_data : dict
            The dict storing the result of the props
        Returns:
        -------
        dash_table.DataTable
            The dash table object
        pd.DataFrame
        """
        pass


class EOSReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
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
            name=name,
            x=df['VpA(A^3)'],
            y=df['EpA(eV)'],
            mode='lines+markers'
        )
        layout = go.Layout(
            title='Energy of State',
            xaxis=dict(
                title_text="VpA (A<sup>3</sup>)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis=dict(
                title_text="EpA (eV)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            )
        )

        return [trace], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        vpa = []
        epa = []
        for k, v in res_data.items():
            vpa.append(float(k))
            epa.append(float(v))
        df = pd.DataFrame(
            {
                "VpA(A^3)": round_format(vpa, decimal),
                "EpA(eV)": round_format(epa, decimal),
            }
        )

        return build_table(df), df


class CohesiveReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        lattice = []
        epa = []
        cohesive_energy = []
        for k, m in res_data.items():
            lattice.append(float(k))
            epa.append(float(m["total_energy"]))
            cohesive_energy.append(float(m["cohesive_energy"]))
        
        df = pd.DataFrame({
            "ScaledLattice": lattice,
            "TotalEnergy(eV/atom)": epa,
            "CohesiveEnergy(eV/atom)": cohesive_energy
        })
        
        trace = go.Scatter(
            name=name,
            x=df['ScaledLattice'],
            y=df['CohesiveEnergy(eV/atom)'],
            mode='lines+markers'
        )
        
        zero_line = go.Scatter(
            x=[min(lattice), max(lattice)],
            y=[0, 0],
            mode='lines',
            line=dict(color='blue', width=1, dash='dot'),
            showlegend=False
        )

        layout = go.Layout(
            title='Cohesive Energy',
            xaxis=dict(
                title_text="Scaled Lattice Parameter a/a<sub>0</sub>",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                ),
            ),
            yaxis=dict(
                title_text="Cohesive Energy E<sub>coh</sub> (eV/atom)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                ),
            )
        )
        
        return [trace, zero_line], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        lattice = []
        epa = []
        cohesive_energy = []
        for k, m in res_data.items():
            lattice.append(float(k))
            epa.append(float(m["total_energy"]))
            cohesive_energy.append(float(m["cohesive_energy"]))
            
        df = pd.DataFrame(
            {
                "Scaled Lattice Parameter (a/a0)": round_format(lattice, decimal),
                "Total Energy (eV/atom)": round_format(epa, decimal),
                "Cohesive Energy (eV/atom)": round_format(cohesive_energy, decimal),
            }
        )

        return build_table(df), df
    
    
class DecohesiveReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        # Sort by separation distance to keep curves monotonic.
        sorted_vals = sorted(res_data.values(), key=lambda x: float(x[0]))
        vacuum_size = [float(vals[0]) for vals in sorted_vals]
        decohesion_e = [float(vals[1]) for vals in sorted_vals]
        stress = [float(vals[2]) for vals in sorted_vals]

        df = pd.DataFrame(
            {
                "Separation Distance (A)": vacuum_size,
                "Decohesion Energy (J/m^2)": decohesion_e,
                "Decohesion Stress (GPa)": [s / 1e9 for s in stress],
            }
        )
        trace_E = go.Scatter(
            name=f"{name} Decohesion Energy",
            x=df['Separation Distance (A)'],
            y=df['Decohesion Energy (J/m^2)'],
            mode='lines+markers',
            yaxis='y1'
        )

        trace_S = go.Scatter(
            name=f"{name} Decohesion Stress",
            x=df['Separation Distance (A)'],
            y=df['Decohesion Stress (GPa)'],
            mode='lines+markers',
            yaxis='y2'
        )
        layout = go.Layout(
            title=dict(
                text='Decohesion Energy and Stress',
                x=0.5,
                xanchor='center'
            ),
            xaxis=dict(
                title_text="Separation Distance (A)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis=dict(
                title="Decohesion Energy (J/m^2)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis2=dict(
                title="Decohesion Stress (GPa)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                ),
                overlaying='y',
                side='right'
            )
        )
        trace = [trace_E, trace_S]
        return trace, layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        sorted_vals = sorted(res_data.values(), key=lambda x: float(x[0]))
        vacuum_size = [float(vals[0]) for vals in sorted_vals]
        decohesion_e = [float(vals[1]) for vals in sorted_vals]
        stress = [float(vals[2]) for vals in sorted_vals]

        df = pd.DataFrame(
            {
                "Separation Distance (A)": round_format(vacuum_size, decimal),
                "Decohesion Energy (J/m^2)": round_format(decohesion_e, decimal),
                "Decohesion Stress (GPa)": round_format([s / 1e9 for s in stress], decimal),
            }
        )

        return build_table(df), df


class FiniteTlattReport(PropertyReport):
    """Report lattice parameters as a function of temperature."""

    @staticmethod
    def _normalized_data(res_data, relax_abc=None):
        data = {}
        for value in res_data.values():
            if isinstance(value, (list, tuple)) and len(value) >= 4:
                a, b, c, temp = (
                    float(value[0]),
                    float(value[1]),
                    float(value[2]),
                    float(value[3]),
                )
            elif isinstance(value, dict):
                a = float(value.get("a", 0.0))
                b = float(value.get("b", 0.0))
                c = float(value.get("c", 0.0))
                temp = float(value.get("temperature", 0.0))
            else:
                continue
            data[temp] = {"a": a, "b": b, "c": c}
        if relax_abc and 0.0 not in data:
            a0, b0, c0 = relax_abc
            data[0.0] = {"a": float(a0), "b": float(b0), "c": float(c0)}
        return data

    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        data = FiniteTlattReport._normalized_data(
            res_data, relax_abc=kwargs.get("relax_abc")
        )
        temps = sorted(data.keys())
        x_values = [
            str(int(temp)) if abs(temp - round(temp)) < 1e-6 else str(temp)
            for temp in temps
        ]
        a_values = [data[temp]["a"] for temp in temps]
        b_values = [data[temp]["b"] for temp in temps]
        c_values = [data[temp]["c"] for temp in temps]

        trace_a = go.Scatter(x=x_values, y=a_values, mode='lines+markers', name='a', line=dict(color='blue'))
        trace_b = go.Scatter(x=x_values, y=b_values, mode='lines+markers', name='b', line=dict(color='green'))
        trace_c = go.Scatter(x=x_values, y=c_values, mode='lines+markers', name='c', line=dict(color='red'))

        layout = go.Layout(
            title='Finite Temperature Lattice Parameters',
            xaxis=dict(title='Temperature (K)'),
            yaxis=dict(title='Lattice length (Å)'),
            showlegend=True
        )
        return [trace_a, trace_b, trace_c], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 6, **kwargs) -> dash_table.DataTable:
        data = FiniteTlattReport._normalized_data(
            res_data, relax_abc=kwargs.get("relax_abc")
        )
        temps = sorted(data.keys())
        rows = []
        for temp in temps:
            a = data[temp]["a"]
            b = data[temp]["b"]
            c = data[temp]["c"]
            c_over_a = (c / a) if a else 0.0
            rows.append({
                "Temperature (K)": int(temp) if abs(temp - round(temp)) < 1e-6 else temp,
                "a (Å)": round(a, decimal),
                "b (Å)": round(b, decimal),
                "c (Å)": round(c, decimal),
                "c/a": round(c_over_a, decimal),
            })
        df = pd.DataFrame(rows)
        return build_table(df), df


class FiniteTelasticReport(PropertyReport):
    """Report finite-temperature elastic constants as a function of temperature."""

    @staticmethod
    def _temperature_rows(res_data: dict):
        temperatures = res_data.get("temperatures", {})
        rows = []
        for temp_key, temp_data in sorted(
            temperatures.items(), key=lambda item: float(item[0])
        ):
            temp = float(temp_key)
            row = {
                "Temperature (K)": int(temp) if abs(temp - round(temp)) < 1e-6 else temp,
                "B (GPa)": temp_data.get("B"),
                "G (GPa)": temp_data.get("G"),
                "E (GPa)": temp_data.get("E"),
                "u": temp_data.get("u", temp_data.get("poisson_ratio")),
                "rank": temp_data.get("rank"),
                "paired responses": temp_data.get("number_of_paired_responses"),
            }
            tensor = temp_data.get("elastic_tensor_GPa", temp_data.get("elastic_tensor"))
            if tensor is not None:
                for ii in range(6):
                    for jj in range(6):
                        row[f"C{ii + 1}{jj + 1} (GPa)"] = tensor[ii][jj]
            rows.append(row)
        return rows

    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        rows = FiniteTelasticReport._temperature_rows(res_data)
        temps = [row["Temperature (K)"] for row in rows]
        traces = []
        for label in ["B (GPa)", "G (GPa)", "E (GPa)", "u"]:
            values = [row.get(label) for row in rows]
            if not any(value is not None for value in values):
                continue
            traces.append(
                go.Scatter(
                    name=f"{name} {label}",
                    x=temps,
                    y=values,
                    mode="lines+markers",
                )
            )

        layout = go.Layout(
            title="Finite-Temperature Elastic Constants",
            xaxis=dict(title="Temperature (K)"),
            yaxis=dict(title="Modulus (GPa) / Poisson ratio"),
            showlegend=True,
        )
        return traces, layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        rows = FiniteTelasticReport._temperature_rows(res_data)
        numeric_columns = {
            key
            for row in rows
            for key, value in row.items()
            if isinstance(value, (int, float)) and key != "Temperature (K)"
        }
        rounded_rows = []
        for row in rows:
            rounded = {}
            for key, value in row.items():
                if key in numeric_columns and value is not None:
                    rounded[key] = round(float(value), decimal)
                else:
                    rounded[key] = value
            rounded_rows.append(rounded)
        df = pd.DataFrame(rounded_rows)
        return build_table(df, cell_style={"width": "120px"}), df


class AnnealingReport(PropertyReport):
    """Report annealing RDF, MSD, and temperature-volume response."""

    @staticmethod
    def _iter_tasks(res_data: dict):
        tasks = res_data.get("tasks", {})
        if tasks:
            return sorted(tasks.items())
        return []

    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        traces = []
        color = kwargs.get("color")
        for task_name, task_data in AnnealingReport._iter_tasks(res_data):
            for stage, rdf_data in sorted(task_data.get("rdf", {}).items()):
                radius = rdf_data.get("radius", [])
                g_r = rdf_data.get("g_r", [])
                if not radius or not g_r:
                    continue
                traces.append(
                    go.Scatter(
                        name=f"{name} {task_name} RDF {stage}",
                        x=radius,
                        y=g_r,
                        mode="lines",
                        xaxis="x",
                        yaxis="y",
                        line=dict(color=color) if color else None,
                    )
                )

            for stage, msd_data in sorted(task_data.get("msd", {}).items()):
                timesteps = msd_data.get("timestep", [])
                total = msd_data.get("msd_total", [])
                if not timesteps or not total:
                    continue
                traces.append(
                    go.Scatter(
                        name=f"{name} {task_name} MSD {stage}",
                        x=timesteps,
                        y=total,
                        mode="lines",
                        xaxis="x2",
                        yaxis="y2",
                    )
                )

            for stage, vt_data in sorted(task_data.get("volume_temperature", {}).items()):
                temps = vt_data.get("temperature", [])
                volume = vt_data.get("volume_per_atom", [])
                if not temps or not volume:
                    continue
                traces.append(
                    go.Scatter(
                        name=f"{name} {task_name} {stage} V(T)",
                        x=temps,
                        y=volume,
                        mode="lines+markers",
                        xaxis="x3",
                        yaxis="y3",
                    )
                )

        layout = go.Layout(
            title="Annealing RDF, MSD, and Volume-Temperature Response",
            showlegend=True,
            xaxis=dict(title="r (Å)", domain=[0.0, 1.0], anchor="y"),
            yaxis=dict(title="g(r)", domain=[0.70, 1.0], anchor="x"),
            xaxis2=dict(title="Timestep", domain=[0.0, 1.0], anchor="y2"),
            yaxis2=dict(title="MSD (Å²)", domain=[0.36, 0.64], anchor="x2"),
            xaxis3=dict(title="Temperature (K)", domain=[0.0, 1.0], anchor="y3"),
            yaxis3=dict(title="Volume/atom (Å³)", domain=[0.0, 0.30], anchor="x3"),
            height=850,
        )
        return traces, layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        rows = []
        for task_name, task_data in AnnealingReport._iter_tasks(res_data):
            summary = task_data.get("summary", {})
            for stage in sorted(set(
                    list(task_data.get("rdf", {}).keys())
                    + list(task_data.get("msd", {}).keys())
                    + list(task_data.get("volume_temperature", {}).keys())
            )):
                rdf_points = summary.get("rdf_points", {}).get(stage, 0)
                msd_points = summary.get("msd_points", {}).get(stage, 0)
                volume_points = summary.get("volume_temperature_points", {}).get(stage, 0)
                volume_data = task_data.get("volume_temperature", {}).get(stage, {})
                temps = volume_data.get("temperature", [])
                volumes = volume_data.get("volume_per_atom", [])
                row = {
                    "Task": task_name,
                    "Stage": stage,
                    "RDF points": rdf_points,
                    "MSD timesteps": msd_points,
                    "V(T) points": volume_points,
                }
                if temps:
                    row["T min (K)"] = round(float(min(temps)), decimal)
                    row["T max (K)"] = round(float(max(temps)), decimal)
                if volumes:
                    row["V/atom min (Å³)"] = round(float(min(volumes)), decimal)
                    row["V/atom max (Å³)"] = round(float(max(volumes)), decimal)
                rows.append(row)
        df = pd.DataFrame(rows)
        return build_table(df, cell_style={"width": "130px"}), df


class ElasticReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        elastic_tensor = res_data['elastic_tensor']
        c11 = elastic_tensor[0][0]
        c12 = elastic_tensor[0][1]
        c13 = elastic_tensor[0][2]
        c22 = elastic_tensor[1][1]
        c23 = elastic_tensor[1][2]
        c33 = elastic_tensor[2][2]
        c44 = elastic_tensor[3][3]
        c55 = elastic_tensor[4][4]
        c66 = elastic_tensor[5][5]
        BV = res_data['B']
        GV = res_data['G']
        EV = res_data['E']
        uV = res_data['u']

        polar = go.Scatterpolar(
            name=name,
            r=[c11, c12, c13, c22, c23, c33,
               c44, c55, c66, BV, GV, EV, uV],
            theta=['C11', 'C12', 'C13', 'C22', 'C23', 'C33',
                   'C44', 'C55', 'C66', 'B', 'G', 'E', 'u'],
            fill='none'
        )

        layout = go.Layout(
            showlegend=True,
            title='Elastic Property'
        )

        return [polar], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        ph = '-'
        et = res_data['elastic_tensor']
        BV = res_data['B']
        GV = res_data['G']
        EV = res_data['E']
        uV = res_data['u']
        null_t = [' '] * 6
        BV_t = ['B', BV, ph, ph, ph, ph]
        GV_t = ['G', GV, ph, ph, ph, ph]
        EV_t = ['E', EV, ph, ph, ph, ph]
        uV_t = ['u', uV, ph, ph, ph, ph]
        table_tensor = [et[0], et[1], et[2], et[3], et[4], et[5],
                        null_t, BV_t, GV_t, EV_t, uV_t]

        # round numbers in table
        rounded_tensor = round_2d_format(table_tensor, decimal)

        df = pd.DataFrame(
            rounded_tensor,
            columns=['Col 1', 'Col 2', 'Col 3', 'Col 4', 'Col 5', 'Col 6'],
        )
        return build_table(df, cell_style={'width': '150px'}), df

class SurfaceReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        miller = []
        surf_e = []
        epa = []
        epa_equi = []
        for k, v in res_data.items():
            miller.append(k.split('_')[0])
            surf_e.append(float(v[0]))
            epa.append(float(v[1]))
            epa_equi.append(float(v[2]))

        # enclose polar plot
        surf_e.append(surf_e[0])
        miller.append(miller[0])
        polar = go.Scatterpolar(
            name=name,
            r=surf_e,
            theta=miller,
            fill='none'
        )

        layout = go.Layout(
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    autorange=True
                )
            ),
            showlegend=True,
            title='Surface Forming Energy'
        )

        return [polar], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        miller = []
        surf_e = []
        epa = []
        epa_equi = []
        for k, v in res_data.items():
            miller.append(k.split('_')[0])
            surf_e.append(float(v[0]))
            epa.append(float(v[1]))
            epa_equi.append(float(v[2]))
        df = pd.DataFrame({
            "Miller Index": miller,
            "E_surface (J/m^2)": round_format(surf_e, decimal),
            "EpA (eV)": round_format(epa, decimal),
            "EpA_equi (eV)": round_format(epa_equi, decimal),
        })

        return build_table(df), df


class InterstitialReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        inter_struct = []
        inter_form_e = []
        struct_e = []
        equi_e = []
        for k, v in res_data.items():
            inter_struct.append(k.split('_')[1])
            inter_form_e.append(float(v[0]))
            struct_e.append(float(v[1]))
            equi_e.append(float(v[2]))

        # enclose polar plot
        inter_struct.append(inter_struct[0])
        inter_form_e.append(inter_form_e[0])

        polar = go.Scatterpolar(
            name=name,
            r=inter_form_e,
            theta=inter_struct,
            fill='none'
        )

        layout = go.Layout(
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    autorange=True
                )
            ),
            showlegend=True,
            title='Interstitial Forming Energy'
        )

        return [polar], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        inter_struct = []
        inter_form_e = []
        struct_e = []
        equi_e = []
        for k, v in res_data.items():
            inter_struct.append(k.split('_')[1])
            inter_form_e.append(float(v[0]))
            struct_e.append(float(v[1]))
            equi_e.append(float(v[2]))
        df = pd.DataFrame({
            "Initial configuration ": inter_struct,
            "E_form (eV)": round_format(inter_form_e, decimal),
            "E_defect (eV)": round_format(struct_e, decimal),
            "E_equi (eV)": round_format(equi_e, decimal),
        })

        return build_table(df), df


class VacancyReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        v = list(res_data.values())[0]
        vac_form_e = float(v[0])

        bar = go.Bar(
            name=name,
            # x=[vac_form_e, struct_e, equi_e],
            # y=['E_form (eV)', 'E_defect (eV)', 'E_equi (eV)'],
            x=[vac_form_e],
            y=['E_form'],
            orientation='h'
        )

        layout = go.Layout(
            title='Vacancy Forming Energy',
            xaxis=dict(
                title_text="Vacancy Forming Energy (eV)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis=dict(
                title_text="",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                ),
            ),
            showlegend=True
        )

        return [bar], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        vac_form_e = []
        struct_e = []
        equi_e = []
        for k, v in res_data.items():
            vac_form_e.append(float(v[0]))
            struct_e.append(float(v[1]))
            equi_e.append(float(v[2]))
        df = pd.DataFrame({
            "E_form (eV)": round_format(vac_form_e, decimal),
            "E_defect (eV)": round_format(struct_e, decimal),
            "E_equi (eV)": round_format(equi_e, decimal),
        })

        return build_table(df), df


class GammaReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        displ = []
        displ_length = []
        fault_en = []
        for k, v in res_data.items():
            displ.append(k)
            displ_length.append(v[0])
            fault_en.append(v[1])
        df = pd.DataFrame({
            "displacement": displ,
            "displace_length": displ_length,
            "fault_en": fault_en
        })
        trace = go.Scatter(
            name=name,
            x=df['displacement'],
            # x=df['displace_length'],
            y=df['fault_en'],
            mode='lines+markers'
        )
        layout = go.Layout(
            title='Stacking Fault Energy (Gamma Line)',
            xaxis=dict(
                title_text="Slip Fraction",
                # title_text="Displace_Length (Å)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis=dict(
                title_text='Fault Energy (J/m<sup>2</sup>)',
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            )
        )

        return [trace], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        displ = []
        displ_length = []
        fault_en = []
        struct_en = []
        equi_en = []
        for k, v in res_data.items():
            displ.append(float(k))
            displ_length.append(v[0])
            fault_en.append(v[1])
            struct_en.append((v[2]))
            equi_en.append(v[3])
        df = pd.DataFrame({
            "Slip_frac": round_format(displ, decimal),
            "Slip_Length (Å)": round_format(displ_length, decimal),
            "E_Fault (J/m^2)": round_format(fault_en, decimal),
            "E_Slab (eV)": round_format(struct_en, decimal),
            "E_Equilib (eV)": round_format(equi_en, decimal)
        })

        return build_table(df), df


class GammaSurfaceReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        rows = []
        for k, v in res_data.items():
            frac_x_str, frac_y_str = str(k).split(",")
            rows.append(
                {
                    "frac_x": float(frac_x_str),
                    "frac_y": float(frac_y_str),
                    "fault_en": float(v[2]),
                }
            )

        df = pd.DataFrame(rows)
        pivot = df.pivot_table(index="frac_y", columns="frac_x", values="fault_en")
        heatmap = go.Heatmap(
            name=name,
            x=list(pivot.columns),
            y=list(pivot.index),
            z=pivot.values,
            colorbar={"title": "Fault Energy (J/m^2)"},
        )
        layout = go.Layout(
            title="Stacking Fault Energy (Gamma Surface)",
            xaxis=dict(
                title_text="Slip Fraction X",
                title_font=dict(size=18, color="#7f7f7f"),
            ),
            yaxis=dict(
                title_text="Slip Fraction Y",
                title_font=dict(size=18, color="#7f7f7f"),
            ),
        )

        return [heatmap], layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        frac_x = []
        frac_y = []
        disp_x = []
        disp_y = []
        fault_en = []
        struct_en = []
        equi_en = []
        for k, v in res_data.items():
            frac_x_str, frac_y_str = str(k).split(",")
            frac_x.append(float(frac_x_str))
            frac_y.append(float(frac_y_str))
            disp_x.append(v[0])
            disp_y.append(v[1])
            fault_en.append(v[2])
            struct_en.append(v[3])
            equi_en.append(v[4])
        df = pd.DataFrame(
            {
                "Slip_frac_x": round_format(frac_x, decimal),
                "Slip_frac_y": round_format(frac_y, decimal),
                "Slip_x (A)": round_format(disp_x, decimal),
                "Slip_y (A)": round_format(disp_y, decimal),
                "E_Fault (J/m^2)": round_format(fault_en, decimal),
                "E_Slab (eV)": round_format(struct_en, decimal),
                "E_Equilib (eV)": round_format(equi_en, decimal),
            }
        )

        return build_table(df), df


class PhononReport(PropertyReport):
    @staticmethod
    def plotly_graph(res_data: dict, name: str, **kwargs):
        bands = res_data['band']

        band_path_list = []
        for seg in bands[0]:
            seg_list = [k for k in seg.keys()]
            band_path_list.extend(seg_list)
        band_list = []
        for band in bands:
            seg_result_list = []
            for seg in band:
                seg_result = [v for v in seg.values()]
                seg_result_list.extend(seg_result)
            band_list.append(seg_result_list)
        pd_dict = {"Band Path": band_path_list}
        for ii in range(len(band_list)):
            pd_dict['Band %02d' % (ii + 1)] = band_list[ii]
        df = pd.DataFrame(pd_dict)
        traces = []


        for ii in range(len(band_list)):
            trace = go.Scatter(
                x=df['Band Path'],
                y=df['Band %02d' % (ii + 1)],
                name='Band %02d' % (ii + 1),
                legendgroup=name,
                legendgrouptitle_text=name,
                mode='lines',
                line=dict(color=kwargs["color"], width=1.5)
            )
            traces.append(trace)

        segment_value_list = res_data['segment']
        band_path_info = res_data['band_path']
        segment_value_iter = iter(segment_value_list)

        x_label_list = []
        connect_seg = False
        pre_k = None
        for seg in band_path_info:
            for point in seg:
                k = list(point.keys())[0]
                if connect_seg:
                    new_k = f'{pre_k}/{k}'
                    x_label_list[-1][0] = new_k
                    connect_seg = False
                else:
                    x_label_list.append([k, float(next(segment_value_iter))])
            pre_k = k
            connect_seg = True

        # label special points
        x_label_values_list = [x[1] for x in x_label_list]
        annotations = []
        shapes = []

        for x_label in x_label_list:
            # add label
            annotations.append(go.layout.Annotation(
                x=x_label[1],
                y=1.08,
                xref="x",
                yref="paper",
                text=x_label[0],  # label text
                showarrow=False,
                yshift=0,  # label position
                xanchor='center'
            ))

            # add special vertical line
            '''
            shapes.append({
                'type': 'line',
                'x0': x_label[1],
                'y0': 0,
                'x1': x_label[1],
                'y1': 1,
                'xref': 'x',
                'yref': 'paper',
                'line': {
                    'color': 'grey',
                    'width': 1,
                    'dash': 'dot',
                },
            })
            '''

        layout = go.Layout(
            title='Phonon Spectra',
            annotations=annotations,
            shapes=shapes,
            autotypenumbers='convert types',
            xaxis=dict(
                tickmode='array',
                tickvals=x_label_values_list,
                ticktext=[f'{float(val):.3f}' for val in x_label_values_list],
                title_text="Band Path",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            ),
            yaxis=dict(
                title_text="Frequency (THz)",
                title_font=dict(
                    size=18,
                    color="#7f7f7f"
                )
            )
        )

        return traces, layout

    @staticmethod
    def dash_table(res_data: dict, decimal: int = 3, **kwargs) -> dash_table.DataTable:
        bands = res_data['band']
        band_path_list = []
        for seg in bands[0]:
            seg_list = [float(k) for k in seg.keys()]
            band_path_list.extend(seg_list)
            band_path_list.append(' ')
        band_path_list.pop()

        band_list = []
        for band in bands:
            seg_result_list = []
            for seg in band:
                seg_result = [v for v in seg.values()]
                seg_result_list.extend(seg_result)
                seg_result_list.append(' ')
            seg_result_list.pop()
            band_list.append(round_format(seg_result_list, decimal))

        pd_dict = {"Band Path": round_format(band_path_list, decimal)}
        for ii in range(len(band_list)):
            pd_dict['Band %02d' % (ii + 1)] = band_list[ii]

        df = pd.DataFrame(pd_dict)

        return build_table(df), df
