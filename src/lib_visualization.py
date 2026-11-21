# =============================================================================
# src/lib_visualization.py
# WP2 figures (SVG). Plotting only; no model logic lives here.
#
#   plot_newell()              Newell cumulative demand / capacity curves
#   plot_capacity_balance()   arrivals per hour before (ETA) and after (CTA) the regulation
#   plot_cost_vs_do_nothing()   cost of delay: do-nothing vs. GDP
#   plot_airline_fairness()  mean delay per airline under RBS (top 10)
#   plot_wp2_figures()   draws the four figures above
#   plot_heatmap()          one sensitivity heatmap (R x file time)
# =============================================================================

import os

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.colors import LogNorm

# Same function as the Excel workbook, so the numbers always match.
from lib_gdp_core import gdp_vs_do_nothing

# =============================================================================
# SENSITIVITY HEATMAP (WP2)
# =============================================================================

def plot_heatmap(
    matrix: pd.DataFrame,
    title: str,
    unit: str,
    best: str,
    out_path: str,
    best_row: int,
    best_col: int,
) -> None:
    """
    Heatmap of one KPI over the radius of exemption R (columns) and the file
    time Hstart - Hfile (rows), with the best cell outlined.

    The colour scale is logarithmic (the KPIs span orders of magnitude across
    the grid); green is good, red is bad.

    Args:
        matrix:      KPI matrix (rows Hstart - Hfile, columns R).
        title:         figure title.
        unit:         unit for the colour bar ('min', 'kg', 'EUR').
        best:          'min' if lower is better, 'max' if higher is better.
        out_path:    SVG path.
        best_row:    row index of the best cell in matrix.
        best_col: column index of the best cell.
    """
    # Largest file-time offset at the top; move the best-cell marker with it.
    matrix = matrix.sort_index(ascending=False)
    best_row = (len(matrix) - 1) - best_row

    plt.figure(figsize=(12, 7))
    values_list = matrix.values.flatten()
    vmin = max(values_list.min(), 0.1)    # LogNorm needs positive limits
    vmax = values_list.max()

    cmap = plt.get_cmap("RdYlGn" if best == 'max' else "RdYlGn_r")
    number_format_str = ",.0f" if any(x in unit for x in ["EUR", "kg"]) else ".1f"

    ax = sns.heatmap(
        matrix,
        annot=True,
        fmt=number_format_str,
        cmap=cmap,
        norm=LogNorm(vmin=vmin, vmax=vmax),
        cbar_kws={'label': unit},
        linewidths=1.5,
        linecolor='white',
        annot_kws={"size": 8, "weight": "bold"},
    )
    ax.set_xticklabels([f"{int(c)} km"  for c in matrix.columns])
    ax.set_yticklabels([f"{int(i)} min" for i in matrix.index], rotation=0)
    ax.set_xlabel('Radius of exemption R (km)', fontsize=11, fontweight='bold')
    ax.set_ylabel('File time: Hstart − Hfile (min)', fontsize=11, fontweight='bold')
    ax.set_title(title.upper(), fontsize=13, fontweight='bold', pad=20)

    ax.add_patch(plt.Rectangle(
        (best_col, best_row), 1, 1,
        fill=False, edgecolor='blue', linewidth=4, zorder=15,
    ))
    ax.text(
        best_col + 0.5, best_row + 0.9, '★ BEST',
        ha='center', va='bottom', fontsize=10, color='blue', fontweight='bold',
        bbox=dict(facecolor='white', alpha=0.9, edgecolor='blue', boxstyle='round,pad=0.2'),
    )

    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# 1. NEWELL CUMULATIVE CURVES
# =============================================================================

