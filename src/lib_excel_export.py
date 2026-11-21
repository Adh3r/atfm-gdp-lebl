# =============================================================================
# src/lib_excel_export.py
# WP5 - Audit workbook (Excel): KPI dashboard, summary tables and the
# flight-by-flight allocation of every scenario.
# =============================================================================

import os

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Header colours
COLOR_HEADER_GDP = "FF1F4E79"  # dark blue: GDP
COLOR_HEADER_GHP = "FF375623"  # dark green: GHP scenarios
COLOR_HEADER_INTERMODAL = "FF7030A0"  # purple: intermodal scenarios
COLOR_SECTION = "FF2E75B6"
COLOR_ROW_ALT = "FFD9E1F2"
COLOR_ROW_WHITE = "FFFFFFFF"
BASE_FONT = "Arial"

# =============================================================================
# STYLE HELPERS
# =============================================================================


def _thin_border():
    side = Side(style="thin", color="FFD0D0D0")
    return Border(left=side, right=side, top=side, bottom=side)


def _header_style(fill_color=COLOR_HEADER_GDP):
    return {
        'font': Font(name=BASE_FONT, size=11, bold=True, color="FFFFFFFF"),
        'fill': PatternFill("solid", fgColor=fill_color),
        'alignment': Alignment(horizontal="center", vertical="center", wrap_text=True),
        'border': _thin_border(),
    }


def _data_style(even_row=False, bold=False):
    color = COLOR_ROW_ALT if even_row else COLOR_ROW_WHITE
    return {
        'font': Font(name=BASE_FONT, size=10, bold=bold),
        'fill': PatternFill("solid", fgColor=color),
        'alignment': Alignment(horizontal="left", vertical="center", wrap_text=False),
        'border': _thin_border(),
    }


def _autofit_columns(ws, min_width=12, max_width=50):
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell_ in col_cells:
            if cell_.value is not None:
                max_len = max(max_len, len(str(cell_.value)))
        ws.column_dimensions[col_letter].width = min(max_width, max(min_width, max_len * 1.1 + 2))


# =============================================================================
# SHEET WRITER (number format from the column name)
# =============================================================================


def _write_df(ws, df, color_header=COLOR_HEADER_GDP):
    """Write df to ws with styled headers and a number format inferred from each column name."""
    # Headers
    cols = list(df.columns)
    for col_idx, col_name in enumerate(cols, start=1):
        cell_ = ws.cell(row=1, column=col_idx, value=col_name)
        styles = _header_style(color_header)
        cell_.font = styles['font']
        cell_.fill = styles['fill']
        cell_.alignment = styles['alignment']
        cell_.border = styles['border']

    ws.row_dimensions[1].height = 30

    # Number format per column
    col_formats = {}
    for col_idx, col_name in enumerate(cols, start=1):
        if 'EUR' in col_name:
            col_formats[col_idx] = '#,##0.00 €'
        elif '%' in col_name:
            col_formats[col_idx] = '0.00"%"'
        elif 'kg' in col_name or 'min' in col_name:
            col_formats[col_idx] = '#,##0.00'
        else:
            col_formats[col_idx] = 'General'

    # Data
    for row_idx, row in enumerate(df.values, start=2):
        even_row = row_idx % 2 == 0
        is_section = str(row[0]).startswith('--')

        for col_idx, value in enumerate(row, start=1):
            cell_ = ws.cell(row=row_idx, column=col_idx, value=value)
            if is_section:
                cell_.font = Font(name=BASE_FONT, bold=True, color="FFFFFFFF")
                cell_.fill = PatternFill("solid", fgColor=COLOR_SECTION)
            else:
                styles = _data_style(even_row)
                cell_.font = styles['font']
                cell_.fill = styles['fill']
                cell_.border = styles['border']
                cell_.alignment = styles['alignment']

                # number format for numeric cells
                if isinstance(value, (int, float)):
                    cell_.number_format = col_formats[col_idx]

        ws.row_dimensions[row_idx].height = 18


# =============================================================================
# WORKBOOK
# =============================================================================


