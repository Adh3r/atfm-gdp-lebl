"""Render the README figures (PNG) into docs/figures/.

They are produced by the same plotting functions as the pipeline (which writes
SVG to output/figures/), from the traffic file selected by config.traffic_file().
The committed figures were generated from the real 10 August 2025 traffic day.

Usage (from the repository root):
    python tools/make_readme_figures.py
"""

import contextlib
import io
import os
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.figure as mfig  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
OUT = os.path.join(ROOT, 'docs', 'figures')

WANTED = {
    '1_diagrama_newell.svg': 'newell_curves.png',
    'fig3_fairness_by_airline.svg': 'wp3_fairness_by_airline.png',
    'fig2_cost_of_delay_curves.svg': 'wp3_cost_of_delay_curves.png',
    'fig3_queue_newell.svg': 'wp4_queue_newell.png',
    'wp5_cost_and_co2_by_method.svg': 'wp5_cost_and_co2_by_method.png',
}


def main() -> None:
    import config
    import generar_graficos_wp4 as gw
    import lib_comparison as comp
    import lib_data_prep as prep
    import lib_gdp_core as gdp
    import lib_ghp_solver as ghp
    import lib_intermodal as inter
    import lib_visualization as vis
    from config import CFG

    os.makedirs(OUT, exist_ok=True)
    original = mfig.Figure.savefig
    tmp = os.path.join(OUT, '_tmp')

    def savefig(self, fname, *args, **kwargs):
        name = os.path.basename(str(fname))
        if name in WANTED:
            kwargs.pop('format', None)
            kwargs.pop('transparent', None)
            original(self, os.path.join(OUT, WANTED[name]), dpi=110, bbox_inches='tight', facecolor='white')

    mfig.Figure.savefig = savefig
    import matplotlib.pyplot as plt
    plt_savefig = plt.savefig
    plt.savefig = lambda fname, *a, **k: savefig(plt.gcf(), fname, *a, **k)
    try:
        params = CFG.to_params_dict()
        with contextlib.redirect_stdout(io.StringIO()):
            df = prep.preparar_vuelos(config.traffic_file(), config.FLEET_TABLE)
            res = gdp.ejecutar_nucleo_gdp(df, params, run_compression=False)
            rbs = res['vuelos_asignados']
            res_ghp = ghp.ejecutar_ghp_completo(rbs, list(res['slots']['slot_start_min']), params)
            wp4 = inter.ejecutar_analisis_intermodal(df, res, res_ghp, params, tmp)
        vis.plot_newell(res['timeline'], params, res['h_noreg'], '1_diagrama_newell.svg')
        ghp.generar_graficas_wp3({'GDP': rbs, 'GHP_Emissions': res_ghp['GHP_Emissions'],
                                  'GHP_Cost': res_ghp['GHP_Cost']}, tmp)
        gw.generar_graficas_wp4(res, wp4, tmp)
        comp.grafica_final(wp4['kpis'], tmp)
    finally:
        mfig.Figure.savefig = original
        plt.savefig = plt_savefig
        shutil.rmtree(tmp, ignore_errors=True)
    print('Figures written to', os.path.relpath(OUT, ROOT), sorted(os.listdir(OUT)))


if __name__ == '__main__':
    main()