def plot_newell(
    timeline: pd.DataFrame,
    params: dict,
    h_noreg: int,
    out_path: str,
) -> None:
    """
    Newell cumulative curves: cumulative demand (by ETA) and cumulative
    capacity (AAR, PAAR during the capacity drop). The area between them is
    the total delay; the vertical lines mark Hstart, Hend and HNoReg.

    Args:
        timeline:    Newell timeline ('minute', 'demand_accum', 'capacity_accum').
        params:      GDP parameters with H_START and H_END.
        h_noreg:     HNoReg (minute the queue dissolves).
        out_path: SVG path.
    """
    plt.figure(figsize=(12, 6))
    hours = timeline['minute'] / 60

    plt.plot(hours, timeline['demand_accum'],
             label='Cumulative demand (ETA)', color='cornflowerblue', linewidth=2)
    plt.plot(hours, timeline['capacity_accum'],
             '--', label='Cumulative capacity (AAR / PAAR)', color='hotpink', linewidth=2)
    plt.fill_between(hours, timeline['demand_accum'], timeline['capacity_accum'],
                     color='plum', alpha=0.5, label='Queue (delay)')

    plt.axvline(x=params['H_START'] / 60, color='palevioletred',    linestyle=':',  label='Hstart (reduced capacity starts)')
    plt.axvline(x=params['H_END']   / 60, color='mediumaquamarine', linestyle=':',  label='Hend (reduced capacity ends)')
    plt.axvline(x=h_noreg           / 60, color='midnightblue',     linestyle='-.', label='HNoReg (queue dissolved)')

    plt.title("Newell cumulative curves", fontsize=20, fontweight='bold')
    plt.xlabel("UTC time (hours)", fontsize=18)
    plt.ylabel("Cumulative number of aircraft", fontsize=18)
    plt.legend()
    plt.grid(True, alpha=0.4)
    plt.tight_layout()
    plt.savefig(out_path, format='svg', dpi=300, bbox_inches='tight')
    plt.close()

# =============================================================================
# 2. HOURLY CAPACITY BALANCE
# =============================================================================