def export_audit_workbook(
    df_flights_raw: pd.DataFrame,
    scenario_tables: dict,
    df_dashboard: pd.DataFrame,
    df_slots: pd.DataFrame,
    params: dict,
    h_noreg: int,
    timeline: pd.DataFrame,
    newell_min_delay: float,
    path: str,
    df_compressed: pd.DataFrame = None,
    df_intermodal: pd.DataFrame = None,
    summary_tables: dict | None = None,
) -> None:
    """
    Consolidated Excel workbook for the report:

        Dashboard        one row of KPIs per scenario
        Summary sheets           WP3 / WP4 / WP5 tables (summary_tables)
        Audit_<scenario>         flight-by-flight allocation of each scenario
        WP2_Cancellations        cancellations, substitution and compression, flight by flight
        Parameters         regulation parameters, HNoReg, Newell bound
        Slots             slot list
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = Workbook()
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    # 1. Dashboard
    ws_dash = wb.create_sheet("Dashboard", 0)
    _write_df(ws_dash, df_dashboard, COLOR_HEADER_GDP)
    _autofit_columns(ws_dash)
    ws_dash.freeze_panes = "B2"

    # 2. Summary tables (WP3, WP4, WP5)
    for name, table in (summary_tables or {}).items():
        if table is None or table.empty:
            continue
        ws = wb.create_sheet(name[:31])
        _write_df(ws, table, COLOR_HEADER_GDP)
        _autofit_columns(ws)
        ws.freeze_panes = "B2"

    # 3. Flight-by-flight audit of every scenario
    audit_cols = [
        'ARCID',
        'airline',
        'ADEP',
        'ATYP',
        'distance_km',
        'flight_status',
        'minutes_etd',
        'minutes_eta',
        'CTD',
        'CTA',
        'total_delay',
        'air_delay',
        'ground_delay',
        'flight_co2_kg',
    ]
    for name, df in scenario_tables.items():
        if df is None or df.empty:
            continue
        tab_color = "1F4E79"
        if "GHP" in name:
            tab_color = "375623"
        if "Intermodal" in name:
            tab_color = "7030A0"
        ws = wb.create_sheet(f"Audit_{name}"[:31])
        ws.sheet_properties.tabColor = tab_color
        df_audit = df[[c for c in audit_cols if c in df.columns]]
        _write_df(
            ws, df_audit.sort_values('minutes_eta').reset_index(drop=True), color_header="FF" + tab_color
        )
        _autofit_columns(ws)
        ws.freeze_panes = "A2"

    # 4. WP2 cancellations, substitution and compression
    base = next(iter(scenario_tables.values()), None)
    if df_compressed is not None and base is not None:
        _sheet_compression(wb, base, df_compressed)

    # 5. WP4 comparison (kept for backwards compatibility if not in the summaries)
    if (
        df_intermodal is not None
        and not df_intermodal.empty
        and 'WP4_Intermodal' not in (summary_tables or {})
    ):
        ws_int = wb.create_sheet("WP4_Intermodal")
        ws_int.sheet_properties.tabColor = "7030A0"
        _write_df(ws_int, df_intermodal, COLOR_HEADER_INTERMODAL)
        _autofit_columns(ws_int)

    # 6. Parameters and slots
    _sheet_parameters(wb, params, h_noreg, newell_min_delay)
    _sheet_slots(wb, df_slots)

    wb.save(path)
    print(f"   Excel workbook written: {os.path.basename(path)}")


# =============================================================================
# FUNCIONES DE APOYO
# =============================================================================


def compression_table(df_rbs: pd.DataFrame, df_compressed: pd.DataFrame) -> pd.DataFrame:
    """
    Flight-by-flight effect of the cancellations, substitution and
    compression (WP2), after the original sheet by Dènia Cabrera. Rows are
    matched by index (callsigns are not unique in the traffic file).
    """
    before = df_rbs[['ARCID', 'airline', 'flight_status', 'minutes_eta', 'CTA', 'total_delay']].rename(
        columns={'minutes_eta': 'ETA', 'CTA': 'CTA_before', 'total_delay': 'delay_before'}
    )
    after = df_compressed[['assigned_slot', 'total_delay']].rename(
        columns={'assigned_slot': 'CTA_after', 'total_delay': 'delay_after'}
    )
    df = before.join(after, how='left')
    df['status'] = np.select(
        [df['CTA_after'].isna(), df['CTA_after'] < df['CTA_before']],
        ['Cancelled', 'Moved earlier'],
        default='No change',
    )
    df['minutes_saved'] = np.where(
        df['status'] == 'Moved earlier', df['delay_before'] - df['delay_after'], 0.0
    )
    return df.sort_values('ETA').reset_index(drop=True)


def _sheet_compression(wb, df_rbs, df_compressed):
    ws = wb.create_sheet('WP2_Cancellations')
    _write_df(ws, compression_table(df_rbs, df_compressed))
    _autofit_columns(ws)
    ws.freeze_panes = "A2"


def _sheet_parameters(wb, params, h_noreg, newell_min_delay):
    ws = wb.create_sheet("Parameters")
    param_rows = [[k, str(v)] for k, v in params.items()]
    param_rows += [["HNoReg", str(h_noreg)], ["Newell minimum delay (min)", str(round(newell_min_delay, 1))]]
    _write_df(ws, pd.DataFrame(param_rows, columns=["Parameter", "Value"]), COLOR_HEADER_GDP)
    _autofit_columns(ws)


def _sheet_slots(wb, df_slots):
    ws = wb.create_sheet("Slots")
    _write_df(ws, df_slots.reset_index(drop=True), COLOR_HEADER_GDP)
    _autofit_columns(ws)
