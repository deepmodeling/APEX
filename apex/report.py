import glob
import os
import logging
from typing import List
from monty.serialization import loadfn
import json
from html import escape as html_escape
from apex.config import Config
from apex.utils import load_config_file, is_json_file, simplify_paths
from apex.reporter.DashReportApp import DashReportApp
import os


def tag_dataset(orig_dataset: dict) -> dict:
    orig_work_path_list = [k for k in orig_dataset.keys()]
    try:
        simplified_path_dict = simplify_paths(orig_work_path_list)
        simplified_dataset = {simplified_path_dict[k]: v for k, v in orig_dataset.items()}
    except KeyError:
        simplified_dataset = orig_dataset
    # replace data id with tag specified in the dataset if exists
    tagged_dataset = {}
    for k, v in simplified_dataset.items():
        if tag := v.pop('tag', None):
            tagged_dataset[tag] = v
        else:
            tagged_dataset[k] = v
    return tagged_dataset


def report_local(input_path_list):
    path_list = []
    for ii in input_path_list:
        glob_list = glob.glob(os.path.abspath(ii))
        path_list.extend(glob_list)
        path_list.sort()

    if not path_list:
        raise RuntimeError('Invalid work path indicated. No path has been found!')

    file_path_list = []
    for jj in path_list:
        if os.path.isfile(jj) and is_json_file(jj):
            file_path_list.append(jj)
        elif os.path.isdir(jj) and os.path.isfile(os.path.join(jj, 'all_result.json')):
            file_path_list.append(os.path.join(jj, 'all_result.json'))
        else:
            raise FileNotFoundError(f'Invalid work path or json file path provided: {jj}')

    if not file_path_list:
        raise FileNotFoundError(
            'all_result.json not exist or not under work path indicated. Please do result archive locally first.'
        )
    all_data_dict = {}
    for kk in file_path_list:
        data_dict = loadfn(kk)
        try:
            workdir_id = data_dict.pop('work_path')
            _ = data_dict.pop('archive_key')
        except KeyError:
            logging.warning(msg=f'Invalid json for result archive, will skip: {kk}')
            continue
        else:
            all_data_dict[workdir_id] = data_dict

    # simplify the work path key for all datasets
    simplified_dataset = tag_dataset(all_data_dict)
    # Use non-debug, no reloader, bind to localhost to avoid env-dependent issues
    # Allow overriding port via env APEX_REPORT_PORT (default 8050)
    port = int(os.getenv('APEX_REPORT_PORT', '8050'))
    DashReportApp(datasets=simplified_dataset).run(debug=False, use_reloader=False, host='127.0.0.1', port=port)


def report_result(config_dict: dict, path_list: List[os.PathLike]):
    config = Config(**config_dict)
    report_local(path_list)

