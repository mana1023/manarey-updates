import json
import os
import platform
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PyQt5.QtCore import QAbstractTableModel, QDate, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QKeySequence, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import db as db_mod
from models import ventas_historial_model as vhm
from models import ventas_model as vm
from models.firestore_db import get_all_locals

# Colores modernos (igual que tabla de stock)
BG = "#0f172a"  # slate-900
BG_ALT = "#111827"  # gray-900
TEXT = "#e5e7eb"  # gray-200
TEXT_MUTED = "#94a3b8"  # slate-400
GRID = "#334155"  # slate-700
HEAD_BG = "#0b1220"
PRIMARY = "#C9A040"  # dorado consistente con el resto de la app
BADGE_SOFT = "#b8923a"  # dorado apagado para badges
BADGE_TEXT = "#1a1a1a"
PENDING_BAR = "#b8923a"  # barra lateral para pendientes
BORDER = "#1f2937"
CARD = "#1e293b"
DARK = "#0f172a"
DORADO = "#C9A040"
ERROR_BG = "#dc2626"

# Locales disponibles (admin) como respaldo
LOCALES = ["Todos", "Cane", "Vidriera", "Longchamps", "Glew", "Estacion"]
_APPDATA = os.environ.get("APPDATA")
if _APPDATA:
    PREFS_DIR = Path(_APPDATA) / "Manarey"
else:
    PREFS_DIR = Path(os.path.expanduser("~")) / ".manarey_prefs"
PREFS_PATH = PREFS_DIR / "user_prefs.json"


def _dedupe(seq):
    seen = set()
    out = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _load_locales(default_local=None):
    try:
        locs = [l for l in get_all_locals() if l]
    except Exception:
        locs = []
    if (
        default_local
        and default_local not in locs
        and default_local not in ("Todos", "Todos los locales")
    ):
        locs.append(default_local)
    if not locs:
        locs = LOCALES[:]
    norm = []
    for l in locs:
        l = (l or "").strip() or "Sin local"
        norm.append(l)
    return _dedupe(["Todos"] + norm)


def _fmt_money(v):
    """Formatea números con separador de miles (sin símbolo)."""
    try:
        return f"{int(v):,}".replace(",", ".")
    except:
        return str(v)


def _fmt_date_only(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".")[0]
    s = s.replace("T", " ")
    date_part = s.split(" ")[0]
    if len(date_part) >= 10 and date_part[4] == "-" and date_part[7] == "-":
        y, m, d = date_part[0:4], date_part[5:7], date_part[8:10]
        return f"{d}-{m}-{y}"
    return date_part


def _fmt_datetime(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".")[0]
    s = s.replace("T", " ")
    parts = s.split(" ")
    date_part = _fmt_date_only(parts[0]) if parts else _fmt_date_only(s)
    time_part = parts[1] if len(parts) > 1 else ""
    return f"{date_part} {time_part}".strip()


def _parse_db_datetime(value):
    s = str(value or "").strip()
    if not s:
        return None
    if "." in s:
        s = s.split(".")[0]
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def _row_original_fecha(row) -> str:
    return str(
        (row or {}).get("fecha_venta_original") or (row or {}).get("fecha") or ""
    )


def _row_history_fecha(row) -> str:
    if (row or {}).get("history_kind") == "completion":
        return str(
            (row or {}).get("pago_completado_fecha") or (row or {}).get("fecha") or ""
        )
    return _row_original_fecha(row)


def _matches_history_filter(
    value, filtro_fecha: str, fecha_dia=None, fecha_inicio=None, fecha_fin=None
) -> bool:
    dt = _parse_db_datetime(value)
    if not dt:
        return False
    day = dt.date()
    today = datetime.now().date()
    if filtro_fecha in ("hoy", ""):
        return day == today
    if filtro_fecha == "dia" and fecha_dia:
        target = _parse_db_datetime(fecha_dia)
        return bool(target and day == target.date())
    if filtro_fecha == "semana":
        return day >= (today - timedelta(days=7))
    if filtro_fecha == "mes":
        return day >= (today - timedelta(days=30))
    if filtro_fecha == "todo":
        if fecha_inicio and fecha_fin:
            ini = _parse_db_datetime(fecha_inicio)
            fin = _parse_db_datetime(fecha_fin)
            if ini and fin:
                return ini.date() <= day <= fin.date()
        return True
    return day == today


def _build_completion_history_row(row: dict):
    if not isinstance(row, dict):
        return None
    fecha_comp = row.get("pago_completado_fecha")
    if not fecha_comp:
        return None
    try:
        monto = float(row.get("pago_completado_monto") or 0)
    except Exception:
        monto = 0.0
    if monto <= 0.01:
        return None
    comp_tipo = (row.get("pago_completado_tipo") or "").strip().lower()
    if comp_tipo not in ("sena", "domicilio"):
        return None
    cloned = dict(row)
    cloned["history_kind"] = "completion"
    cloned["history_source_type"] = comp_tipo
    cloned["history_original_fecha"] = _row_original_fecha(row)
    cloned["fecha"] = fecha_comp
    cloned["total"] = monto
    cloned["monto_pagado"] = monto
    cloned["monto_pendiente"] = 0.0
    cloned["forma_pago"] = (
        row.get("pago_completado_forma") or row.get("forma_pago") or ""
    )
    cloned["tipo_pago"] = f"completado_{comp_tipo}"
    return cloned


def _norm_fp(value: str) -> str:
    fp = (value or "").strip().lower()
    return (
        fp.replace("Ã©", "e")
        .replace("Ã­", "i")
        .replace("Ã³", "o")
        .replace("Ã¡", "a")
        .replace("Ãº", "u")
    )


def _is_credito_personal_row(row) -> bool:
    return (row.get("tipo_pago") or "").strip().lower() == "credito_personal"


def _get_display_total(row) -> float:
    if (row or {}).get("history_kind") == "completion":
        try:
            return float((row or {}).get("total") or 0)
        except Exception:
            return 0.0
    try:
        monto_pagado = float((row or {}).get("monto_pagado") or 0)
    except Exception:
        monto_pagado = 0.0
    try:
        monto_pendiente = float((row or {}).get("monto_pendiente") or 0)
    except Exception:
        monto_pendiente = 0.0
    if monto_pagado > 0.009 and monto_pendiente > 0.009:
        return monto_pagado
    try:
        total = float((row or {}).get("total") or 0)
    except Exception:
        total = 0.0
    return total


def _get_display_rest(row) -> float:
    if (row or {}).get("history_kind") == "completion":
        return 0.0
    try:
        monto_pendiente = float((row or {}).get("monto_pendiente") or 0)
    except Exception:
        monto_pendiente = 0.0
    return max(0.0, monto_pendiente)


def _sumar_metodo(fp: str, monto: float, totals: dict, fp_totals: dict) -> None:
    if monto <= 0:
        return
    nombre = (fp or "").strip() or "Otros"
    fp_totals.setdefault(nombre, 0.0)
    fp_totals[nombre] += monto
    fp_norm = _norm_fp(nombre)
    if fp_norm == "efectivo":
        totals["efectivo"] += monto
    elif "credito" in fp_norm:
        totals["credito"] += monto
    elif "debito" in fp_norm:
        totals["debito"] += monto
    elif "tarjeta" in fp_norm:
        totals["credito"] += monto
    elif fp_norm == "transferencia":
        totals["transferencia"] += monto
    elif fp_norm == "qr":
        totals["qr"] += monto


def _agregar_envio_local_a_totales(row, pagos, totals: dict, fp_totals: dict) -> None:
    try:
        incluye_envio = int(row.get("incluye_envio") or 0)
        precio_envio = float(row.get("precio_envio") or 0)
    except Exception:
        return
    forma_envio = (row.get("forma_pago_envio") or "").strip().lower()
    if (
        incluye_envio != 1
        or precio_envio <= 0
        or forma_envio not in ("local", "en local")
    ):
        return
    metodo = _get_envio_local_metodo(row, pagos)
    if metodo:
        _sumar_metodo(metodo, precio_envio, totals, fp_totals)


def _get_envio_local_metodo(row, pagos) -> str:
    pagos = pagos or []
    if pagos:
        for p in pagos:
            forma = (p.get("forma") or "").strip()
            if _norm_fp(forma) != "credito personal":
                return forma
        return (pagos[0].get("forma") or "").strip() if pagos else ""
    return (row.get("forma_pago") or "").strip()


def _rebuild_totals_from_fp_totals(fp_totals: dict) -> dict:
    totals = {
        "efectivo": 0.0,
        "credito": 0.0,
        "debito": 0.0,
        "transferencia": 0.0,
        "qr": 0.0,
    }
    for fp, monto in (fp_totals or {}).items():
        try:
            _sumar_metodo(fp, float(monto or 0), totals, {})
        except Exception:
            pass
    return totals