def plot_capacity_balance(
    df_flights: pd.DataFrame,
    df_rbs: pd.DataFrame,
    params: dict,
    out_path: str,
) -> None:
    """
    The two histograms of the WP2 statement: arrivals per hour before the
    regulation (by ETA) and once it is applied (by CTA for the affected
    flights, ETA for the others), against the declared capacity (PAAR during
    the capacity drop, AAR otherwise). It checks the GDP: regulated arrivals
    never exceed the capacity of the hour.

    Args:
        df_flights: all arrivals with 'minutes_eta'.
        df_rbs:     affected flights with their 'CTA' (same index as df_flights).
        params:     GDP parameters (H_START, H_END, AAR, PAAR).
        out_path:   SVG path.
    """
    plt.figure(figsize=(12, 6))

    eta_hour = (df_flights['minutes_eta'] // 60).astype(int)
    hourly_demand = eta_hour.value_counts().reindex(range(24), fill_value=0).sort_index()

    arrival = df_flights['minutes_eta'].copy()
    arrival.loc[df_rbs.index] = df_rbs['CTA']
    served = (arrival // 60).astype(int).value_counts().reindex(range(24), fill_value=0).sort_index()

    h_first, h_last = int(params['H_START'] / 60), int(params['H_END'] / 60)
    capacity = [params['PAAR'] if h_first <= h < h_last else params['AAR'] for h in range(24)]

    hours = range(24)
    plt.bar(hours, hourly_demand, color='cornflowerblue', alpha=0.5, width=0.8, edgecolor='black', label='Before the regulation (ETA)')
    plt.bar(hours, served, color='midnightblue', alpha=0.8, width=0.4, edgecolor='black', label='Regulated (CTA)')
    plt.step(hours, capacity, where='mid', color='hotpink', linewidth=3, label='Capacity (AAR / PAAR)')

    plt.title('Arrivals per hour before and after the regulation', fontsize=20, fontweight='bold')
    plt.xlabel('Time of day (UTC hour)', fontsize=18)
    plt.ylabel('Arrivals per hour', fontsize=18)
    plt.xticks(hours)
    plt.legend(loc='upper left')
    plt.grid(True, axis='y', linestyle='--', alpha=0.6)
    plt.tight_layout()
    plt.savefig(out_path, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# 3. COST OF DELAY: DO-NOTHING VS. GDP
# =============================================================================

def plot_cost_vs_do_nothing(df_rbs: pd.DataFrame, out_path: str) -> None:
    """
    Cost of delay with the GDP against the do-nothing case (the same delay
    absorbed in airborne holding), both from the Cook & Tanner model of
    cost_model (see gdp_vs_do_nothing).

    Args:
        df_rbs:      RBS result with delays.
        out_path: SVG path.
    """
    kpis = gdp_vs_do_nothing(df_rbs)
    labels = [
        'Do-nothing\n(all delay airborne)',
        'GDP\n(delay moved to the ground)',
    ]
    values_list = [kpis['cost_baseline'], kpis['cost_gdp']]

    plt.figure(figsize=(10, 6))
    bars = plt.bar(labels, values_list, color=['crimson', 'mediumseagreen'], edgecolor='black', width=0.6)
    plt.title("Cost of delay: GDP vs. do-nothing", fontsize=14, fontweight='bold')
    plt.ylabel("Cost of delay, Cook & Tanner 2015 (EUR, 2022 prices)", fontsize=12)

    vmax_bar = max(values_list)
    plt.ylim(0, vmax_bar * 1.30)
    for bar_patch in bars:
        height = bar_patch.get_height()
        plt.text(bar_patch.get_x() + bar_patch.get_width() / 2, height + vmax_bar * 0.03,
                 f"EUR {int(height):,}", ha='center', va='bottom', fontweight='bold', fontsize=11)

    plt.grid(axis='y', linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(out_path, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# 4. RBS FAIRNESS BY AIRLINE
# =============================================================================

def plot_airline_fairness(df_rbs: pd.DataFrame, out_path: str) -> None:
    """
    Mean delay per airline under RBS, for the 10 airlines with most affected
    flights, against the overall mean.

    Args:
        df_rbs:      RBS result with delays.
        out_path: SVG path.
    """
    top_10 = df_rbs['airline'].value_counts().head(10).index
    means = (
        df_rbs[df_rbs['airline'].isin(top_10)]
        .groupby('airline')['total_delay'].mean()
        .sort_values(ascending=False)
    )

    plt.figure(figsize=(12, 6))
    plt.bar(means.index, means.values, color='mediumpurple', edgecolor='black', alpha=0.8)
    overall_mean = df_rbs['total_delay'].mean()
    plt.axhline(y=overall_mean, color='red', linestyle='--', linewidth=2,
                label=f"Mean delay, all affected flights ({overall_mean:.1f} min)")

    plt.title("RBS fairness: mean delay per airline (top 10 by affected flights)", fontsize=14, fontweight='bold')
    plt.xlabel("Airline (ICAO designator)", fontsize=12)
    plt.ylabel("Mean delay (min)", fontsize=12)
    plt.legend()
    plt.grid(axis='y', linestyle='--', alpha=0.6)
    plt.tight_layout()
    plt.savefig(out_path, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# ORCHESTRATOR
# =============================================================================

def plot_wp2_figures(
    timeline: pd.DataFrame,
    df_flights: pd.DataFrame,
    df_rbs: pd.DataFrame,
    params: dict,
    h_noreg: int,
    paths: dict,
) -> None:
    """Draw the four WP2 figures (always as SVG, whatever extension paths has)."""
    folder = os.path.dirname(paths['cum'])
    os.makedirs(folder, exist_ok=True)

    plot_newell(timeline, params, h_noreg, paths['cum'].replace('.png', '.svg'))
    plot_capacity_balance(df_flights, df_rbs, params, paths['bal'].replace('.png', '.svg'))
    plot_cost_vs_do_nothing(df_rbs, os.path.join(folder, '3_cost_gdp_vs_do_nothing.svg'))
    plot_airline_fairness(df_rbs, os.path.join(folder, '4_rbs_fairness_by_airline.svg'))
    print("   WP2 figures (SVG) written to output/figures/")


# =============================================================================
# DEBUG MODE: python src/lib_visualization.py
# =============================================================================
if __name__ == "__main__":
    import config
    import lib_data_prep as prep
    import lib_gdp_core as gdp
    from config import CFG

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_folder = os.path.join(base, 'output', 'figures', 'TEST_VISUALS')
    params = CFG.to_params_dict()

    df_flights = prep.prepare_flights(config.traffic_file(), config.FLEET_TABLE)
    results = gdp.run_gdp(df_flights, params)
    plot_wp2_figures(
        timeline=results['timeline'],
        df_flights=df_flights,
        df_rbs=results['allocation'],
        params=params,
        h_noreg=results['h_noreg'],
        paths={'cum': os.path.join(test_folder, '1_newell.svg'),
               'bal': os.path.join(test_folder, '2_balance.svg')},
    )