def _generate_static_html(all_result_json: os.PathLike, output_html: os.PathLike = None):
    data = loadfn(all_result_json)
    work_path = data.get('work_path', str(os.path.dirname(all_result_json)))
    # Build simple HTML
    parts = []
    parts.append('<!DOCTYPE html>')
    parts.append('<html><head><meta charset="utf-8"><title>APEX Static Report</title>')
    parts.append('<style>body{font-family:sans-serif;margin:1.5rem} table{border-collapse:collapse;margin:1rem 0} th,td{border:1px solid #ccc;padding:6px 10px} h2{margin-top:2rem} pre{background:#f8f8f8;padding:10px;overflow:auto}</style>')
    parts.append('</head><body>')
    parts.append(f'<h1>APEX Static Report</h1><p>Workdir: {html_escape(work_path)}</p>')

    def section(title):
        parts.append(f'<h2>{html_escape(title)}</h2>')

    # Iterate configurations
    for conf_key, conf_val in data.items():
        if conf_key in ('work_path', 'archive_key'):
            continue
        if not isinstance(conf_val, dict):
            continue
        section(f'Configuration: {conf_key}')
        # Relaxation summary (final cell)
        try:
            rel = conf_val.get('relaxation', {})
            cell = rel['result']['data']['cells'][-1]
            parts.append('<h3>Relaxation (final cell)</h3>')
            parts.append('<table>')
            for row in cell:
                parts.append('<tr>' + ''.join(f'<td>{v:.6f}</td>' for v in row) + '</tr>')
            parts.append('</table>')
        except Exception:
            pass

        # Properties
        for pk, pv in conf_val.items():
            if pk == 'relaxation':
                continue
            parts.append(f'<h3>Property: {html_escape(pk)}</h3>')
            res = pv.get('result', {})
            # Elastic tensor summary
            if isinstance(res, dict) and 'elastic_tensor' in res:
                et = res['elastic_tensor']
                parts.append('<table>')
                for row in et:
                    parts.append('<tr>' + ''.join(
                        f'<td>{(x if isinstance(x, str) else f"{x:.4f}")}</td>' for x in row
                    ) + '</tr>')
                parts.append('</table>')
                for tag in ('B', 'G', 'E', 'u'):
                    if tag in res:
                        parts.append(f'<p>{tag}: {res[tag]}</p>')
                continue
            # Lat_param_T new dict format -> include 0K from relaxation
            if isinstance(res, dict) and res and all(isinstance(vv, dict) for vv in res.values()):
                # collect values
                temp_map = {}
                for tk, tv in res.items():
                    try:
                        t = float(tk)
                    except Exception:
                        t = float(tv.get('temperature', 0.0))
                    temp_map[t] = {
                        'a': tv.get('a'), 'b': tv.get('b'), 'c': tv.get('c'),
                        'c_over_a': tv.get('c_over_a')
                    }
                # append 0K from relaxation if available
                try:
                    rel = conf_val.get('relaxation', {})
                    cell = rel['result']['data']['cells'][-1]
                    import math
                    a0 = (cell[0][0]**2 + cell[0][1]**2 + cell[0][2]**2)**0.5
                    b0 = (cell[1][0]**2 + cell[1][1]**2 + cell[1][2]**2)**0.5
                    c0 = (cell[2][0]**2 + cell[2][1]**2 + cell[2][2]**2)**0.5
                    temp_map.setdefault(0.0, {'a': a0, 'b': b0, 'c': c0, 'c_over_a': (c0/a0 if a0 else None)})
                except Exception:
                    pass
                # table
                parts.append('<table><tr><th>Temp (K)</th><th>a (A)</th><th>b (A)</th><th>c (A)</th><th>c/a</th></tr>')
                for t in sorted(temp_map.keys()):
                    tv = temp_map[t]
                    a, b, c, ca = tv.get('a'), tv.get('b'), tv.get('c'), tv.get('c_over_a')
                    def cellf(x):
                        return f'{x:.6f}' if isinstance(x, (int, float)) else ''
                    tstr = str(int(t)) if abs(t-round(t))<1e-6 else f'{t:.2f}'
                    parts.append('<tr>' +
                                 f'<td>{html_escape(tstr)}</td>'+
                                 f'<td>{cellf(a)}</td><td>{cellf(b)}</td><td>{cellf(c)}</td><td>{cellf(ca)}</td>'+
                                 '</tr>')
                parts.append('</table>')
                # simple inline plot (SVG) for a,b,c vs T
                try:
                    ts = sorted(temp_map.keys())
                    ays = [float(temp_map[t]['a']) for t in ts]
                    bys = [float(temp_map[t]['b']) for t in ts]
                    cys = [float(temp_map[t]['c']) for t in ts]
                    import math
                    def mkpoly(xs, ys, color):
                        # scale to 600x200 SVG
                        w,h=600,200
                        xmin,xmax=min(xs),max(xs)
                        ymin,ymax=min(ys),max(ys)
                        xr = (xmax-xmin) or 1.0
                        yr = (ymax-ymin) or 1.0
                        pts=['{:.1f},{:.1f}'.format(40+(x-xmin)/xr*(w-60), 10+(ymax-y)/yr*(h-40)) for x,y in zip(xs,ys)]
                        return f'<polyline fill="none" stroke="{color}" stroke-width="2" points="'+' '.join(pts)+'" />'
                    xs = ts
                    svg = '<svg width="680" height="240" xmlns="http://www.w3.org/2000/svg">'
                    svg += '<rect x="1" y="1" width="678" height="238" fill="white" stroke="#ccc" />'
                    svg += mkpoly(xs, ays, 'blue')
                    svg += mkpoly(xs, bys, 'green')
                    svg += mkpoly(xs, cys, 'red')
                    svg += '</svg>'
                    parts.append(svg)
                except Exception:
                    pass
                continue
            # Fallback: pretty JSON
            parts.append('<pre>' + html_escape(json.dumps(res, indent=2, ensure_ascii=False)) + '</pre>')

        # Annealing (RDF + intervals) fallback display: scan filesystem for outputs
        try:
            # Work out absolute conf directory
            conf_dir = conf_key
            if not os.path.isabs(conf_dir):
                conf_dir = os.path.join(work_path, conf_dir)
            if os.path.isdir(conf_dir):
                # find annealing_* dirs
                anns = [d for d in os.listdir(conf_dir) if d.startswith('annealing_') and os.path.isdir(os.path.join(conf_dir, d))]
                for ann in sorted(anns):
                    ann_dir = os.path.join(conf_dir, ann)
                    # tasks under annealing dir
                    tasks = [t for t in os.listdir(ann_dir) if t.startswith('task.') and os.path.isdir(os.path.join(ann_dir, t))]
                    if not tasks:
                        continue
                    parts.append(f'<h3>Annealing: {html_escape(ann)}</h3>')
                    parts.append('<table><tr><th>Task</th><th>heating_interval.dat</th><th>cooling_interval.dat</th><th>rdf_ramp.dat</th><th>rdf_cool.dat</th></tr>')
                    for t in sorted(tasks):
                        tdir = os.path.join(ann_dir, t)
                        h = os.path.exists(os.path.join(tdir, 'heating_interval.dat'))
                        c = os.path.exists(os.path.join(tdir, 'cooling_interval.dat'))
                        rr = os.path.exists(os.path.join(tdir, 'rdf_ramp.dat'))
                        rc = os.path.exists(os.path.join(tdir, 'rdf_cool.dat'))
                        def yn(x):
                            return 'yes' if x else ''
                        parts.append('<tr>' + ''.join([
                            f'<td>{html_escape(t)}</td>',
                            f'<td>{yn(h)}</td>',
                            f'<td>{yn(c)}</td>',
                            f'<td>{yn(rr)}</td>',
                            f'<td>{yn(rc)}</td>',
                        ]) + '</tr>')
                    parts.append('</table>')
        except Exception:
            pass

    parts.append('</body></html>')
    if output_html is None:
        output_html = os.path.join(os.path.dirname(all_result_json), 'report_static.html')
    with open(output_html, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(parts))
    return output_html


def report_static(path_list: List[os.PathLike]):
    # Resolve work paths or json
    globbed = []
    for p in path_list:
        globbed.extend(glob.glob(os.path.abspath(p)))
    if not globbed:
        raise RuntimeError('Invalid work path indicated. No path has been found!')
    outputs = []
    for p in globbed:
        if os.path.isfile(p) and is_json_file(p):
            outputs.append(_generate_static_html(p))
        elif os.path.isdir(p) and os.path.isfile(os.path.join(p, 'all_result.json')):
            outputs.append(_generate_static_html(os.path.join(p, 'all_result.json')))
        else:
            logging.warning(f'Skip invalid path: {p}')
    if not outputs:
        raise FileNotFoundError('No all_result.json found under given paths. Please do result archive first.')
    print('Static report generated:')
    for o in outputs:
        print(' -', o)


def report_from_args(config_file, path_list, static: bool = False):
    if static:
        print('-------Static Report Mode-------')
        report_static(path_list)
        print('Complete!')
        return
    print('-------Report Visualization Mode-------')
    report_result(
        config_dict=load_config_file(config_file),
        path_list=path_list
    )
    print('Complete!')
