# =============================================================================
# src/lib_excel_export.py
# FASE 3: Construcción y formato del Excel maestro de auditoría (Versión Reporte Final)
# =============================================================================

import os

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Colores Corporativos para el Reporte
COLOR_CABECERA_AZUL = "FF1F4E79"  # Azul Oscuro (Profesional)
COLOR_CABECERA_GHP  = "FF375623"  # Verde Oscuro (Para escenarios GHP/Optimizados)
COLOR_CABECERA_INT  = "FF7030A0"  # Púrpura (Para Intermodalidad)
COLOR_SECCION       = "FF2E75B6"
COLOR_FILA_ALTERNA  = "FFD9E1F2"
COLOR_FILA_BLANCA   = "FFFFFFFF"
FUENTE_BASE         = "Arial"

# =============================================================================
# UTILIDADES DE ESTILO
# =============================================================================

def _borde_fino():
    lado = Side(style="thin", color="FFD0D0D0")
    return Border(left=lado, right=lado, top=lado, bottom=lado)

def _estilo_cabecera(color_fondo=COLOR_CABECERA_AZUL):
    return {
        'font':      Font(name=FUENTE_BASE, size=11, bold=True, color="FFFFFFFF"),
        'fill':      PatternFill("solid", fgColor=color_fondo),
        'alignment': Alignment(horizontal="center", vertical="center", wrap_text=True),
        'border':    _borde_fino(),
    }

def _estilo_dato(fila_par=False, negrita=False):
    color = COLOR_FILA_ALTERNA if fila_par else COLOR_FILA_BLANCA
    return {
        'font':      Font(name=FUENTE_BASE, size=10, bold=negrita),
        'fill':      PatternFill("solid", fgColor=color),
        'alignment': Alignment(horizontal="left", vertical="center", wrap_text=False),
        'border':    _borde_fino(),
    }

def _autoajustar_columnas(ws, ancho_minimo=12, ancho_maximo=50):
    for col_cells in ws.columns:
        max_len = 0
        col_letra = get_column_letter(col_cells[0].column)
        for celda in col_cells:
            if celda.value is not None:
                max_len = max(max_len, len(str(celda.value)))
        ws.column_dimensions[col_letra].width = min(ancho_maximo, max(ancho_minimo, max_len * 1.1 + 2))

# =============================================================================
# MOTOR DE ESCRITURA CON AUTO-FORMATEO DE KPIs
# =============================================================================

def _escribir_dataframe_con_formato(ws, df, color_header=COLOR_CABECERA_AZUL):
    """Escribe un DF aplicando estilos y auto-detectando formatos de número según la columna."""
    # Cabeceras
    columnas = list(df.columns)
    for col_idx, nombre_col in enumerate(columnas, start=1):
        celda = ws.cell(row=1, column=col_idx, value=nombre_col)
        estilos = _estilo_cabecera(color_header)
        celda.font = estilos['font']; celda.fill = estilos['fill']
        celda.alignment = estilos['alignment']; celda.border = estilos['border']
    
    ws.row_dimensions[1].height = 30

    # Analizar formatos de columnas basados en el nombre
    formatos_columna = {}
    for col_idx, nombre_col in enumerate(columnas, start=1):
        if 'EUR' in nombre_col:
            formatos_columna[col_idx] = '#,##0.00 €'
        elif '%' in nombre_col:
            formatos_columna[col_idx] = '0.00"%"'
        elif 'kg' in nombre_col or 'min' in nombre_col:
            formatos_columna[col_idx] = '#,##0.00'
        else:
            formatos_columna[col_idx] = 'General'

    # Datos
    for fila_idx, row in enumerate(df.values, start=2):
        fila_par = (fila_idx % 2 == 0)
        es_seccion = str(row[0]).startswith('--')
        
        for col_idx, valor in enumerate(row, start=1):
            celda = ws.cell(row=fila_idx, column=col_idx, value=valor)
            if es_seccion:
                celda.font = Font(name=FUENTE_BASE, bold=True, color="FFFFFFFF")
                celda.fill = PatternFill("solid", fgColor=COLOR_SECCION)
            else:
                estilos = _estilo_dato(fila_par)
                celda.font = estilos['font']; celda.fill = estilos['fill']
                celda.border = estilos['border']
                celda.alignment = estilos['alignment']
                
                # Aplicar formato de número si es numérico
                if isinstance(valor, (int, float)):
                    celda.number_format = formatos_columna[col_idx]
                    
        ws.row_dimensions[fila_idx].height = 18

# =============================================================================
# ORQUESTADOR PRINCIPAL
# =============================================================================