def _load_time_prefs():
    try:
        if PREFS_PATH.exists():
            return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_time_prefs(data: dict):
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class VentaCard(QWidget):
    """Card visual para mostrar información de una venta"""

    def __init__(self, parent, venta_data, role, username):
        super().__init__(parent)
        self.venta_id = venta_data["id"]
        self.role = role
        self.username = username

        # Configuración visual moderna
        self.setObjectName("VentaCard")
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(CARD))
        self.setPalette(pal)
        self.setStyleSheet(
            """
            QWidget#VentaCard {
                border: 1px solid #34343a;
                border-radius: 14px;
            }
            QLabel { color: #e5e7eb; }
        """
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

        # Layout principal
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header con número de venta y fecha
        header = QHBoxLayout()

        # Número de venta
        numero_label = QLabel(f"Venta #{venta_data['numero_venta']}")
        numero_label.setFont(QFont("Segoe UI", 16, QFont.Black))
        numero_label.setStyleSheet(f"color: {PRIMARY};")
        header.addWidget(numero_label)

        header.addStretch()

        # Fecha
        fecha_label = QLabel(_fmt_datetime(venta_data.get("fecha")))
        fecha_label.setStyleSheet("color: #c9c9cf; font-size: 12px;")
        header.addWidget(fecha_label)

        root.addLayout(header)

        # Información del cliente
        cliente_layout = QHBoxLayout()

        cliente_info = QLabel(f"👤 {venta_data['cliente_nombre']}")
        cliente_info.setFont(QFont("Segoe UI", 12, QFont.Bold))
        cliente_layout.addWidget(cliente_info)

        telefono_info = QLabel(f"📞 {venta_data['cliente_telefono']}")
        telefono_info.setStyleSheet("color: #a0aec0;")
        cliente_layout.addWidget(telefono_info)

        cliente_layout.addStretch()
        root.addLayout(cliente_layout)

        # Productos vendidos (resumen)
        productos_text = venta_data.get("productos_lista", "Sin productos")
        productos_label = QLabel(f"📦 {productos_text}")
        productos_label.setWordWrap(True)
        productos_label.setStyleSheet("color: #d7d7de; font-size: 13px;")
        root.addWidget(productos_label)

        # Información de pago y totales
        totales_layout = QHBoxLayout()

        # Forma de pago
        pago_info = QLabel(f"💳 {venta_data['forma_pago']}")
        pago_info.setStyleSheet("color: #a0aec0;")
        totales_layout.addWidget(pago_info)

        totales_layout.addStretch()

        # Total
        total_label = QLabel(f"TOTAL: ${_fmt_money(venta_data['total'])}")
        total_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        total_label.setStyleSheet(f"color: {PRIMARY};")
        totales_layout.addWidget(total_label)

        root.addLayout(totales_layout)

        # Botones de acción
        actions_layout = QHBoxLayout()

        # Botón ver boleta
        ver_boleta_btn = QPushButton("📄 Ver Boleta")
        ver_boleta_btn.setCursor(Qt.PointingHandCursor)
        ver_boleta_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {PRIMARY}, stop:1 #e6ac00);
                color: #1A202C;
                border: none;
                border-radius: 14px;
                padding: 12px 20px;
                font-weight: 700;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(255,193,7,0.3);
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffcc33, stop:1 {PRIMARY});
                box-shadow: 0 6px 16px rgba(255,193,7,0.4);
            }}
        """
        )
        ver_boleta_btn.clicked.connect(self.ver_boleta)
        actions_layout.addWidget(ver_boleta_btn)

        actions_layout.addStretch()
        root.addLayout(actions_layout)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

    def ver_boleta(self):
        """Abre la boleta PDF de esta venta"""
        success, message = vm.generar_pdf_boleta(self.venta_id)
        if success:
            self._open_pdf_path(message)
        else:
            QMessageBox.warning(
                self, "Error", f"No se pudo generar la boleta: {message}"
            )

    def _open_pdf_path(self, filepath: str):
        """Abre un PDF usando el OS; filepath puede venir de generar_pdf_boleta"""
        try:
            if not filepath:
                QMessageBox.warning(self, "PDF", "No se recibió ruta del PDF")
                return
            if platform.system() == "Windows":
                os.startfile(filepath)
            elif platform.system() == "Darwin":
                subprocess.call(["open", filepath])
            else:
                subprocess.call(["xdg-open", filepath])
        except Exception as e:
            QMessageBox.warning(self, "PDF", f"No se pudo abrir el PDF: {e}")


class VentasLoadWorker(QThread):
    data_loaded = pyqtSignal(int, list, list, list, str, str)

    def __init__(
        self,
        local_arg: str,
        filtro_fecha: str,
        search: str,
        fecha_dia,
        fecha_inicio,
        fecha_fin,
        load_id: int,
        parent=None,
    ):
        super().__init__(parent)
        self.local_arg = local_arg
        self.filtro_fecha = filtro_fecha
        self.search = search
        self.fecha_dia = fecha_dia
        self.fecha_inicio = fecha_inicio
        self.fecha_fin = fecha_fin
        self.load_id = load_id

    def run(self):
        try:
            vm.ensure_envios_schema()
        except Exception:
            pass
        try:
            if self.isInterruptionRequested():
                return
            ventas = vhm.get_ventas_por_local_fast(
                self.local_arg,
                self.filtro_fecha,
                self.search,
                fecha_dia=self.fecha_dia,
                fecha_inicio=self.fecha_inicio,
                fecha_fin=self.fecha_fin,
                include_canceladas=True,
            )
        except Exception:
            ventas = []

        # pendientes sin filtro de fecha
        pending = []
        completions = []
        try:
            todas = vhm.get_ventas_por_local_fast(
                self.local_arg,
                "todo",
                "",
                fecha_dia=None,
                fecha_inicio=None,
                fecha_fin=None,
                include_canceladas=True,
            )
        except Exception:
            todas = []
        s = (self.search or "").lower()
        for v in todas or []:
            try:
                estado = (v.get("estado") or "").strip().lower()
                if estado == "cancelada":
                    continue
                if (v.get("tipo_pago") or "").lower() != "sena":
                    continue
                if float(v.get("monto_pendiente") or 0) <= 0.0001:
                    continue
                if (
                    s
                    and s not in (v.get("cliente_nombre", "") or "").lower()
                    and s not in (v.get("numero_venta", "") or "")
                ):
                    continue
                pending.append(v)
            except Exception:
                continue
        for v in todas or []:
            try:
                comp = _build_completion_history_row(v)
                if not comp:
                    continue
                if not _matches_history_filter(
                    comp.get("fecha"),
                    self.filtro_fecha,
                    self.fecha_dia,
                    self.fecha_inicio,
                    self.fecha_fin,
                ):
                    continue
                if (
                    s
                    and s not in (comp.get("cliente_nombre", "") or "").lower()
                    and s not in (comp.get("numero_venta", "") or "")
                ):
                    continue
                completions.append(comp)
            except Exception:
                continue
        try:
            pending.sort(key=lambda x: x.get("fecha") or "", reverse=True)
        except Exception:
            pass
        try:
            completions.sort(key=lambda x: x.get("fecha") or "", reverse=True)
        except Exception:
            pass

        try:
            if self.isInterruptionRequested():
                return
            self.data_loaded.emit(
                self.load_id,
                ventas or [],
                pending or [],
                completions or [],
                self.local_arg,
                self.filtro_fecha,
            )
        except Exception:
            pass


class VentasTableModel(QAbstractTableModel):
    HEADERS = [
        "Remito",
        "Fecha",
        "Numero",
        "Cliente",
        "Productos",
        "Pago",
        "Total",
        "Envio",
        "Rest.",
    ]

    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = list(rows or [])

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def rowCount(self, parent=None):
        return len(self._rows)

    def columnCount(self, parent=None):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            try:
                return self.HEADERS[section]
            except Exception:
                return None
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._rows):
            return None
        r = self._rows[row]

        if role == Qt.DisplayRole:
            if col == 0:
                return "?" if r.get("remito_ok") else ""
            if col == 1:
                return r.get("fecha", "")
            if col == 2:
                return r.get("numero", "")
            if col == 3:
                return r.get("cliente", "")
            if col == 4:
                return r.get("productos", "")
            if col == 5:
                return r.get("pago", "")
            if col == 6:
                return r.get("total", "")
            if col == 7:
                return r.get("envio", "")
            if col == 8:
                return r.get("resto", "")

        if role == Qt.ToolTipRole and col == 4:
            return r.get("productos_full", "")

        if role == Qt.TextAlignmentRole:
            if col in (0, 1, 2, 5, 6, 7, 8):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.ForegroundRole:
            if r.get("remito_ok") and col == 0:
                return QColor("#10b981")
            if r.get("estado") == "cancelada":
                return QColor("#9ca3af")
            if r.get("pending"):
                return QColor("#fbbf24")
            if r.get("entregado"):
                return QColor("#a7f3d0")
            return QColor(TEXT)

        if role == Qt.BackgroundRole:
            if r.get("estado") == "cancelada":
                return QColor("#2a2a2e")
            if r.get("pending"):
                return QColor("#2b1f1f")
            if r.get("entregado"):
                return QColor("#1f2f2a")
            return None

        return None

    def row_at(self, row):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class VentasWindow(QMainWindow):
    """Ventana principal del historial de ventas con estilo moderno"""

    def __init__(self, username: str, role: str, user_local: str, back_command=None):
        super().__init__()
        self.username = username
        self.role = role
        self.user_local = user_local
        self.back_command = back_command
        self._prefs_cache = _load_time_prefs()
        self._load_counter = 0
        self._current_load_id = 0
        self._load_thread = None
        self._init_time_filter_done = False
        # Cache de filas actualmente mostradas en la tabla (incluye pendientes siempre arriba)
        self._rows_cache = []
        # Control de visibilidad de totales para locales
        self._totals_unlocked = self.role == "admin"
        self._totals_lock_enabled = self.role != "admin"
        self._cash_total_local = None
        self._cash_total_local_arg = None

        self.setWindowTitle(f"Historial de Ventas - {user_local}")
        self.resize(1100, 760)
        self.setMinimumSize(860, 620)
        self.setWindowState(self.windowState() | Qt.WindowMaximized)
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {BG}, stop:1 #0f172a);
            }}
            QLabel{{color:{TEXT};}}
        """
        )

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # Header con botón de volver
        header = QHBoxLayout()
        if self.back_command:
            back_btn = QPushButton("← Volver")
            back_btn.setCursor(Qt.PointingHandCursor)
            back_btn.setMinimumHeight(40)
            back_btn.setStyleSheet(
                "QPushButton{background:#34343a;color:#C9A040;border:1px solid #3e3e44;"
                "border-radius:10px;padding:8px 14px;font-weight:700;}"
                "QPushButton:hover{background:#3e3e44;}"
            )
            back_btn.clicked.connect(self.back_command)
            header.addWidget(back_btn)

        title = QLabel("Historial de Ventas")
        title.setFont(QFont("Segoe UI", 24, QFont.Black))
        title.setStyleSheet(f"color:{PRIMARY};")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # Filtros con estilo moderno
        filtros_layout = QHBoxLayout()

        # Campo de búsqueda
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar por cliente o número de venta...")
        self.search_input.setMinimumWidth(180)
        self.search_input.setMaximumWidth(320)
        self.search_input.setStyleSheet(
            f"""
            QLineEdit {{
                background: {CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 600;
            }}
            QLineEdit:focus {{
                border-color: {PRIMARY};
                background: #2a2a2e;
            }}
            QLineEdit:hover {{
                background: #2a2a2e;
                border-color: #4a4a50;
            }}
            QLineEdit::placeholder {{
                color: #6b7280;
                font-style: italic;
            }}
        """
        )
        filtros_layout.addWidget(self.search_input)

        # Filtro por período (combo único)
        self.periodo_combo = QComboBox()
        self.periodo_combo.addItem("Hoy", "hoy")
        self.periodo_combo.addItem("Últimos 7 días", "semana")
        self.periodo_combo.addItem("Últimos 30 días", "mes")
        self.periodo_combo.addItem("Mes seleccionado", "meses")
        self.periodo_combo.addItem("Día específico", "dia")
        self.periodo_combo.setStyleSheet(
            f"""
            QComboBox {{
                background: {CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 600;
                min-width: 160px;
            }}
            QComboBox:focus {{
                border-color: {PRIMARY};
            }}
            QComboBox:hover {{
                background: #2a2a2e;
                border-color: #4a4a50;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 25px;
                padding-right: 10px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid {TEXT};
            }}
            QComboBox QAbstractItemView {{
                background: {CARD};
                color: {TEXT};
                selection-background-color: {BORDER};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
            }}
        """
        )
        filtros_layout.addWidget(self.periodo_combo)

        # Selector de mes (para "Mes seleccionado")
        self.mes_edit = QDateEdit()
        self.mes_edit.setCalendarPopup(True)
        self.mes_edit.setDisplayFormat("MM-yyyy")
        try:
            self.mes_edit.setDate(QDate.currentDate().addMonths(-1))
        except Exception:
            pass
        self.mes_edit.setVisible(False)
        filtros_layout.addWidget(self.mes_edit)

        # Selector de fecha (por día, por defecto hoy)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd-MM-yyyy")
        try:
            self.date_edit.setDate(QDate.currentDate())
        except Exception:
            pass
        self.date_edit.setStyleSheet(
            f"""
            QDateEdit {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.95), stop:1 rgba(255,255,255,0.85));
                color: #1A202C;
                border: 2px solid rgba(45,55,72,0.1);
                border-radius: 16px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 600;
                min-width: 150px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1);
            }}
            QDateEdit:focus {{
                border-color: {PRIMARY};
                box-shadow: 0 0 0 3px rgba(255,193,7,0.2), 0 4px 12px rgba(0,0,0,0.1);
            }}
        """
        )
        self.date_edit.setVisible(False)
        filtros_layout.addWidget(self.date_edit)

        # Recordar configuración de tiempo
        self.remember_time_cb = QCheckBox("Recordar filtro de tiempo")
        self.remember_time_cb.setStyleSheet("color:#e5e7eb; font-weight:600;")
        filtros_layout.addWidget(self.remember_time_cb)

        # Filtro por local (solo admin)
        self.local_combo = None
        if self.role == "admin":
            self.local_combo = QComboBox()
            self.local_combo.addItems(_load_locales(self.user_local))
            self.local_combo.setStyleSheet(
                f"""
                QComboBox {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255,255,255,0.95), stop:1 rgba(255,255,255,0.85));
                    color: #1A202C;
                    border: 2px solid rgba(45,55,72,0.1);
                    border-radius: 16px;
                    padding: 14px 18px;
                    font-size: 14px;
                    font-weight: 600;
                    min-width: 160px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1);
                }}
            """
            )
            filtros_layout.addWidget(self.local_combo)
            try:
                default_local = self.user_local or "Todos"
                idx = self.local_combo.findText(default_local)
                if idx >= 0:
                    self.local_combo.setCurrentIndex(idx)
            except Exception:
                pass

        # Botón buscar
        buscar_btn = QPushButton("Refrescar")
        buscar_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {PRIMARY}, stop:1 #e6ac00);
                color: #1A202C;
                border: none;
                border-radius: 14px;
                padding: 12px 20px;
                font-weight: 700;
                font-size: 14px;
                box-shadow: 0 4px 12px rgba(255,193,7,0.3);
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffcc33, stop:1 {PRIMARY});
                box-shadow: 0 6px 16px rgba(255,193,7,0.4);
            }}
        """
        )
        buscar_btn.clicked.connect(self.cargar_ventas)
        filtros_layout.addWidget(buscar_btn)

        filtros_layout.addStretch()
        root.addLayout(filtros_layout)

        # Tabla de ventas con estilo avanzado
        self.ventas_table = QTableView()
        self.ventas_table.setAlternatingRowColors(True)
        self.ventas_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ventas_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ventas_table.verticalHeader().setVisible(False)
        self.ventas_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.ventas_model = VentasTableModel([], self)
        self.ventas_table.setModel(self.ventas_model)

        header = self.ventas_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)

        self.ventas_table.setStyleSheet(
            f"""
            QTableView {{
                background: {CARD};
                alternate-background-color: #1b1b1f;
                color: {TEXT};
                border: 1px solid #2a2a2e;
                border-radius: 10px;
            }}
            QHeaderView::section {{
                background: #222226;
                color: {TEXT};
                font-weight: 700;
                padding: 8px;
                border: none;
            }}
        """
        )

        self.ventas_table.doubleClicked.connect(self.ver_detalles_venta)
        root.addWidget(self.ventas_table)

        # Barra de totales del período
        self.totals_bar = QFrame()
        self.totals_bar.setStyleSheet(
            f"""
            QFrame {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
                padding: 10px 14px;
            }}
            QLabel {{ color: {TEXT}; }}
        """
        )
        totals_main_layout = QVBoxLayout(self.totals_bar)
        totals_main_layout.setContentsMargins(12, 8, 12, 8)
        totals_main_layout.setSpacing(6)

        # Fila superior: resumen general
        top_totals_layout = QHBoxLayout()
        top_totals_layout.setSpacing(16)

        self.total_count_label = QLabel("Ventas: 0")
        self.total_count_label.setStyleSheet("font-weight: 700;")
        top_totals_layout.addWidget(self.total_count_label)

        self.total_amount_label = QLabel("Total período: $0")
        self.total_amount_label.setStyleSheet(f"color:{PRIMARY}; font-weight: 800;")
        top_totals_layout.addWidget(self.total_amount_label)

        # Extras: señas y pendiente
        self.senas_label = QLabel("Señas: $0")
        self.senas_label.setStyleSheet("color:#facc15; font-weight: 700;")
        top_totals_layout.addWidget(self.senas_label)

        self.pendiente_label = QLabel("Pendiente: $0")
        self.pendiente_label.setStyleSheet("color:#ef4444; font-weight: 700;")
        top_totals_layout.addWidget(self.pendiente_label)

        self.domicilio_label = QLabel("Pagado en domicilio: $0")
        self.domicilio_label.setStyleSheet("color:#38bdf8; font-weight: 700;")
        top_totals_layout.addWidget(self.domicilio_label)

        self.show_totals_btn = QPushButton("Ver totales")
        self.show_totals_btn.setCursor(Qt.PointingHandCursor)
        self.show_totals_btn.setStyleSheet(
            """
            QPushButton {background:#1f2937;color:#e5e7eb;border:none;border-radius:10px;
            padding:6px 12px;font-weight:800;}
            QPushButton:hover {background:#374151;}
        """
        )
        self.show_totals_btn.clicked.connect(self._on_toggle_totals)
        if not self._totals_lock_enabled:
            self.show_totals_btn.hide()
        top_totals_layout.addWidget(self.show_totals_btn)

        top_totals_layout.addStretch()

        right_cash = QHBoxLayout()
        right_cash.setSpacing(10)
        self.cash_local_label = QLabel("Dinero en el local: $0")
        self.cash_local_label.setStyleSheet("font-weight: 800; color: #22c55e;")
        right_cash.addWidget(self.cash_local_label)

        self.cash_withdraw_btn = QPushButton("Retirar")
        self.cash_withdraw_btn.setCursor(Qt.PointingHandCursor)
        self.cash_withdraw_btn.setStyleSheet(
            """
            QPushButton {background:#1f2937;color:#e5e7eb;border:none;border-radius:10px;
            padding:6px 14px;font-weight:800;}
            QPushButton:hover {background:#374151;}
        """
        )
        self.cash_withdraw_btn.clicked.connect(self._on_retirar_efectivo)
        right_cash.addWidget(self.cash_withdraw_btn)
        top_totals_layout.addLayout(right_cash)
        totals_main_layout.addLayout(top_totals_layout)

        # Fila inferior: desglose por forma de pago
        self.totals_breakdown_wrap = QFrame()
        bottom_totals_layout = QHBoxLayout(self.totals_breakdown_wrap)
        bottom_totals_layout.setContentsMargins(0, 0, 0, 0)
        bottom_totals_layout.setSpacing(16)

        self.total_cash_label = QLabel("Efectivo: $0")
        self.total_cash_label.setStyleSheet("font-weight: 700;")
        bottom_totals_layout.addWidget(self.total_cash_label)

        self.total_credit_label = QLabel("Tarjeta de Crédito: $0")
        self.total_credit_label.setStyleSheet("font-weight: 700;")
        bottom_totals_layout.addWidget(self.total_credit_label)

        self.total_debit_label = QLabel("Tarjeta de Débito: $0")
        self.total_debit_label.setStyleSheet("font-weight: 700;")
        bottom_totals_layout.addWidget(self.total_debit_label)

        self.total_transfer_label = QLabel("Transferencia: $0")
        self.total_transfer_label.setStyleSheet("font-weight: 700;")
        bottom_totals_layout.addWidget(self.total_transfer_label)

        self.total_qr_label = QLabel("QR: $0")
        self.total_qr_label.setStyleSheet("font-weight: 700;")
        bottom_totals_layout.addWidget(self.total_qr_label)

        bottom_totals_layout.addStretch()
        totals_main_layout.addWidget(self.totals_breakdown_wrap)
        root.addWidget(self.totals_bar)
        self._apply_totals_visibility()

        # Área para mostrar detalles de venta seleccionada
        self.detalles_widget = QWidget()
        self.detalles_widget.setVisible(False)
        self.detalles_widget.setStyleSheet(
            f"""
            QWidget {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.12), stop:1 rgba(255,255,255,0.08));
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 20px;
                padding: 25px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.1);
                backdrop-filter: blur(10px);
            }}
        """
        )

        detalles_layout = QVBoxLayout(self.detalles_widget)

        detalles_header = QHBoxLayout()
        detalles_title = QLabel("Detalles de la Venta")
        detalles_title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        detalles_title.setStyleSheet(
            f"""
            color: {PRIMARY};
            background: transparent;
            text-shadow: 0 1px 3px rgba(0,0,0,0.3);
        """
        )
        detalles_header.addWidget(detalles_title)

        detalles_header.addStretch()

        close_detalles_btn = QPushButton("✕")
        close_detalles_btn.setFixedSize(32, 32)
        close_detalles_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: #dc2626;
                color: white;
                border: none;
                border-radius: 16px;
                font-weight: 900;
            }}
            QPushButton:hover {{ background: #b91c1c; }}
        """
        )
        close_detalles_btn.clicked.connect(self.ocultar_detalles)
        detalles_header.addWidget(close_detalles_btn)

        detalles_layout.addLayout(detalles_header)

        # Área de detalles (se llenará dinámicamente)
        self.detalles_content = QWidget()
        self.detalles_content_layout = QVBoxLayout(self.detalles_content)
        self.detalles_content_layout.setSpacing(12)

        self.detalles_scroll = QScrollArea()
        self.detalles_scroll.setWidgetResizable(True)
        self.detalles_scroll.setFrameShape(QFrame.NoFrame)
        self.detalles_scroll.setWidget(self.detalles_content)
        detalles_layout.addWidget(self.detalles_scroll)

        try:
            self.detalles_widget.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Preferred
            )
            self.detalles_widget.setMinimumWidth(620)
        except Exception:
            pass
        root.addWidget(self.detalles_widget)

        # Señales de filtro
        self.periodo_combo.currentIndexChanged.connect(
            lambda idx: self.on_period_changed(idx, user_action=True)
        )
        self.date_edit.dateChanged.connect(self._on_date_changed)
        self.mes_edit.dateChanged.connect(self._on_month_changed)
        self.remember_time_cb.toggled.connect(self._maybe_save_time_filter)

        # Búsqueda automática
        self.search_input.textChanged.connect(self.cargar_ventas)
        if self.role == "admin" and self.local_combo:
            self.local_combo.currentIndexChanged.connect(
                lambda _=None: self.cargar_ventas()
            )

        # Inicializar estado de filtro de tiempo y cargar
        self._init_time_filter_state()
        self.cargar_ventas()

    def _get_pending_senas(self, local_arg: str, search: str):
        """
        Devuelve ventas con seña pendiente (monto_pendiente > 0) sin filtrar por fecha,
        para mostrarlas siempre arriba.
        """
        try:
            todas = vhm.get_ventas_por_local_fast(
                local_arg,
                "todo",
                "",
                fecha_dia=None,
                fecha_inicio=None,
                fecha_fin=None,
                include_canceladas=True,
            )
        except Exception:
            return []

        pending = []
        s = (search or "").lower()
        for v in todas or []:
            try:
                if int(v.get("entrega_entregado") or 0) == 1:
                    continue
                estado = (v.get("estado") or "").strip().lower()
                if estado == "cancelada":
                    continue
                if (v.get("tipo_pago") or "").lower() != "sena":
                    continue
                if float(v.get("monto_pendiente") or 0) <= 0.0001:
                    continue
                if (
                    s
                    and s not in (v.get("cliente_nombre", "") or "").lower()
                    and s not in (v.get("numero_venta", "") or "")
                ):
                    continue
                pending.append(v)
            except Exception:
                continue

        try:
            pending.sort(key=lambda x: x.get("fecha") or "", reverse=True)
        except Exception:
            pass
        return pending

    def _set_period_by_key(self, key: str):
        try:
            self.periodo_combo.blockSignals(True)
            for i in range(self.periodo_combo.count()):
                if self.periodo_combo.itemData(i) == key:
                    self.periodo_combo.setCurrentIndex(i)
                    return
            self.periodo_combo.setCurrentIndex(0)
        finally:
            try:
                self.periodo_combo.blockSignals(False)
            except Exception:
                pass

    def _init_time_filter_state(self):
        prefs = (
            self._prefs_cache.get("ventas_history", {})
            if isinstance(self._prefs_cache, dict)
            else {}
        )
        remember = bool(prefs.get("remember_time", False))
        self._date_user_set = False
        try:
            self.remember_time_cb.setChecked(remember)
        except Exception:
            pass

        key = prefs.get("time_key", "hoy") if remember else "hoy"
        self._set_period_by_key(key)

        if key == "dia":
            date_str = prefs.get("fecha_dia") if remember else None
            qd = QDate.fromString(date_str or "", "yyyy-MM-dd")
            if not qd.isValid():
                qd = QDate.currentDate()
            try:
                self.date_edit.blockSignals(True)
                self.date_edit.setDate(qd)
            finally:
                try:
                    self.date_edit.blockSignals(False)
                except Exception:
                    pass
        elif key == "meses":
            mes_str = prefs.get("mes") if remember else None
            qd = QDate.fromString(mes_str or "", "yyyy-MM")
            if not qd.isValid():
                qd = QDate.currentDate().addMonths(-1)
            try:
                self.mes_edit.blockSignals(True)
                self.mes_edit.setDate(qd)
            finally:
                try:
                    self.mes_edit.blockSignals(False)
                except Exception:
                    pass

        # Ajustar visibilidad sin recargar aún
        self._init_time_filter_done = True
        self.on_period_changed(
            self.periodo_combo.currentIndex(),
            user_action=False,
            reload=False,
            save=False,
        )

    def _maybe_save_time_filter(self):
        if not getattr(self, "_init_time_filter_done", False):
            return
        prefs = self._prefs_cache if isinstance(self._prefs_cache, dict) else {}
        vh = prefs.get("ventas_history", {})
        if self.remember_time_cb.isChecked():
            vh["remember_time"] = True
            vh["time_key"] = self.periodo_combo.itemData(
                self.periodo_combo.currentIndex()
            )
            vh["fecha_dia"] = self.date_edit.date().toString("yyyy-MM-dd")
            vh["mes"] = self.mes_edit.date().toString("yyyy-MM")
        else:
            vh["remember_time"] = False
            for k in ("time_key", "fecha_dia", "mes"):
                vh.pop(k, None)
        prefs["ventas_history"] = vh
        self._prefs_cache = prefs
        _save_time_prefs(prefs)

    def on_period_changed(
        self, idx, user_action: bool = False, reload: bool = True, save: bool = True
    ):
        key = self.periodo_combo.itemData(idx)
        if key == "dia":
            self.date_edit.setVisible(True)
            self.date_edit.setEnabled(True)
            self.mes_edit.setVisible(False)
            if (user_action and not self._date_user_set) or (
                not self.remember_time_cb.isChecked() and not self._date_user_set
            ):
                try:
                    self.date_edit.blockSignals(True)
                    self.date_edit.setDate(QDate.currentDate())
                finally:
                    try:
                        self.date_edit.blockSignals(False)
                    except Exception:
                        pass
        elif key == "meses":
            self.mes_edit.setVisible(True)
            self.date_edit.setVisible(False)
        else:
            self.date_edit.setVisible(False)
            self.mes_edit.setVisible(False)

        if save:
            self._maybe_save_time_filter()
        if reload:
            self.cargar_ventas()

    def _on_date_changed(self, _qdate):
        # Asegura que estemos en modo día y guarda si corresponde
        self._set_period_by_key("dia")
        self.on_period_changed(
            self.periodo_combo.currentIndex(),
            user_action=False,
            reload=False,
            save=False,
        )
        if not self.date_edit.date().isValid():
            try:
                self.date_edit.setDate(QDate.currentDate())
            except Exception:
                pass
        else:
            self._date_user_set = True
        self._maybe_save_time_filter()
        self.cargar_ventas()

    def _on_month_changed(self, _qdate):
        self._set_period_by_key("meses")
        self.on_period_changed(
            self.periodo_combo.currentIndex(),
            user_action=False,
            reload=False,
            save=False,
        )
        self._maybe_save_time_filter()
        self.cargar_ventas()

    def cargar_ventas(self):
        """Carga las ventas segun los filtros aplicados (async)"""
        search = self.search_input.text().strip()
        periodo_idx = self.periodo_combo.currentIndex()

        fecha_dia = None
        fecha_inicio = None
        fecha_fin = None
        key = self.periodo_combo.itemData(periodo_idx) or "hoy"
        filtro_fecha = "hoy"

        if key == "dia":
            try:
                fecha_dia = self.date_edit.date().toString("yyyy-MM-dd")
            except Exception:
                fecha_dia = None
            filtro_fecha = "dia"
        elif key == "semana":
            filtro_fecha = "semana"
        elif key == "mes":
            filtro_fecha = "mes"
        elif key == "meses":
            try:
                d = self.mes_edit.date()
                year = d.year()
                month = d.month()
                first = QDate(year, month, 1)
                last = QDate(year, month, d.daysInMonth())
                fecha_inicio = first.toString("yyyy-MM-dd")
                fecha_fin = last.toString("yyyy-MM-dd")
                filtro_fecha = "todo"
            except Exception:
                fecha_inicio = fecha_fin = None
                filtro_fecha = "mes"
        else:
            filtro_fecha = "hoy"

        local_arg = (
            self.local_combo.currentText()
            if self.role == "admin" and self.local_combo
            else self.user_local
        )
        try:
            self._cash_filter = {
                "filtro_fecha": filtro_fecha,
                "fecha_dia": fecha_dia,
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin,
                "local": local_arg,
            }
        except Exception:
            pass

        self._load_counter += 1
        self._current_load_id = self._load_counter
        if self._load_thread and self._load_thread.isRunning():
            try:
                self._load_thread.requestInterruption()
            except Exception:
                pass

        self._load_thread = VentasLoadWorker(
            local_arg,
            filtro_fecha,
            search,
            fecha_dia,
            fecha_inicio,
            fecha_fin,
            self._current_load_id,
            self,
        )
        self._load_thread.data_loaded.connect(self._on_ventas_loaded)
        self._load_thread.start()

    def _on_ventas_loaded(
        self, load_id, ventas, pending, completions, local_arg, filtro_fecha
    ):
        if load_id != self._current_load_id:
            return
        self._render_ventas(ventas, pending, completions, local_arg, filtro_fecha)

    def _render_ventas(self, ventas, pending, completions, local_arg, filtro_fecha):
        def _is_pending_row(row: dict) -> bool:
            if not isinstance(row, dict):
                return False
            if row.get("history_kind") == "completion":
                return False
            if int(row.get("entrega_entregado") or 0) == 1:
                return False
            try:
                mp = float(row.get("monto_pendiente") or 0)
                if mp > 0.01:
                    return True
            except Exception:
                pass
            estado = (row.get("estado") or "").strip().lower()
            if estado == "cancelada":
                return False
            if estado in ("pendiente", "incompleto"):
                return True
            return False

        rows_map = {}
        for v in ventas or []:
            key = v.get("id") if v.get("id") is not None else v.get("numero_venta")
            rows_map[key or id(v)] = v
        for p in pending or []:
            key = p.get("id") if p.get("id") is not None else p.get("numero_venta")
            rows_map.setdefault(key or id(p), p)
        for c in completions or []:
            key = f"completion:{c.get('id') or c.get('numero_venta') or id(c)}"
            rows_map[key] = c

        rows = list(rows_map.values())
        pending_rows = [r for r in rows if _is_pending_row(r)]
        non_pending_rows = [r for r in rows if not _is_pending_row(r)]

        def _fecha_key(row):
            return _row_history_fecha(row)

        pending_rows.sort(key=_fecha_key, reverse=True)
        non_pending_rows.sort(key=_fecha_key, reverse=True)
        all_rows = pending_rows + non_pending_rows

        self._rows_cache = list(all_rows or [])

        if not all_rows:
            self.ventas_model.set_rows([])
            self.update_totals_bar([], local_arg, filtro_fecha)
            return

        model_rows = []
        for venta in all_rows:
            history_kind = venta.get("history_kind")
            is_pending = _is_pending_row(venta)
            remito_ok = int(venta.get("remito_impreso") or 0) == 1
            tipo_pago = (venta.get("tipo_pago") or "").strip().lower()
            tipo_origen = (venta.get("tipo_pago_origen") or tipo_pago).strip().lower()
            tipo_map = {
                "sena": "Seña",
                "domicilio": "Domicilio",
                "completo": "Completo",
                "credito_personal": "Credito personal",
            }
            tipo_label = tipo_map.get(
                tipo_pago, (tipo_pago.title() if tipo_pago else "Completo")
            )
            if history_kind == "completion":
                source = (venta.get("history_source_type") or "").strip().lower()
                if source == "sena":
                    tipo_label = "Completo seña"
                elif source == "domicilio":
                    tipo_label = "Cobro domicilio"
            elif tipo_origen == "sena":
                tipo_label = (
                    "Seña completada"
                    if float(venta.get("pago_completado_monto") or 0) > 0.01
                    else "Seña"
                )
            try:
                mp = float(venta.get("monto_pendiente") or 0)
                if tipo_pago == "sena" and mp <= 0.01:
                    tipo_label = "Paga"
            except Exception:
                pass
            estado = (venta.get("estado") or "").strip().lower()
            forma_pago_label = venta.get("forma_pago")
            if history_kind == "completion":
                forma_pago_label = (
                    venta.get("pago_completado_forma") or forma_pago_label
                )
            elif tipo_origen == "sena":
                forma_pago_label = venta.get("forma_pago_origen") or forma_pago_label
            pago_text = f"{forma_pago_label} ({tipo_label})"
            if is_pending:
                pago_text = f"{venta['forma_pago']} (Pendiente)"
            elif history_kind == "completion":
                pago_text = f"{venta['forma_pago']} ({tipo_label})"
            if estado == "cancelada":
                pago_text = "Venta cancelada"

            productos_text = venta.get("productos_lista", "Sin productos")[:50]
            if history_kind == "completion":
                fecha_origen = _fmt_datetime(venta.get("history_original_fecha"))
                productos_text = f"Completa venta del {fecha_origen}"[:50]
            if len(venta.get("productos_lista", "")) > 50:
                productos_text += "..."

            model_rows.append(
                {
                    "remito_ok": remito_ok,
                    "fecha": _fmt_datetime(_row_history_fecha(venta)),
                    "numero": f"{venta.get('numero_venta')} / Pago"
                    if history_kind == "completion"
                    else venta.get("numero_venta"),
                    "cliente": venta.get("cliente_nombre"),
                    "productos": productos_text,
                    "productos_full": venta.get("productos_lista", ""),
                    "pago": pago_text,
                    "total": f"${_fmt_money(_get_display_total(venta))}",
                    "envio": f"${_fmt_money(float((venta or {}).get('precio_envio') or 0))}"
                    if int((venta or {}).get("incluye_envio") or 0) == 1
                    and float((venta or {}).get("precio_envio") or 0) > 0
                    else "",
                    "resto": f"${_fmt_money(_get_display_rest(venta))}"
                    if _get_display_rest(venta) > 0.009
                    else "",
                    "pending": is_pending,
                    "estado": estado,
                    "entregado": int(venta.get("entrega_entregado") or 0) == 1,
                    "raw": venta,
                }
            )

        self.ventas_model.set_rows(model_rows)
        self.update_totals_bar(all_rows, local_arg, filtro_fecha)

    def update_totals_bar(self, ventas, local_arg, filtro_fecha, pendientes=None):
        """Actualiza la barra de totales (suma, promedio, señas, pendiente, desglose pagos)."""

        def _is_cancelled(row):
            return (row.get("estado") or "").strip().lower() == "cancelada"

        count = len([v for v in (ventas or []) if not _is_cancelled(v)])
        total_sum = 0
        total_senas = 0
        total_pendiente = 0
        total_domicilio = 0.0
        fp_totals = {}
        totals = {
            "efectivo": 0.0,
            "credito": 0.0,
            "debito": 0.0,
            "transferencia": 0.0,
            "qr": 0.0,
        }
        total_efectivo = 0.0
        total_credito = 0.0
        total_debito = 0.0
        total_transferencia = 0.0
        total_qr = 0.0
        rows_map = {}
        for row in list(ventas or []) + list(pendientes or []):
            if not isinstance(row, dict):
                continue
            key = (
                row.get("id") if row.get("id") is not None else row.get("numero_venta")
            )
            rows_map[key or id(row)] = row
        all_rows = list(rows_map.values())
        venta_ids = set()
        for v in ventas or []:
            try:
                if _is_cancelled(v):
                    continue
                total_sum += float(_get_display_total(v) or 0)
            except Exception:
                pass
        for v in all_rows:
            if _is_cancelled(v):
                continue
            history_kind = v.get("history_kind")
            tipo_origen = (
                (v.get("tipo_pago_origen") or v.get("tipo_pago") or "").strip().lower()
            )
            try:
                completion_monto = float(v.get("pago_completado_monto") or 0)
            except Exception:
                completion_monto = 0.0
            try:
                vid = int(v.get("id") or 0)
            except Exception:
                vid = 0
            if vid > 0:
                venta_ids.add(vid)
            try:
                if history_kind != "completion" and tipo_origen == "sena":
                    if completion_monto > 0.01:
                        total_senas += max(
                            0.0, float(v.get("total") or 0) - completion_monto
                        )
                    else:
                        total_senas += float(v.get("monto_pagado") or 0)
            except Exception:
                pass
            try:
                if int(
                    v.get("entrega_entregado") or 0
                ) != 1 and not _is_credito_personal_row(v):
                    total_pendiente += float(v.get("monto_pendiente") or 0)
            except Exception:
                pass

            # Usar desglose de pagos si está disponible
            pagos_override = None
            if history_kind == "completion":
                pagos_override = [
                    {
                        "forma": v.get("pago_completado_forma")
                        or v.get("forma_pago")
                        or "Otros",
                        "monto": completion_monto,
                    }
                ]
            elif tipo_origen == "sena" and completion_monto > 0.01:
                monto_inicial = max(0.0, float(v.get("total") or 0) - completion_monto)
                pagos_override = (
                    [
                        {
                            "forma": v.get("forma_pago_origen")
                            or v.get("forma_pago")
                            or "Otros",
                            "monto": monto_inicial,
                        }
                    ]
                    if monto_inicial > 0.01
                    else []
                )

            pagos = (
                pagos_override if pagos_override is not None else (v.get("pagos") or [])
            )
            if pagos:
                for p in pagos:
                    try:
                        fp = (p.get("forma") or "").strip() or "Otros"
                        monto = float(p.get("monto") or 0)
                        fp_totals.setdefault(fp, 0.0)
                        fp_totals[fp] += monto
                        fp_lower = fp.lower()
                        fp_norm = (
                            fp_lower.replace("é", "e")
                            .replace("í", "i")
                            .replace("ó", "o")
                            .replace("á", "a")
                            .replace("ú", "u")
                        )
                        if fp_norm == "efectivo":
                            total_efectivo += monto
                        elif "credito" in fp_norm:
                            total_credito += monto
                        elif "debito" in fp_norm:
                            total_debito += monto
                        elif "tarjeta" in fp_norm:
                            total_credito += monto
                        elif fp_norm == "transferencia":
                            total_transferencia += monto
                        elif fp_norm == "qr":
                            total_qr += monto
                    except Exception:
                        pass
                _agregar_envio_local_a_totales(
                    v,
                    pagos,
                    {
                        "efectivo": 0.0,
                        "credito": 0.0,
                        "debito": 0.0,
                        "transferencia": 0.0,
                        "qr": 0.0,
                    },
                    fp_totals,
                )
            else:
                # Fallback: si hay 'Pago dividido:' en notas, parsear montos por método
                parsed_from_notas = False
                try:
                    notas = (v.get("notas") or "").strip()
                    if notas.lower().startswith("pago dividido:"):
                        parts_txt = notas.split(":", 1)[1]
                        parts = [p.strip() for p in parts_txt.split(",") if p.strip()]
                        for p in parts:
                            if "=" in p:
                                nombre, valor = p.split("=", 1)
                                fp = (nombre or "").strip() or "Otros"
                                valor = (
                                    valor.replace("$", "")
                                    .replace(" ", "")
                                    .replace(",", ".")
                                )
                                monto = float(valor or 0)
                                fp_totals.setdefault(fp, 0.0)
                                fp_totals[fp] += monto
                                fp_lower = fp.lower()
                                fp_norm = (
                                    fp_lower.replace("é", "e")
                                    .replace("í", "i")
                                    .replace("ó", "o")
                                    .replace("á", "a")
                                    .replace("ú", "u")
                                )
                                if fp_norm == "efectivo":
                                    total_efectivo += monto
                                elif "credito" in fp_norm:
                                    total_credito += monto
                                elif "debito" in fp_norm:
                                    total_debito += monto
                                elif "tarjeta" in fp_norm:
                                    total_credito += monto
                                elif fp_norm == "transferencia":
                                    total_transferencia += monto
                                elif fp_norm == "qr":
                                    total_qr += monto
                        parsed_from_notas = True
                except Exception:
                    parsed_from_notas = False
                if parsed_from_notas:
                    _agregar_envio_local_a_totales(
                        v,
                        [],
                        {
                            "efectivo": 0.0,
                            "credito": 0.0,
                            "debito": 0.0,
                            "transferencia": 0.0,
                            "qr": 0.0,
                        },
                        fp_totals,
                    )

                if not parsed_from_notas:
                    # Fallback final: usar forma_pago y total de la venta (puede ser genérico)
                    fp = (v.get("forma_pago") or "").strip() or "Otros"
                    try:
                        monto_base = float(v.get("total") or 0)
                        tipo_pago = (v.get("tipo_pago") or "").strip().lower()
                        if history_kind == "completion":
                            monto_base = completion_monto
                        elif tipo_origen == "sena" and completion_monto > 0.01:
                            monto_base = max(
                                0.0, float(v.get("total") or 0) - completion_monto
                            )
                            fp = (v.get("forma_pago_origen") or fp).strip() or "Otros"
                        elif tipo_pago == "sena":
                            monto_base = float(v.get("monto_pagado") or 0)
                        if monto_base <= 0:
                            continue
                        fp_totals.setdefault(fp, 0.0)
                        fp_totals[fp] += monto_base
                        fp_lower = fp.lower()
                        fp_norm = (
                            fp_lower.replace("é", "e")
                            .replace("í", "i")
                            .replace("ó", "o")
                            .replace("á", "a")
                            .replace("ú", "u")
                        )
                        if fp_norm == "efectivo":
                            total_efectivo += monto_base
                        elif "credito" in fp_norm:
                            total_credito += monto_base
                        elif "debito" in fp_norm:
                            total_debito += monto_base
                        elif "tarjeta" in fp_norm:
                            total_credito += monto_base
                        elif fp_norm == "transferencia":
                            total_transferencia += monto_base
                        elif fp_norm == "qr":
                            total_qr += monto_base
                        _agregar_envio_local_a_totales(
                            v,
                            [],
                            {
                                "efectivo": 0.0,
                                "credito": 0.0,
                                "debito": 0.0,
                                "transferencia": 0.0,
                                "qr": 0.0,
                            },
                            fp_totals,
                        )
                    except Exception:
                        pass
        self.total_count_label.setText(f"Ventas: {count}")
        self.total_amount_label.setText(f"Total período: ${_fmt_money(total_sum)}")
        self.senas_label.setText(f"Señas: ${_fmt_money(total_senas)}")
        self.pendiente_label.setText(f"Pendiente: ${_fmt_money(total_pendiente)}")
        try:
            total_domicilio = vm.get_domicilio_pagos_total(list(venta_ids), local_arg)
        except Exception:
            total_domicilio = 0.0
        self.domicilio_label.setText(
            f"Pagado en domicilio: ${_fmt_money(total_domicilio)}"
        )
        totals = _rebuild_totals_from_fp_totals(fp_totals)

        try:
            self.total_cash_label.setText(
                f"Efectivo: ${_fmt_money(totals['efectivo'])}"
            )
            self.total_credit_label.setText(
                f"Tarjeta de Credito: ${_fmt_money(totals['credito'])}"
            )
            self.total_debit_label.setText(
                f"Tarjeta de Debito: ${_fmt_money(totals['debito'])}"
            )
            self.total_transfer_label.setText(
                f"Transferencia: ${_fmt_money(totals['transferencia'])}"
            )
            self.total_qr_label.setText(f"QR: ${_fmt_money(totals['qr'])}")
        except Exception:
            pass

        try:
            self._current_local_arg = local_arg
            self._refresh_cash_local_total(local_arg)
        except Exception:
            pass

        self._apply_totals_visibility()

    def _apply_totals_visibility(self):
        show = bool(self._totals_unlocked or not self._totals_lock_enabled)
        try:
            self.senas_label.setVisible(show)
        except Exception:
            pass
        try:
            self.domicilio_label.setVisible(show)
        except Exception:
            pass
        try:
            self.total_amount_label.setVisible(show)
        except Exception:
            pass
        try:
            self.totals_breakdown_wrap.setVisible(show)
        except Exception:
            pass
        try:
            if self._totals_lock_enabled:
                self.show_totals_btn.setText(
                    "Ocultar totales" if show else "Ver totales"
                )
        except Exception:
            pass

    def _on_toggle_totals(self):
        if self.role == "admin":
            self._totals_unlocked = True
            self._apply_totals_visibility()
            return
        if self._totals_unlocked:
            self._totals_unlocked = False
            self._apply_totals_visibility()
            return
        if self._ask_totals_password():
            self._totals_unlocked = True
            self._apply_totals_visibility()

    def _ask_totals_password(self) -> bool:
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Ver totales")
            dlg.setStyleSheet(
                f"""
                QDialog {{
                    background: {BG};
                }}
                QLabel {{ color: {TEXT}; }}
                QLineEdit {{
                    background: {BG_ALT};
                    color: {TEXT};
                    border: 1px solid {BORDER};
                    border-radius: 10px;
                    padding: 8px 10px;
                    font-weight: 700;
                }}
                QCheckBox {{ color: {TEXT}; font-weight: 600; }}
                QPushButton {{
                    background: #1f2937;
                    color: #e5e7eb;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 16px;
                    font-weight: 800;
                }}
                QPushButton:hover {{ background: #374151; }}
            """
            )

            lay = QVBoxLayout(dlg)
            title = QLabel("Ingresa la contrasena para ver totales")
            title.setStyleSheet("font-size: 14px; font-weight: 800;")
            lay.addWidget(title)

            pass_input = QLineEdit()
            pass_input.setEchoMode(QLineEdit.Password)
            lay.addWidget(pass_input)
            show_pass = QCheckBox("Mostrar contraseña")
            show_pass.toggled.connect(
                lambda v: pass_input.setEchoMode(
                    QLineEdit.Normal if v else QLineEdit.Password
                )
            )
            lay.addWidget(show_pass)

            btns = QHBoxLayout()
            btns.addStretch()
            cancel_btn = QPushButton("Cancelar")
            ok_btn = QPushButton("Confirmar")
            btns.addWidget(cancel_btn)
            btns.addWidget(ok_btn)
            lay.addLayout(btns)

            cancel_btn.clicked.connect(dlg.reject)
            ok_btn.clicked.connect(dlg.accept)
            ok_btn.setDefault(True)
            ok_btn.setAutoDefault(True)
            pass_input.returnPressed.connect(ok_btn.click)
            pass_input.setFocus()

            if dlg.exec_() != QDialog.Accepted:
                return False
            pwd = (pass_input.text() or "").strip()
            if pwd != self._get_cash_password():
                QMessageBox.warning(self, "Ver totales", "Contraseña incorrecta.")
                return False
            return True
        except Exception:
            return False

    def _compute_cash_from_rows(self, rows) -> float:
        def _is_cancelled(row):
            return (row.get("estado") or "").strip().lower() == "cancelada"

        def _norm_fp(value: str) -> str:
            fp = (value or "").strip().lower()
            return (
                fp.replace("é", "e")
                .replace("í", "i")
                .replace("ó", "o")
                .replace("á", "a")
                .replace("ú", "u")
            )

        total_efectivo = 0.0
        for v in rows or []:
            if _is_cancelled(v):
                continue
            tipo_origen = (
                (v.get("tipo_pago_origen") or v.get("tipo_pago") or "").strip().lower()
            )
            try:
                completion_monto = float(v.get("pago_completado_monto") or 0)
            except Exception:
                completion_monto = 0.0
            pagos = v.get("pagos") or []
            if pagos:
                for p in pagos:
                    try:
                        fp = _norm_fp(p.get("forma") or "")
                        if fp == "efectivo":
                            total_efectivo += float(p.get("monto") or 0)
                    except Exception:
                        pass
                try:
                    metodo_envio = _norm_fp(_get_envio_local_metodo(v, pagos))
                    if metodo_envio == "efectivo" and (
                        v.get("forma_pago_envio") or ""
                    ).strip().lower() in ("local", "en local"):
                        total_efectivo += float(v.get("precio_envio") or 0)
                except Exception:
                    pass
                continue

            parsed_from_notas = False
            try:
                notas = (v.get("notas") or "").strip()
                if notas.lower().startswith("pago dividido:"):
                    parts_txt = notas.split(":", 1)[1]
                    parts = [p.strip() for p in parts_txt.split(",") if p.strip()]
                    for p in parts:
                        if "=" in p:
                            nombre, valor = p.split("=", 1)
                            fp = _norm_fp(nombre)
                            valor = (
                                valor.replace("$", "")
                                .replace(" ", "")
                                .replace(",", ".")
                            )
                            monto = float(valor or 0)
                            if fp == "efectivo":
                                total_efectivo += monto
                    parsed_from_notas = True
            except Exception:
                parsed_from_notas = False
            if parsed_from_notas:
                try:
                    metodo_envio = _norm_fp(_get_envio_local_metodo(v, []))
                    if metodo_envio == "efectivo" and (
                        v.get("forma_pago_envio") or ""
                    ).strip().lower() in ("local", "en local"):
                        total_efectivo += float(v.get("precio_envio") or 0)
                except Exception:
                    pass

            if not parsed_from_notas:
                try:
                    fp = _norm_fp(v.get("forma_pago") or "")
                    monto_base = float(_get_display_total(v) or 0)
                    tipo_pago = (v.get("tipo_pago") or "").strip().lower()
                    if tipo_origen == "sena" and completion_monto > 0.01:
                        monto_inicial = max(
                            0.0, float(v.get("total") or 0) - completion_monto
                        )
                        fp_inicial = _norm_fp(
                            v.get("forma_pago_origen") or v.get("forma_pago") or ""
                        )
                        fp_final = _norm_fp(
                            v.get("pago_completado_forma") or v.get("forma_pago") or ""
                        )
                        if monto_inicial > 0.01 and fp_inicial == "efectivo":
                            total_efectivo += monto_inicial
                        if fp_final == "efectivo":
                            total_efectivo += completion_monto
                        continue
                    if (
                        tipo_pago == "sena"
                        or float(v.get("monto_pendiente") or 0) > 0.009
                    ):
                        monto_base = float(v.get("monto_pagado") or monto_base or 0)
                    if monto_base > 0 and fp == "efectivo":
                        total_efectivo += monto_base
                    if fp == "efectivo" and (
                        v.get("forma_pago_envio") or ""
                    ).strip().lower() in ("local", "en local"):
                        total_efectivo += float(v.get("precio_envio") or 0)
                except Exception:
                    pass
        return float(total_efectivo or 0)

    def _refresh_cash_local_total(self, local_arg: str):
        try:
            today = QDate.currentDate().toString("yyyy-MM-dd")
            ventas_all = vhm.get_ventas_por_local_fast(
                local_arg,
                "todo",
                "",
                fecha_inicio="1970-01-01",
                fecha_fin=today,
                include_canceladas=True,
            )
            self._cash_local_total = self._compute_cash_from_rows(ventas_all)
            self._cash_withdrawn_total = vm.get_cash_withdrawn_total(
                local_arg,
                filtro_fecha="todo",
                fecha_inicio="1970-01-01",
                fecha_fin=today,
            )
            self._update_cash_local_label()
        except Exception:
            pass

    def _update_cash_local_label(self):
        try:
            withdrawn = float(getattr(self, "_cash_withdrawn_total", 0) or 0)
            available = max(
                0.0, float(getattr(self, "_cash_local_total", 0) or 0) - withdrawn
            )
            self.cash_local_label.setText(
                f"Dinero en el local: ${_fmt_money(available)}"
            )
        except Exception:
            pass

    def _on_retirar_efectivo(self):
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Retirar efectivo")
            dlg.setStyleSheet(
                f"""
                QDialog {{
                    background: {BG};
                }}
                QLabel {{ color: {TEXT}; }}
                QLineEdit {{
                    background: {BG_ALT};
                    color: {TEXT};
                    border: 1px solid {BORDER};
                    border-radius: 10px;
                    padding: 8px 10px;
                    font-weight: 700;
                }}
                QCheckBox {{ color: {TEXT}; font-weight: 600; }}
                QPushButton {{
                    background: #1f2937;
                    color: #e5e7eb;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 16px;
                    font-weight: 800;
                }}
                QPushButton:hover {{ background: #374151; }}
            """
            )

            lay = QVBoxLayout(dlg)
            title = QLabel("Retiro de efectivo")
            title.setStyleSheet("font-size: 16px; font-weight: 900;")
            lay.addWidget(title)

            pass_lbl = QLabel("Contraseña")
            lay.addWidget(pass_lbl)
            pass_input = QLineEdit()
            pass_input.setEchoMode(QLineEdit.Password)
            lay.addWidget(pass_input)
            show_pass = QCheckBox("Mostrar contraseña")
            show_pass.toggled.connect(
                lambda v: pass_input.setEchoMode(
                    QLineEdit.Normal if v else QLineEdit.Password
                )
            )
            lay.addWidget(show_pass)

            amount_lbl = QLabel("Monto a retirar")
            lay.addWidget(amount_lbl)
            amount_input = QLineEdit()
            amount_input.setPlaceholderText("$ 0")
            lay.addWidget(amount_input)

            def _format_amount(raw: str) -> str:
                digits = "".join(ch for ch in (raw or "") if ch.isdigit())
                if not digits:
                    return ""
                try:
                    formatted = "{:,}".format(int(digits)).replace(",", ".")
                except Exception:
                    formatted = digits
                return f"$ {formatted}"

            def _amount_changed():
                current = amount_input.text() or ""
                formatted = _format_amount(current)
                if formatted == current:
                    return
                amount_input.blockSignals(True)
                amount_input.setText(formatted)
                amount_input.setCursorPosition(len(formatted))
                amount_input.blockSignals(False)

            amount_input.textChanged.connect(_amount_changed)

            btns = QHBoxLayout()
            btns.addStretch()
            cancel_btn = QPushButton("Cancelar")
            ok_btn = QPushButton("Confirmar")
            btns.addWidget(cancel_btn)
            btns.addWidget(ok_btn)
            lay.addLayout(btns)

            cancel_btn.clicked.connect(dlg.reject)
            ok_btn.clicked.connect(dlg.accept)

            if dlg.exec_() != QDialog.Accepted:
                return

            pwd = (pass_input.text() or "").strip()
            if pwd != self._get_cash_password():
                QMessageBox.warning(self, "Retirar efectivo", "Contraseña incorrecta.")
                return

            monto_txt = (
                (amount_input.text() or "")
                .replace("$", "")
                .replace(".", "")
                .replace(",", "")
                .strip()
            )
            try:
                monto = float(monto_txt)
            except Exception:
                monto = 0.0
            if monto <= 0:
                QMessageBox.warning(
                    self, "Retirar efectivo", "El monto debe ser mayor a 0."
                )
                return
            available = max(
                0.0,
                float(getattr(self, "_cash_local_total", 0) or 0)
                - float(getattr(self, "_cash_withdrawn_total", 0) or 0),
            )
            if monto > available:
                QMessageBox.warning(
                    self, "Retirar efectivo", "El monto supera el efectivo disponible."
                )
                return
            local_arg = (getattr(self, "_current_local_arg", "") or "").strip() or (
                self.user_local or ""
            )
            ok, msg = vm.add_cash_withdrawal(local_arg, monto, self.username)
            if not ok:
                QMessageBox.warning(self, "Retirar efectivo", msg)
                return
            try:
                self._cash_withdrawn_total = float(
                    getattr(self, "_cash_withdrawn_total", 0) or 0
                ) + float(monto or 0)
            except Exception:
                pass
            self._update_cash_local_label()
        except Exception:
            QMessageBox.warning(
                self, "Retirar efectivo", "No se pudo registrar el retiro."
            )

    def _get_cash_password(self) -> str:
        pwd = vm.get_cash_withdraw_password(default="")
        if pwd:
            return pwd
        cfg = self._load_config()
        pwd = (cfg.get("cash_withdraw_password") or "").strip()
        return pwd or "Manavella10"

    def _load_config(self) -> dict:
        for p in db_mod.CONFIG_PATHS:
            try:
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
        return {}

    def ver_detalles_venta(self, index):
        """Muestra los detalles completos de una venta"""
        row = index.row()
        if row >= self.ventas_model.rowCount():
            return
        venta = None
        try:
            if (
                hasattr(self, "_rows_cache")
                and self._rows_cache
                and row < len(self._rows_cache)
            ):
                venta = self._rows_cache[row]
        except Exception:
            venta = None
        if not venta:
            return

        venta_id = None
        try:
            venta_id = int(venta.get("id") or 0)
        except Exception:
            venta_id = None

        if venta_id:
            try:
                detailed = vhm.get_venta_detallada(venta_id)
            except Exception:
                detailed = None
            if isinstance(detailed, dict) and detailed:
                for k in (
                    "history_kind",
                    "history_source_type",
                    "history_original_fecha",
                    "pago_completado_fecha",
                    "pago_completado_monto",
                    "pago_completado_forma",
                    "pago_completado_tipo",
                ):
                    if venta.get(k) not in (None, ""):
                        detailed[k] = venta.get(k)
                # Completar datos base si faltan en detalle
                for k in (
                    "cliente_nombre",
                    "fecha",
                    "local",
                    "total",
                    "estado",
                    "numero_venta",
                    "forma_pago",
                    "tipo_pago",
                ):
                    if k not in detailed or detailed.get(k) in (None, ""):
                        detailed[k] = venta.get(k)
                venta = detailed

        self._mostrar_detalles_venta(venta)

    def _mostrar_detalles_venta(self, venta):
        """Renderiza panel de detalles para una venta."""
        try:
            # limpiar
            while self.detalles_content_layout.count():
                item = self.detalles_content_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

            # Resumen
            resumen = QLabel(
                f"Cliente: {venta.get('cliente_nombre','')}\n"
                f"Fecha: {_fmt_datetime(_row_history_fecha(venta))}\n"
                f"Local: {venta.get('local','')}\n"
                f"Total: ${_fmt_money(venta.get('total'))}\n"
                f"Estado: {venta.get('estado','')}"
            )
            resumen.setStyleSheet(f"color:{TEXT};")
            self.detalles_content_layout.addWidget(resumen)

            if venta.get("history_kind") == "completion":
                tipo_comp = (venta.get("history_source_type") or "").strip().lower()
                monto_comp = float(
                    venta.get("pago_completado_monto") or venta.get("total") or 0
                )
                fecha_origen = _fmt_datetime(
                    venta.get("history_original_fecha") or _row_original_fecha(venta)
                )
                forma_comp = (
                    venta.get("pago_completado_forma")
                    or venta.get("forma_pago")
                    or "Sin dato"
                )
                if tipo_comp == "domicilio":
                    texto = f"Pago completado en domicilio. Venta original: {fecha_origen}. Cobrado: ${_fmt_money(monto_comp)} por {forma_comp}."
                else:
                    texto = f"Resto de seña completado. Venta original: {fecha_origen}. Cobrado: ${_fmt_money(monto_comp)} por {forma_comp}."
                extra_lbl = QLabel(texto)
                extra_lbl.setWordWrap(True)
                extra_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
                self.detalles_content_layout.addWidget(extra_lbl)

            # Tabla de items
            items = venta.get("items") or []
            productos_table = QTableWidget()
            productos_table.setColumnCount(4)
            productos_table.setHorizontalHeaderLabels(
                ["Producto", "Cant", "Precio", "Subtotal"]
            )
            productos_table.setRowCount(len(items))
            productos_table.verticalHeader().setVisible(False)
            productos_table.setEditTriggers(QTableWidget.NoEditTriggers)
            productos_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            productos_table.setStyleSheet(
                "QTableWidget{background:#1f1f22;color:#e5e7eb;alternate-background-color:#1b1b1f;"
                "border:1px solid #2a2a2e;border-radius:10px;}"
                "QHeaderView::section{background:#222226;color:#e5e7eb;font-weight:700;padding:6px;border:none;}"
            )
            for i, it in enumerate(items):
                nombre = (
                    (it.get("nombre") or it.get("producto") or "")
                    if isinstance(it, dict)
                    else ""
                )
                cant = int(it.get("cantidad") or 0) if isinstance(it, dict) else 0
                precio = (
                    float(it.get("precio_unitario") or it.get("precio") or 0)
                    if isinstance(it, dict)
                    else 0
                )
                subtotal = float(it.get("subtotal") or (precio * cant))
                productos_table.setItem(i, 0, QTableWidgetItem(str(nombre)))
                productos_table.setItem(i, 1, QTableWidgetItem(str(cant)))
                productos_table.setItem(
                    i, 2, QTableWidgetItem(f"${_fmt_money(precio)}")
                )
                productos_table.setItem(
                    i, 3, QTableWidgetItem(f"${_fmt_money(subtotal)}")
                )
            header = productos_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
            self.detalles_content_layout.addWidget(productos_table)

            # Botones
            btns = QHBoxLayout()
            ver_boleta_btn = QPushButton("Ver boleta")
            ver_boleta_btn.clicked.connect(lambda: self.ver_boleta_row(venta))
            btns.addWidget(ver_boleta_btn)

            estado = (venta.get("estado") or "").strip().lower()
            if estado != "cancelada" and venta.get("history_kind") != "completion":
                cancelar_btn = QPushButton("Cancelar venta")
                cancelar_btn.clicked.connect(lambda: self.cancelar_venta_dialog(venta))
                btns.addWidget(cancelar_btn)

                try:
                    pendiente = float(venta.get("monto_pendiente") or 0)
                except Exception:
                    pendiente = 0
                if pendiente > 0.01 and not _is_credito_personal_row(venta):
                    completar_btn = QPushButton("Completar pago")
                    completar_btn.clicked.connect(
                        lambda: self.completar_pago_dialog(venta.get("id"))
                    )
                    btns.addWidget(completar_btn)

                ver_domicilio_btn = QPushButton("Ver cobros domicilio")
                ver_domicilio_btn.clicked.connect(
                    lambda: self.ver_cobros_domicilio_dialog(venta.get("id"))
                )
                btns.addWidget(ver_domicilio_btn)

            btns.addStretch()
            btns_wrap = QWidget()
            btns_wrap.setLayout(btns)
            self.detalles_content_layout.addWidget(btns_wrap)

            self.detalles_widget.setVisible(True)
        except Exception:
            pass

    def ver_boleta_row(self, venta):
        if not isinstance(venta, dict):
            return
        venta_id = int(venta.get("id") or 0)
        if venta_id <= 0:
            QMessageBox.warning(self, "Error", "No se encontro la venta seleccionada.")
            return
        if venta.get("history_kind") == "completion":
            success, message = vm.generar_pdf_completacion_pago(venta_id)
        else:
            success, message = vm.generar_pdf_boleta(venta_id)
        if success:
            self._open_pdf_path(message)
        else:
            QMessageBox.warning(
                self, "Error", f"No se pudo generar la boleta: {message}"
            )

    def ver_boleta_venta(self, venta_id):
        """Abre la boleta PDF de esta venta"""
        success, message = vm.generar_pdf_boleta(venta_id)
        if success:
            self._open_pdf_path(message)
        else:
            QMessageBox.warning(
                self, "Error", f"No se pudo generar la boleta: {message}"
            )

    def _open_pdf_path(self, filepath: str):
        """Abre un PDF usando el OS; filepath puede venir de generar_pdf_boleta"""
        try:
            if not filepath:
                QMessageBox.warning(self, "PDF", "No se recibió ruta del PDF")
                return
            if platform.system() == "Windows":
                os.startfile(filepath)
            elif platform.system() == "Darwin":
                subprocess.call(["open", filepath])
            else:
                subprocess.call(["xdg-open", filepath])
        except Exception as e:
            QMessageBox.warning(self, "PDF", f"No se pudo abrir el PDF: {e}")

    def _calc_reintegro_info(self, venta_detallada):
        pagos = venta_detallada.get("pagos") or []
        total_pagado = 0.0
        lineas = []
        if pagos:
            for p in pagos:
                forma = (p.get("forma") or "").strip() or "Otros"
                try:
                    monto = float(p.get("monto") or 0)
                except Exception:
                    monto = 0.0
                total_pagado += monto
                lineas.append(f"{forma}: ${_fmt_money(monto)}")
        else:
            try:
                total_pagado = float(venta_detallada.get("monto_pagado") or 0)
            except Exception:
                total_pagado = 0.0
            if total_pagado <= 0:
                try:
                    total_pagado = float(venta_detallada.get("total") or 0)
                except Exception:
                    total_pagado = 0.0
            forma = (venta_detallada.get("forma_pago") or "").strip() or "Sin dato"
            lineas.append(f"{forma}: ${_fmt_money(total_pagado)}")
        try:
            pendiente = float(venta_detallada.get("monto_pendiente") or 0)
        except Exception:
            pendiente = 0.0
        if _is_credito_personal_row(venta_detallada):
            pendiente = 0.0
        envio_no_reintegro = 0.0
        try:
            incluye_envio = int(venta_detallada.get("incluye_envio") or 0)
            precio_envio = float(venta_detallada.get("precio_envio") or 0)
            forma_envio = (venta_detallada.get("forma_pago_envio") or "").strip()
            if incluye_envio == 1 and precio_envio > 0 and forma_envio:
                envio_no_reintegro = precio_envio
        except Exception:
            envio_no_reintegro = 0.0
        if envio_no_reintegro > 0:
            total_pagado = max(0.0, total_pagado - envio_no_reintegro)
        return total_pagado, lineas, pendiente, envio_no_reintegro

    def cancelar_venta_dialog(self, venta_detallada):
        venta_id = venta_detallada.get("id")
        if not venta_id:
            QMessageBox.warning(
                self, "Cancelar venta", "No se encontro la venta seleccionada"
            )
            return

        estado = (venta_detallada.get("estado") or "").strip().lower()
        if estado == "cancelada":
            QMessageBox.warning(self, "Cancelar venta", "La venta ya esta cancelada")
            return

        total_pagado, lineas, pendiente, envio_no_reintegro = self._calc_reintegro_info(
            venta_detallada
        )
        items = venta_detallada.get("items") or []
        devolucion_items = None
        devolucion_resumen = ""
        reintegro_final = float(total_pagado or 0)

        dlg = QDialog(self)
        dlg.setWindowTitle("Cancelar venta")
        lay = QVBoxLayout(dlg)

        reintegro_lbl = QLabel(f"Reintegrar al cliente: ${_fmt_money(total_pagado)}")
        reintegro_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        lay.addWidget(reintegro_lbl)

        if pendiente > 0:
            pendiente_lbl = QLabel(
                f"Pendiente: ${_fmt_money(pendiente)} (no se reintegra)"
            )
            pendiente_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
            lay.addWidget(pendiente_lbl)
        if envio_no_reintegro > 0:
            envio_lbl = QLabel(
                f"Envio no se reintegra: ${_fmt_money(envio_no_reintegro)}"
            )
            envio_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
            lay.addWidget(envio_lbl)

        metodos_title = QLabel("Metodos de pago:")
        metodos_title.setStyleSheet(
            f"color: {TEXT}; font-weight: 700; margin-top: 6px;"
        )
        lay.addWidget(metodos_title)

        metodos_lbl = QLabel("\n".join(lineas) if lineas else "Sin datos")
        metodos_lbl.setWordWrap(True)
        metodos_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        lay.addWidget(metodos_lbl)

        motivo_lbl = QLabel("Motivo de cancelacion (obligatorio):")
        motivo_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700; margin-top: 10px;")
        lay.addWidget(motivo_lbl)

        motivo_input = QLineEdit()
        motivo_input.setPlaceholderText("Ej: cliente anulo la compra")
        lay.addWidget(motivo_input)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Confirmar cancelacion")
        btns.button(QDialogButtonBox.Cancel).setText("Volver")
        lay.addWidget(btns)

        def _try_accept():
            motivo = (motivo_input.text() or "").strip()
            if not motivo:
                QMessageBox.warning(
                    dlg, "Cancelar venta", "Tenes que escribir un motivo"
                )
                return
            nonlocal devolucion_items, devolucion_resumen, reintegro_final
            devolucion_items = None
            devolucion_resumen = ""
            reintegro_final = float(total_pagado or 0)
            if items:
                result = self._seleccionar_productos_devolucion(
                    items, default_checked=True
                )
                if result is None:
                    return
                devolucion_items, devolucion_resumen = result
                if devolucion_items is not None:
                    try:
                        total_qty = sum(int(it.get("cantidad") or 0) for it in items)
                    except Exception:
                        total_qty = 0
                    try:
                        selected_qty = sum(
                            int(it.get("cantidad") or 0) for it in devolucion_items
                        )
                    except Exception:
                        selected_qty = 0
                    if total_qty > 0 and selected_qty >= total_qty:
                        reintegro_final = float(total_pagado or 0)
                    else:
                        reintegro_final = self._calc_reintegro_parcial(
                            venta_detallada, devolucion_items
                        )
                        reintegro_final = min(
                            float(total_pagado or 0), float(reintegro_final or 0)
                        )
            dlg.accept()

        btns.accepted.connect(_try_accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_() != QDialog.Accepted:
            return

        motivo = (motivo_input.text() or "").strip()
        motivo_db = motivo
        if devolucion_resumen:
            motivo_db = f"{motivo} | Devolucion: {devolucion_resumen}"
        try:
            total_qty = sum(int(it.get("cantidad") or 0) for it in items)
        except Exception:
            total_qty = 0
        try:
            selected_qty = sum(
                int(it.get("cantidad") or 0) for it in (devolucion_items or [])
            )
        except Exception:
            selected_qty = 0
        es_total = total_qty > 0 and selected_qty >= total_qty
        if es_total:
            ok, msg = vm.cancelar_venta_con_devolucion(
                int(venta_id),
                motivo_db,
                self.username,
                devolucion_items=devolucion_items,
            )
        else:
            ok, msg = vm.devolver_productos_parcial(
                int(venta_id),
                motivo_db,
                self.username,
                devolucion_items or [],
            )
        if ok:
            detalle_metodos = "; ".join(lineas) if lineas else "Sin datos"
            extra_envio = ""
            if envio_no_reintegro > 0:
                extra_envio = (
                    f"\nEnvio no se reintegra: ${_fmt_money(envio_no_reintegro)}"
                )
            extra_devolucion = ""
            if devolucion_resumen:
                extra_devolucion = f"\nDevolucion: {devolucion_resumen}"
            titulo = "Cancelar venta" if es_total else "Devolucion parcial"
            cuerpo = (
                f"Venta cancelada.\nReintegrar: ${_fmt_money(reintegro_final)}\nMetodos: {detalle_metodos}{extra_envio}{extra_devolucion}"
                if es_total
                else f"Productos devueltos.\nReintegrar: ${_fmt_money(reintegro_final)}\nMetodos: {detalle_metodos}{extra_envio}{extra_devolucion}"
            )
            QMessageBox.information(self, titulo, cuerpo)
            self.cargar_ventas()
            self.ocultar_detalles()
        else:
            QMessageBox.warning(self, "Cancelar venta", msg)

    def _seleccionar_productos_devolucion(
        self, items: list, default_checked: bool = False
    ):
        dlg = QDialog(self)
        dlg.setWindowTitle("Productos a devolver")
        lay = QVBoxLayout(dlg)

        info = QLabel("Selecciona los productos que se devuelven al stock:")
        info.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        lay.addWidget(info)

        checks = []
        for it in items:
            nombre = (
                it.get("producto_nombre") or it.get("nombre") or ""
            ).strip() or "Producto"
            try:
                qty = int(it.get("cantidad") or 0)
            except Exception:
                qty = 0
            cb = QCheckBox(f"{nombre} x{qty}")
            cb.setStyleSheet(f"color: {TEXT};")
            if default_checked:
                cb.setChecked(True)
            lay.addWidget(cb)
            checks.append((cb, it, qty))

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Confirmar")
        btns.button(QDialogButtonBox.Cancel).setText("Cancelar")
        lay.addWidget(btns)

        def _try_accept():
            if not any(cb.isChecked() for cb, _, _ in checks):
                QMessageBox.warning(
                    dlg, "Devolucion", "Selecciona al menos un producto."
                )
                return
            dlg.accept()

        btns.accepted.connect(_try_accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_() != QDialog.Accepted:
            return None

        selected = []
        resumen_parts = []
        for cb, it, qty in checks:
            if not cb.isChecked():
                continue
            try:
                pid = int(it.get("producto_id") or 0)
            except Exception:
                pid = 0
            if pid > 0 and qty > 0:
                selected.append({"producto_id": pid, "cantidad": qty})
                nombre = (
                    it.get("producto_nombre") or it.get("nombre") or ""
                ).strip() or "Producto"
                resumen_parts.append(f"{nombre} x{qty}")
        return selected, "; ".join(resumen_parts)

    def _calc_reintegro_parcial(self, venta_detallada, devolucion_items: list) -> float:
        by_pid = {}
        for it in venta_detallada.get("items") or []:
            try:
                pid = int(it.get("producto_id") or 0)
            except Exception:
                pid = 0
            if pid > 0:
                by_pid[pid] = it
        total = 0.0
        for d in devolucion_items or []:
            try:
                pid = int(d.get("producto_id") or 0)
                qty = int(d.get("cantidad") or 0)
            except Exception:
                pid = 0
                qty = 0
            if pid <= 0 or qty <= 0:
                continue
            it = by_pid.get(pid) or {}
            try:
                subtotal = (
                    float(it.get("subtotal"))
                    if it.get("subtotal") is not None
                    else None
                )
            except Exception:
                subtotal = None
            if subtotal is not None:
                try:
                    item_qty = float(it.get("cantidad") or 0)
                except Exception:
                    item_qty = 0.0
                if item_qty > 0:
                    total += (float(subtotal) / item_qty) * float(qty)
                    continue
            try:
                precio = float(it.get("precio_unitario") or 0)
            except Exception:
                precio = 0.0
            total += float(qty) * precio
        return total

    def ocultar_detalles(self):
        """Oculta el área de detalles"""
        self.detalles_widget.setVisible(False)

        # Limpiar contenido
        while self.detalles_content_layout.count():
            item = self.detalles_content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def completar_pago_dialog(self, venta_id: int):
        """Diálogo para completar pago de una venta con seña"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Completar pago")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Selecciona la forma de pago para completar:"))

        opciones = [
            "Efectivo",
            "Transferencia",
            "Tarjeta de Débito",
            "Tarjeta de Crédito",
            "QR",
        ]
        group = QButtonGroup(dlg)
        radios = []
        for i, txt in enumerate(opciones):
            rb = QRadioButton(txt)
            if i == 0:
                rb.setChecked(True)
            group.addButton(rb, i)
            radios.append(rb)
            lay.addWidget(rb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_() == QDialog.Accepted:
            sel_id = group.checkedId()
            forma = opciones[sel_id if sel_id >= 0 else 0]
            success, msg = vm.completar_pago_sena(venta_id, forma, self.username)
            if success:
                QMessageBox.information(self, "Pago", "Pago completado correctamente")
                pdf_ok, pdf_path = vm.generar_pdf_boleta(venta_id)
                if pdf_ok:
                    self._open_pdf_path(pdf_path)
                else:
                    QMessageBox.warning(
                        self, "Boleta", f"No se pudo generar la boleta: {pdf_path}"
                    )
                # Refrescar lista y totales
                self.cargar_ventas()
                # Ocultar detalles; el usuario puede reabrir para ver cambios
                self.ocultar_detalles()
            else:
                QMessageBox.warning(self, "Error", msg)

    def ver_cobros_domicilio_dialog(self, venta_id: int):
        """Muestra cobros en domicilio (requiere contraseÃ±a)."""
        if not venta_id:
            QMessageBox.warning(self, "Domicilio", "Venta no encontrada.")
            return
        pwd, ok = QInputDialog.getText(
            self, "Domicilio", "ContraseÃ±a:", QLineEdit.Password
        )
        if not ok:
            return
        if (pwd or "").strip() != self._get_cash_password():
            QMessageBox.warning(self, "Domicilio", "ContraseÃ±a incorrecta.")
            return

        try:
            pagos = vm.get_domicilio_pagos(int(venta_id))
        except Exception:
            pagos = []

        dlg = QDialog(self)
        dlg.setWindowTitle("Cobros en domicilio")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        if not pagos:
            lay.addWidget(QLabel("No hay cobros en domicilio registrados."))
        else:
            table = QTableWidget()
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["Fecha", "Monto", "Local", "Usuario"])
            table.setRowCount(len(pagos))
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setStyleSheet(
                "QTableWidget{background:#1f1f22;color:#e5e7eb;alternate-background-color:#1b1b1f;"
                "border:1px solid #2a2a2e;border-radius:10px;}"
                "QHeaderView::section{background:#222226;color:#e5e7eb;font-weight:700;padding:6px;border:none;}"
            )
            total = 0.0
            for i, p in enumerate(pagos):
                fecha = p.get("fecha") or ""
                try:
                    monto = float(p.get("monto") or 0)
                except Exception:
                    monto = 0.0
                total += monto
                local = p.get("local") or ""
                usuario = p.get("usuario") or ""
                table.setItem(i, 0, QTableWidgetItem(str(fecha)))
                table.setItem(i, 1, QTableWidgetItem(f"${_fmt_money(monto)}"))
                table.setItem(i, 2, QTableWidgetItem(str(local)))
                table.setItem(i, 3, QTableWidgetItem(str(usuario)))
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
            lay.addWidget(table)
            lay.addWidget(QLabel(f"Total cobrado en domicilio: ${_fmt_money(total)}"))

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.button(QDialogButtonBox.Ok).setText("Cerrar")
        lay.addWidget(btns)
        btns.accepted.connect(dlg.accept)

        dlg.exec_()

    def closeEvent(self, event):
        try:
            if self._load_thread and self._load_thread.isRunning():
                self._load_thread.requestInterruption()
                self._load_thread.wait(200)
        except Exception:
            pass
        super().closeEvent(event)