def exportar_auditoria_excel(
    df_vuelos_crudo: pd.DataFrame,
    escenarios_dict: dict,
    df_dashboard: pd.DataFrame,
    df_slots: pd.DataFrame,
    params: dict,
    h_noreg: int,
    timeline: pd.DataFrame,
    r_min_newell: float,
    path: str,
    df_res_comprimido: pd.DataFrame = None,
    df_intermodal: pd.DataFrame = None,
    tablas_resumen: dict | None = None,
) -> None:
    """
    Consolidated Excel workbook for the report:

        00_Dashboard_KPIs        one row of KPIs per scenario
        Summary sheets           WP3 / WP4 / WP5 tables (tablas_resumen)
        Audit_<scenario>         flight-by-flight allocation of each scenario
        WP2_Compression          penalty-and-compression, flight by flight
        Config_Escenario         regulation parameters, HNoReg, Newell bound
        Config_Slots             slot list
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = Workbook()
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    # 1. Dashboard
    ws_dash = wb.create_sheet("00_Dashboard_KPIs", 0)
    _escribir_dataframe_con_formato(ws_dash, df_dashboard, COLOR_CABECERA_AZUL)
    _autoajustar_columnas(ws_dash)
    ws_dash.freeze_panes = "B2"

    # 2. Summary tables (WP3, WP4, WP5)
    for nombre, tabla in (tablas_resumen or {}).items():
        if tabla is None or tabla.empty:
            continue
        ws = wb.create_sheet(nombre[:31])
        _escribir_dataframe_con_formato(ws, tabla, COLOR_CABECERA_AZUL)
        _autoajustar_columnas(ws)
        ws.freeze_panes = "B2"

    # 3. Flight-by-flight audit of every scenario
    cols_audit = [
        'ARCID', 'airline', 'ADEP', 'ATYP', 'distancia_km', 'flight_status',
        'minutes_etd', 'minutes_eta', 'CTD', 'CTA',
        'total_delay', 'air_delay', 'ground_delay', 'co2_kg_vuelo',
    ]
    for nombre, df in escenarios_dict.items():
        if df is None or df.empty:
            continue
        color_tab = "1F4E79"
        if "GHP" in nombre:
            color_tab = "375623"
        if "Intermodal" in nombre:
            color_tab = "7030A0"
        ws = wb.create_sheet(f"Audit_{nombre}"[:31])
        ws.sheet_properties.tabColor = color_tab
        df_audit = df[[c for c in cols_audit if c in df.columns]]
        _escribir_dataframe_con_formato(
            ws, df_audit.sort_values('minutes_eta').reset_index(drop=True), color_header="FF" + color_tab)
        _autoajustar_columnas(ws)
        ws.freeze_panes = "A2"

    # 4. WP2 penalty and compression
    base = next(iter(escenarios_dict.values()), None)
    if df_res_comprimido is not None and base is not None:
        _sheet_compresion(wb, base, df_res_comprimido)

    # 5. WP4 comparison (kept for backwards compatibility if not in the summaries)
    if df_intermodal is not None and not df_intermodal.empty and 'WP4_Intermodal' not in (tablas_resumen or {}):
        ws_int = wb.create_sheet("WP4_Intermodal")
        ws_int.sheet_properties.tabColor = "7030A0"
        _escribir_dataframe_con_formato(ws_int, df_intermodal, COLOR_CABECERA_INT)
        _autoajustar_columnas(ws_int)

    # 6. Parameters and slots
    _sheet_parametros(wb, params, h_noreg, r_min_newell)
    _sheet_matriz_slots(wb, df_slots)

    wb.save(path)
    print(f"✅ Excel workbook written: {os.path.basename(path)}")

# =============================================================================
# FUNCIONES DE APOYO
# =============================================================================

def tabla_compresion(df_res: pd.DataFrame, df_res_comprimido: pd.DataFrame) -> pd.DataFrame:
    """
    Flight-by-flight effect of penalty and compression (WP2), as in the
    original sheet by Dènia Cabrera. Rows are matched by index (callsigns are
    not unique in the traffic file).
    """
    orig = df_res[['ARCID', 'airline', 'flight_status', 'minutes_eta', 'CTA', 'total_delay']].rename(
        columns={'minutes_eta': 'ETA', 'CTA': 'CTA_before', 'total_delay': 'delay_before'})
    comp = df_res_comprimido[['assigned_slot', 'total_delay']].rename(
        columns={'assigned_slot': 'CTA_after', 'total_delay': 'delay_after'})
    df = orig.join(comp, how='left')
    df['status'] = np.select(
        [df['CTA_after'].isna(), df['CTA_after'] < df['CTA_before']],
        ['Cancelled (penalty)', 'Moved earlier (compression)'],
        default='No change',
    )
    df['minutes_saved'] = np.where(df['status'] == 'Moved earlier (compression)',
                                   df['delay_before'] - df['delay_after'], 0.0)
    return df.sort_values('ETA').reset_index(drop=True)


def _sheet_compresion(wb, df_res, df_res_comprimido):
    ws = wb.create_sheet('WP2_Compression')
    _escribir_dataframe_con_formato(ws, tabla_compresion(df_res, df_res_comprimido))
    _autoajustar_columnas(ws)
    ws.freeze_panes = "A2"


def _sheet_parametros(wb, params, h_noreg, r_min_newell):
    ws = wb.create_sheet("Config_Escenario")
    datos = [[k, str(v)] for k, v in params.items()]
    datos += [["HNoReg", str(h_noreg)], ["Newell minimum delay (min)", str(round(r_min_newell, 1))]]
    _escribir_dataframe_con_formato(ws, pd.DataFrame(datos, columns=["Parameter", "Value"]), COLOR_CABECERA_AZUL)
    _autoajustar_columnas(ws)


def _sheet_matriz_slots(wb, df_slots):
    ws = wb.create_sheet("Config_Slots")
    _escribir_dataframe_con_formato(ws, df_slots.reset_index(drop=True), COLOR_CABECERA_AZUL)
    _autoajustar_columnas(ws)
