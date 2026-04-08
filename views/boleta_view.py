# views/boleta_view.py
import json
import os
import re
import time
from datetime import datetime, timedelta

from PyQt5.QtCore import (
    QAbstractTableModel,
    QByteArray,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFont, QGuiApplication
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import stock_model as sm
from models import ventas_model as vm
from utils.flow_layout import FlowContainer, FlowLayout
from utils.ui_scale import scale, scale_font

_APPDATA = os.environ.get("APPDATA")
if _APPDATA:
    PREFS_DIR = os.path.join(_APPDATA, "Manarey")
else:
    PREFS_DIR = os.path.join(os.path.expanduser("~"), ".manarey_prefs")
PREFS_PATH = os.path.join(PREFS_DIR, "user_prefs.json")
PRODUCT_SELECTOR_LAYOUT_VERSION = 3

DORADO = "#C9A040"
DARK = "#1f1f22"
CARD = "#232327"
BORDER = "#34343a"
TEXT = "#ECECF1"
MUTED = "#c9c9cf"

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _split_search_terms(text: str) -> list:
    return _WORD_RE.findall((text or "").lower())


def _match_terms_prefix(text: str, terms: list) -> bool:
    if not terms:
        return True
    words = _WORD_RE.findall((text or "").lower())
    if not words:
        return False
    for term in terms:
        if not any(w.startswith(term) for w in words):
            return False
    return True


PLAZAS = ["1 plaza", "1 1/2 plaza", "2 plazas", "2 1/2 plazas", "Queen", "King"]
RODADOS = [
    'Rodado 12"',
    'Rodado 14"',
    'Rodado 16"',
    'Rodado 20"',
    'Rodado 24"',
    'Rodado 26"',
    'Rodado 29"',
]
SELECTOR_LOCALES_FALLBACK = ["Cane", "Vidriera", "Longchamps", "Glew", "Estacion"]


def _fmt_money(v):
    try:
        return "{:,}".format(int(v)).replace(",", ".")
    except Exception:
        return str(v)


def _is_credit_card_method(method: str) -> bool:
    return (method or "").strip().lower() == "tarjeta de credito"


def _clean_local_label(name: str) -> str:
    val = (name or "").strip()
    if " " in val:
        token, rest = val.split(" ", 1)
        if token and set(token) == {"?"}:
            val = rest.lstrip()
        elif (
            len(token) <= 6 and any(ord(c) > 127 for c in token) and not token.isalpha()
        ):
            val = rest.lstrip()
    return val.strip()


_DELIVERY_DAY_LABELS = [
    "Lunes",
    "Martes",
    "Miercoles",
    "Jueves",
    "Viernes",
    "Sabado",
    "Domingo",
]


def _build_delivery_day_options():
    today = datetime.now().date()
    options = [(today.strftime("%Y-%m-%d"), "Hoy")]
    for offset in range(1, 7):
        d = today + timedelta(days=offset)
        options.append((d.strftime("%Y-%m-%d"), _DELIVERY_DAY_LABELS[d.weekday()]))
    return options


class CheckoutDialog(QDialog):
    """Dialogo para pago (incluye pago dividido) y envio."""

    PAYMENT_METHODS = [
        "Efectivo",
        "QR",
        "Transferencia",
        "Tarjeta de debito",
        "Tarjeta de credito",
    ]

    def __init__(self, base_total: int, parent=None):
        super().__init__(parent)
        self.base_total = int(base_total or 0)
        self.result_data = None

        self.setWindowTitle("Confirmar pago")
        self.setModal(True)
        self.setFixedWidth(scale(520))
        self.setStyleSheet(f"QDialog{{background:{DARK};}} QLabel{{color:{TEXT};}}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        title = QLabel("Datos de pago y envio")
        try:
            fnt = QFont("Segoe UI", 16, QFont.Bold)
            fnt.setPointSizeF(scale_font(16))
            title.setFont(fnt)
        except Exception:
            title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setStyleSheet(f"color:{DORADO};")
        lay.addWidget(title)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(QLabel("Forma de pago:"))
        self.method1_cb = QComboBox()
        self.method1_cb.addItems(self.PAYMENT_METHODS)
        self.method1_cb.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
            f"QComboBox QAbstractItemView{{background:{CARD};color:{TEXT};"
            f"border:1px solid {BORDER};selection-background-color:#fbbf24;"
            "selection-color:#1a202c;outline:0;}}"
        )
        row1.addWidget(self.method1_cb)
        lay.addLayout(row1)

        self.split_cb = QCheckBox("Pago dividido")
        self.split_cb.setStyleSheet(f"color:{TEXT};")
        self.split_cb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        split_row = QHBoxLayout()
        split_row.addWidget(self.split_cb)
        self.split_three_cb = QCheckBox("Usar 3 metodos")
        self.split_three_cb.setStyleSheet(f"color:{TEXT};")
        split_row.addWidget(self.split_three_cb)
        split_row.addStretch()
        lay.addLayout(split_row)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addWidget(QLabel("Metodo 2:"))
        self.method2_cb = QComboBox()
        self.method2_cb.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
            f"QComboBox QAbstractItemView{{background:{CARD};color:{TEXT};"
            f"border:1px solid {BORDER};selection-background-color:#fbbf24;"
            "selection-color:#1a202c;outline:0;}}"
        )
        row2.addWidget(self.method2_cb)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(10)
        row3.addWidget(QLabel("Monto metodo 1:"))
        self.amount1_spin = QSpinBox()
        self.amount1_spin.setRange(0, 999_999_999)
        self.amount1_spin.setStyleSheet(
            f"QSpinBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.amount1_spin.setSingleStep(100)
        row3.addWidget(self.amount1_spin)
        lay.addLayout(row3)

        row3b = QHBoxLayout()
        row3b.setSpacing(10)
        row3b.addWidget(QLabel("Metodo 3:"))
        self.method3_cb = QComboBox()
        self.method3_cb.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
            f"QComboBox QAbstractItemView{{background:{CARD};color:{TEXT};"
            f"border:1px solid {BORDER};selection-background-color:#fbbf24;"
            "selection-color:#1a202c;outline:0;}}"
        )
        row3b.addWidget(self.method3_cb)
        row3b.addWidget(QLabel("Monto metodo 2:"))
        self.amount2_spin = QSpinBox()
        self.amount2_spin.setRange(0, 999_999_999)
        self.amount2_spin.setStyleSheet(
            f"QSpinBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.amount2_spin.setSingleStep(100)
        row3b.addWidget(self.amount2_spin)
        lay.addLayout(row3b)

        self.remaining_lbl = QLabel("")
        self.remaining_lbl.setStyleSheet("color:#b9bdc7;")
        lay.addWidget(self.remaining_lbl)

        self.shipping_cb = QCheckBox("Con envio")
        self.shipping_cb.setStyleSheet(f"color:{TEXT};")
        lay.addWidget(self.shipping_cb)

        row4 = QHBoxLayout()
        row4.setSpacing(10)
        row4.addWidget(QLabel("Monto envio:"))
        self.shipping_spin = QSpinBox()
        self.shipping_spin.setRange(0, 999_999_999)
        self.shipping_spin.setSingleStep(100)
        self.shipping_spin.setStyleSheet(
            f"QSpinBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        row4.addWidget(self.shipping_spin)
        lay.addLayout(row4)

        row5 = QHBoxLayout()
        row5.setSpacing(10)
        row5.addWidget(QLabel("Entre calles:"))
        self.entre_calles_input = QLineEdit()
        self.entre_calles_input.setPlaceholderText("Ej: San Martin y Belgrano")
        self.entre_calles_input.setStyleSheet(
            f"QLineEdit{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        row5.addWidget(self.entre_calles_input)
        lay.addLayout(row5)

        self.total_lbl = QLabel("")
        self.total_lbl.setStyleSheet("color:#d6d6de; font-weight:700;")
        lay.addWidget(self.total_lbl)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setStyleSheet(
            f"QPushButton{{background:{BORDER};color:{TEXT};border:none;border-radius:10px;"
            "padding:10px 18px;font-weight:700;}}"
            "QPushButton:hover{background:#3e3e44;}"
        )
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        ok_btn = QPushButton("Continuar")
        ok_btn.setStyleSheet(
            f"QPushButton{{background:{DORADO};color:#171717;border:none;border-radius:10px;"
            "padding:10px 18px;font-weight:800;}}"
            "QPushButton:hover{background:#d4af4a;}"
        )
        ok_btn.clicked.connect(self._on_accept)
        btns.addWidget(ok_btn)
        lay.addLayout(btns)

        self.method1_cb.currentIndexChanged.connect(self._refresh)
        self.split_cb.toggled.connect(self._refresh)
        self.split_three_cb.toggled.connect(self._refresh)
        self.method2_cb.currentIndexChanged.connect(self._refresh)
        self.method3_cb.currentIndexChanged.connect(self._refresh)
        self.amount1_spin.valueChanged.connect(self._refresh)
        self.amount2_spin.valueChanged.connect(self._refresh)
        self.shipping_cb.toggled.connect(self._refresh)
        self.shipping_spin.valueChanged.connect(self._refresh)
        self.entre_calles_input.textChanged.connect(self._refresh)

        self._refresh()

    def _current_total(self) -> int:
        envio = (
            int(self.shipping_spin.value() or 0) if self.shipping_cb.isChecked() else 0
        )
        return int(self.base_total) + envio

    def _refresh_split_method_options(self):
        method1 = self.method1_cb.currentText()
        current2 = self.method2_cb.currentText()
        current3 = self.method3_cb.currentText()
        opts2 = [m for m in self.PAYMENT_METHODS if m != method1]
        self.method2_cb.blockSignals(True)
        self.method2_cb.clear()
        self.method2_cb.addItems(opts2)
        if current2 in opts2:
            self.method2_cb.setCurrentText(current2)
        self.method2_cb.blockSignals(False)

        method2 = self.method2_cb.currentText()
        opts3 = [m for m in self.PAYMENT_METHODS if m not in {method1, method2}]
        self.method3_cb.blockSignals(True)
        self.method3_cb.clear()
        self.method3_cb.addItems(opts3)
        if current3 in opts3:
            self.method3_cb.setCurrentText(current3)
        self.method3_cb.blockSignals(False)

    def _refresh(self):
        total = self._current_total()
        self.shipping_spin.setEnabled(self.shipping_cb.isChecked())
        self.entre_calles_input.setEnabled(self.shipping_cb.isChecked())
        if not self.shipping_cb.isChecked():
            self.shipping_spin.blockSignals(True)
            self.shipping_spin.setValue(0)
            self.shipping_spin.blockSignals(False)
            if self.entre_calles_input.text():
                self.entre_calles_input.blockSignals(True)
                self.entre_calles_input.clear()
                self.entre_calles_input.blockSignals(False)
            total = self._current_total()

        split = self.split_cb.isChecked()
        split_three = split and self.split_three_cb.isChecked()
        self.method2_cb.setEnabled(split)
        self.amount1_spin.setEnabled(split)
        self.method3_cb.setEnabled(split_three)
        self.amount2_spin.setEnabled(split_three)
        self.split_three_cb.setEnabled(split)
        self.remaining_lbl.setVisible(split)
        self._refresh_split_method_options()

        self.amount1_spin.setMaximum(max(0, total))
        self.amount2_spin.setMaximum(max(0, total))
        if not split:
            self.amount1_spin.blockSignals(True)
            self.amount1_spin.setValue(max(0, total))
            self.amount1_spin.blockSignals(False)
            self.amount2_spin.blockSignals(True)
            self.amount2_spin.setValue(0)
            self.amount2_spin.blockSignals(False)
            self.remaining_lbl.setText("")
        else:
            if self.amount1_spin.value() > total:
                self.amount1_spin.blockSignals(True)
                self.amount1_spin.setValue(total)
                self.amount1_spin.blockSignals(False)
            amount1 = int(self.amount1_spin.value() or 0)
            remainder_after_1 = max(0, int(total) - amount1)
            if split_three:
                if self.amount2_spin.value() > remainder_after_1:
                    self.amount2_spin.blockSignals(True)
                    self.amount2_spin.setValue(remainder_after_1)
                    self.amount2_spin.blockSignals(False)
                remainder = max(
                    0, remainder_after_1 - int(self.amount2_spin.value() or 0)
                )
                self.remaining_lbl.setText(
                    f"Resto (metodo 3): ${_fmt_money(remainder)}"
                )
            else:
                self.amount2_spin.blockSignals(True)
                self.amount2_spin.setValue(0)
                self.amount2_spin.blockSignals(False)
                self.remaining_lbl.setText(
                    f"Resto (metodo 2): ${_fmt_money(remainder_after_1)}"
                )

        self.total_lbl.setText(f"Total a cobrar: ${_fmt_money(total)}")

    def _on_accept(self):
        total = self._current_total()
        if total <= 0:
            QMessageBox.warning(
                self, "Total invalido", "El total a cobrar debe ser mayor a 0."
            )
            return

        method1 = self.method1_cb.currentText()
        method2 = self.method2_cb.currentText()
        split = self.split_cb.isChecked()
        split_three = split and self.split_three_cb.isChecked()
        amount1 = int(self.amount1_spin.value() or 0)
        amount2 = int(self.amount2_spin.value() or 0)

        if split and method1 == method2:
            QMessageBox.warning(
                self, "Pago dividido", "Elegi un segundo metodo distinto al primero."
            )
            return

        if split and (amount1 <= 0 or amount1 >= total):
            QMessageBox.warning(
                self,
                "Pago dividido",
                "El monto del primer pago debe ser mayor a 0 y menor al total.",
            )
            return

        if split_three:
            method3 = self.method3_cb.currentText()
            if not method3:
                QMessageBox.warning(
                    self, "Pago dividido", "Elegi un tercer metodo distinto."
                )
                return
            if len({method1, method2, method3}) != 3:
                QMessageBox.warning(
                    self, "Pago dividido", "Los tres metodos deben ser distintos."
                )
                return
            if amount2 <= 0 or amount2 >= (total - amount1):
                QMessageBox.warning(
                    self,
                    "Pago dividido",
                    "El monto del segundo metodo debe ser mayor a 0 y menor al resto.",
                )
                return

        pagos = []
        forma_pago_label = method1
        notas = ""
        if not split:
            pagos = [{"forma": method1, "monto": float(total)}]
        elif split_three:
            remainder = int(total) - int(amount1) - int(amount2)
            pagos = [
                {"forma": method1, "monto": float(amount1)},
                {"forma": method2, "monto": float(amount2)},
                {"forma": self.method3_cb.currentText(), "monto": float(remainder)},
            ]
            forma_pago_label = (
                f"{method1} + {method2} + {self.method3_cb.currentText()}"
            )
            notas = (
                f"Pago dividido: {method1}=${amount1}, {method2}=${amount2}, "
                f"{self.method3_cb.currentText()}=${remainder}"
            )
        else:
            remainder = int(total) - int(amount1)
            pagos = [
                {"forma": method1, "monto": float(amount1)},
                {"forma": method2, "monto": float(remainder)},
            ]
            forma_pago_label = f"{method1} + {method2}"
            notas = f"Pago dividido: {method1}=${amount1}, {method2}=${remainder}"

        incluye_envio = 1 if self.shipping_cb.isChecked() else 0
        envio = int(self.shipping_spin.value() or 0) if incluye_envio else 0
        entre_calles = self.entre_calles_input.text().strip() if incluye_envio else ""

        self.result_data = {
            "incluye_envio": incluye_envio,
            "precio_envio": float(envio),
            "entre_calles": entre_calles,
            "forma_pago": forma_pago_label,
            "pagos": pagos,
            "notas": notas,
        }
        self.accept()


class ProductLoadWorker(QThread):
    data_loaded = pyqtSignal(int, list)

    def __init__(self, local: str, load_id: int, parent=None):
        super().__init__(parent)
        self.local = local
        self.load_id = load_id

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            local = _clean_local_label(self.local)
            rows = sm.list_by_local(local, "", "", "", "", "")
            products = [sm._row_to_dict(r) for r in rows] if rows else []
        except Exception:
            products = []
        try:
            if self.isInterruptionRequested():
                return
            self.data_loaded.emit(self.load_id, products)
        except Exception:
            pass


class VentaRegisterWorker(QThread):
    finished_with_result = pyqtSignal(bool, str, object)

    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.payload = dict(payload or {})

    def run(self):
        try:
            ok, msg, venta_id = vm.registrar_venta(**self.payload)
            self.finished_with_result.emit(bool(ok), str(msg or ""), venta_id)
        except Exception as e:
            self.finished_with_result.emit(False, str(e), None)


class ProductSelectionModel(QAbstractTableModel):
    HEADERS = [
        "Nombre",
        "Material",
        "Cant",
        "Categoria",
        "Color",
        "Fabricante",
        "Medida",
        "Estado",
        "Precio",
        "+",
        "-",
        "En boleta",
    ]

    def __init__(self, products=None, cart=None, parent=None):
        super().__init__(parent)
        self._rows = list(products or [])
        self._cart = cart or {}
        self._row_by_id = {}
        self._rebuild_index()

    def _rebuild_index(self):
        self._row_by_id = {p.get("id"): i for i, p in enumerate(self._rows)}

    def set_products(self, products):
        self.beginResetModel()
        self._rows = list(products or [])
        self._rebuild_index()
        self.endResetModel()

    def set_cart(self, cart):
        self._cart = cart or {}

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
        p = self._rows[row]
        if role == Qt.DisplayRole:
            if col == 0:
                return (p.get("nombre", "") or "").strip()
            if col == 1:
                return p.get("material", "") or "-"
            if col == 2:
                return str(int(p.get("cantidad", 0) or 0))
            if col == 3:
                return p.get("categoria", "") or "-"
            if col == 4:
                return p.get("color", "") or "-"
            if col == 5:
                return p.get("fabricante", "") or "-"
            if col == 6:
                return p.get("medida", "") or "-"
            if col == 7:
                return p.get("estado", "") or "-"
            if col == 8:
                try:
                    return f"${_fmt_money(int(p.get('precio_venta', 0) or 0))}"
                except Exception:
                    return "$0"
            if col == 9:
                return ""
            if col == 10:
                return ""
            if col == 11:
                pid = p.get("id")
                qty = int(self._cart.get(pid, 0) or 0)
                return "" if qty <= 0 else str(qty)
        if role == Qt.ForegroundRole:
            return QColor(TEXT)
        if role == Qt.BackgroundRole:
            return QColor("#1f1f22") if (row % 2 == 0) else QColor("#1b1b1f")
        if role == Qt.TextAlignmentRole:
            if col in (2, 11):
                return Qt.AlignCenter
            if col in (8,):
                return Qt.AlignRight | Qt.AlignVCenter
            if col in (9, 10):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def product_at(self, row):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def update_qty(self, product_id: int):
        row = self._row_by_id.get(product_id)
        if row is None:
            return
        idx = self.index(row, 11)
        self.dataChanged.emit(idx, idx, [Qt.DisplayRole])


class ProductSelectionDialog(QDialog):
    """Dialogo para seleccionar productos del stock (sin edicion)"""

    products_selected = pyqtSignal(list)

    def __init__(self, local: str, parent=None, initial_cart=None):
        super().__init__(parent)
        self.local = _clean_local_label(local)
        self.all_products = []
        locs = []
        for loc in SELECTOR_LOCALES_FALLBACK:
            clean = _clean_local_label(loc)
            if clean and clean not in locs:
                locs.append(clean)
        if self.local and self.local not in locs:
            locs.append(self.local)
        self.available_locals = locs or ([self.local] if self.local else [])
        try:
            if isinstance(initial_cart, list):
                self.cart = {}
                self.cart_products = {}
                for it in initial_cart:
                    try:
                        pid = int(it.get("producto_id") or 0)
                        qty = int(it.get("cantidad") or 0)
                    except Exception:
                        pid = 0
                        qty = 0
                    if pid > 0 and qty > 0:
                        self.cart[pid] = qty
                        self.cart_products[pid] = dict(it)
            else:
                self.cart = dict(initial_cart) if isinstance(initial_cart, dict) else {}
        except Exception:
            self.cart = {}
        self.products_data = {}
        self._load_counter = 0
        self._current_load_id = 0
        self._load_thread = None
        self._pending_category = None
        self._pending_fabricante = None
        if not hasattr(self, "cart_products"):
            self.cart_products = {}
        self._insufficient_warn_at = {}

        self.setWindowTitle("Seleccionar productos")
        self.setModal(True)
        self._load_size_pref()
        self.setStyleSheet(f"QDialog{{background:{DARK};}} QLabel{{color:{TEXT};}}")

        self.setup_ui()
        self.reset_filters()
        self.load_products()

    def _load_size_pref(self):
        try:
            os.makedirs(PREFS_DIR, exist_ok=True)
            if os.path.exists(PREFS_PATH):
                data = json.load(open(PREFS_PATH, "r", encoding="utf-8")) or {}
                size = data.get("product_selector_size")
                if size and isinstance(size, dict):
                    self.resize(int(size.get("w", 1600)), int(size.get("h", 1000)))
                    return
        except Exception:
            pass
        self.resize(1400, 900)

    def _save_size_pref(self):
        try:
            os.makedirs(PREFS_DIR, exist_ok=True)
            data = {}
            if os.path.exists(PREFS_PATH):
                data = json.load(open(PREFS_PATH, "r", encoding="utf-8")) or {}
            size = self.size()
            data["product_selector_size"] = {"w": size.width(), "h": size.height()}
            with open(PREFS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_table_layout_pref(self):
        try:
            # Evita restaurar estados viejos/corruptos del header.
            # En algunos locales ese estado estaba provocando cierre duro del dialogo.
            self._clear_legacy_table_layout_pref()
        except Exception:
            pass

    def _save_table_layout_pref(self):
        try:
            self._clear_legacy_table_layout_pref()
        except Exception:
            pass

    def _clear_legacy_table_layout_pref(self):
        try:
            if not os.path.exists(PREFS_PATH):
                return
            data = json.load(open(PREFS_PATH, "r", encoding="utf-8")) or {}
            changed = False
            for key in (
                "product_selector_table_state",
                "product_selector_layout_version",
            ):
                if key in data:
                    data.pop(key, None)
                    changed = True
            if not changed:
                return
            os.makedirs(PREFS_DIR, exist_ok=True)
            with open(PREFS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _save_filters_pref(self):
        try:
            os.makedirs(PREFS_DIR, exist_ok=True)
            data = {}
            if os.path.exists(PREFS_PATH):
                data = json.load(open(PREFS_PATH, "r", encoding="utf-8")) or {}
            filt = {
                "search": (self.search_input.text() or "")
                if getattr(self, "search_input", None)
                else "",
                "category": (self.category_combo.currentText() or "")
                if getattr(self, "category_combo", None)
                else "",
                "plaza_index": getattr(self, "plaza_combo", None).currentIndex()
                if getattr(self, "plaza_combo", None)
                else 0,
                "rodado_index": getattr(self, "rodado_combo", None).currentIndex()
                if getattr(self, "rodado_combo", None)
                else 0,
                "medida_index": getattr(self, "medida_combo", None).currentIndex()
                if getattr(self, "medida_combo", None)
                else 0,
                "fabricante_index": getattr(
                    self, "fabricante_combo", None
                ).currentIndex()
                if getattr(self, "fabricante_combo", None)
                else 0,
            }
            data["product_selector_filters"] = filt
            with open(PREFS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_filters_pref(self):
        try:
            if not os.path.exists(PREFS_PATH):
                return
            data = json.load(open(PREFS_PATH, "r", encoding="utf-8")) or {}
            filt = data.get("product_selector_filters", {}) or {}
            try:
                if filt.get("search") is not None and getattr(
                    self, "search_input", None
                ):
                    self.search_input.blockSignals(True)
                    self.search_input.setText(filt.get("search") or "")
                    self.search_input.blockSignals(False)
                if filt.get("category") is not None and getattr(
                    self, "category_combo", None
                ):
                    idx = self.category_combo.findText(filt.get("category") or "")
                    if idx >= 0:
                        self.category_combo.setCurrentIndex(idx)
                if filt.get("plaza_index") is not None and getattr(
                    self, "plaza_combo", None
                ):
                    ix = int(filt.get("plaza_index") or 0)
                    if 0 <= ix < self.plaza_combo.count():
                        self.plaza_combo.setCurrentIndex(ix)
                if filt.get("rodado_index") is not None and getattr(
                    self, "rodado_combo", None
                ):
                    ix = int(filt.get("rodado_index") or 0)
                    if 0 <= ix < self.rodado_combo.count():
                        self.rodado_combo.setCurrentIndex(ix)
                if filt.get("medida_index") is not None and getattr(
                    self, "medida_combo", None
                ):
                    ix = int(filt.get("medida_index") or 0)
                    if 0 <= ix < self.medida_combo.count():
                        self.medida_combo.setCurrentIndex(ix)
                if filt.get("fabricante_index") is not None and getattr(
                    self, "fabricante_combo", None
                ):
                    ix = int(filt.get("fabricante_index") or 0)
                    if 0 <= ix < self.fabricante_combo.count():
                        self.fabricante_combo.setCurrentIndex(ix)
            except Exception:
                pass
        except Exception:
            pass

    def _save_dialog_prefs(self):
        self._save_size_pref()
        self._save_table_layout_pref()
        self._save_filters_pref()

    def accept(self):
        self._save_dialog_prefs()
        super().accept()

    def reject(self):
        self._save_dialog_prefs()
        super().reject()

    def closeEvent(self, event):
        self._save_dialog_prefs()
        try:
            if getattr(self, "_load_thread", None) and self._load_thread.isRunning():
                self._load_thread.requestInterruption()
                self._load_thread.wait(200)
        except Exception:
            pass
        super().closeEvent(event)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        title = QLabel("Seleccionar productos para la Venta")
        title.setFont(QFont("Segoe UI", 22, QFont.Black))
        title.setStyleSheet(f"color:{DORADO};")
        layout.addWidget(title)

        filters_container = FlowContainer()
        filters_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        filters = FlowLayout(filters_container, margin=0, spacing=10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar producto...")
        self.search_input.setMinimumWidth(260)
        self.search_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet(
            f"QLineEdit{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        # Conectar múltiples señales para asegurar que se detecte cuando se limpie
        self.search_input.textChanged.connect(self.on_search_changed)
        self.search_input.returnPressed.connect(self.on_search_changed)
        filters.addWidget(self.search_input)

        self.local_combo = QComboBox()
        shop = "\U0001F3EA "
        pin = "\U0001F4CD "
        for loc in self.available_locals:
            label = f"{shop}{loc}" if loc == self.local else f"{pin}{loc}"
            self.local_combo.addItem(label, loc)
        if self.local:
            idx = self.local_combo.findData(self.local)
            if idx >= 0:
                self.local_combo.setCurrentIndex(idx)
        self.local_combo.setVisible(True)
        self.local_combo.setMinimumWidth(170)
        self.local_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.local_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.local_combo.currentIndexChanged.connect(self.load_products)
        filters.addWidget(self.local_combo)

        self.category_combo = QComboBox()
        self.category_combo.addItem("Todas las categorias")
        self.category_combo.setMinimumWidth(180)
        self.category_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.category_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.category_combo.currentIndexChanged.connect(self.filter_products)
        filters.addWidget(self.category_combo)

        self.plaza_combo = QComboBox()
        self.plaza_combo.addItems(["Plazas"] + PLAZAS)
        self.plaza_combo.setMinimumWidth(120)
        self.plaza_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.plaza_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.plaza_combo.currentIndexChanged.connect(self.filter_products)
        filters.addWidget(self.plaza_combo)

        self.rodado_combo = QComboBox()
        self.rodado_combo.addItems(["Rodado"] + RODADOS)
        self.rodado_combo.setMinimumWidth(110)
        self.rodado_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.rodado_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.rodado_combo.currentIndexChanged.connect(self.filter_products)
        filters.addWidget(self.rodado_combo)

        self.medida_combo = QComboBox()
        self.medida_combo.addItems(["Medida"] + sm.ALLOWED_MEDIDAS)
        self.medida_combo.setMinimumWidth(140)
        self.medida_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.medida_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.medida_combo.currentIndexChanged.connect(self.filter_products)
        filters.addWidget(self.medida_combo)

        self.fabricante_combo = QComboBox()
        self.fabricante_combo.addItem("Fabricante")
        self.fabricante_combo.setMinimumWidth(150)
        self.fabricante_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.fabricante_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;}}"
        )
        self.fabricante_combo.currentIndexChanged.connect(self.filter_products)
        filters.addWidget(self.fabricante_combo)

        reset_btn = QPushButton("Restablecer")
        reset_btn.clicked.connect(self.reset_filters)
        filters.addWidget(reset_btn)

        refresh_btn = QPushButton("Refrescar")
        refresh_btn.clicked.connect(self.load_products)
        filters.addWidget(refresh_btn)
        layout.addWidget(filters_container)

        self.table = QTableView()
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setStyleSheet(
            "QTableView{background:#1f1f22;color:#e5e7eb;alternate-background-color:#1b1b1f;"
            "border:1px solid #2a2a2e;border-radius:10px;}"
            "QHeaderView::section{background:#222226;color:#e5e7eb;font-weight:700;padding:8px;border:none;}"
        )
        self.table_model = ProductSelectionModel([], self.cart, self)
        self.table.setModel(self.table_model)

        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setHighlightSections(False)
        self._load_table_layout_pref()
        self._fit_selector_table_columns()

        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self.accept_btn = QPushButton("Aceptar Seleccion")
        self.accept_btn.clicked.connect(self.accept_selection)
        self.accept_btn.setEnabled(False)
        buttons.addWidget(self.accept_btn)

        layout.addLayout(buttons)

    def _apply_selector_table_density(self):
        try:
            if not hasattr(self, "table") or not hasattr(self, "_selector_table_style"):
                return
            width = int(self.table.viewport().width() or self.table.width() or 0)
            if width < 1100:
                font = 12
                header_font = 11
                header_pad = 6
                item_pad_v = 8
                item_pad_h = 6
                row_h = 40
                btn_size = 20
            elif width < 1500:
                font = 13
                header_font = 12
                header_pad = 7
                item_pad_v = 9
                item_pad_h = 8
                row_h = 44
                btn_size = 22
            else:
                font = 14
                header_font = 13
                header_pad = 8
                item_pad_v = 10
                item_pad_h = 10
                row_h = 50
                btn_size = 24
            density = (
                font,
                header_font,
                header_pad,
                item_pad_v,
                item_pad_h,
                row_h,
                btn_size,
            )
            if getattr(self, "_selector_table_density", None) == density:
                return
            self._selector_table_density = density
            self._selector_btn_size = btn_size
            self.table.setStyleSheet(
                self._selector_table_style.format(
                    font=font,
                    header_font=header_font,
                    header_pad=header_pad,
                    item_pad_v=item_pad_v,
                    item_pad_h=item_pad_h,
                )
            )
            self.table.verticalHeader().setDefaultSectionSize(row_h)
        except Exception:
            pass

    def _fit_selector_table_columns(self):
        try:
            if not hasattr(self, "table"):
                return
            header = self.table.horizontalHeader()
            cols = self.table.columnCount()
            if cols <= 0:
                return
            btn_size = min(24, self._action_btn_size())
            for col in range(cols):
                header.setSectionResizeMode(col, QHeaderView.Stretch)

            # Ajustar por nombre para respetar orden movido por el usuario
            name_to_index = {}
            for col in range(cols):
                try:
                    name = str(self.table.model().headerData(col, Qt.Horizontal) or "")
                except Exception:
                    name = ""
                if name:
                    name_to_index[name.lower()] = col

            fixed_by_name = {
                "cant": 42,  # mas angosta
                "precio": 180,  # mas ancha
                "+": max(10, btn_size),  # columna mas angosta
                "-": max(10, btn_size),  # columna mas angosta
                "en boleta": 110,
            }
            for name, w in fixed_by_name.items():
                col = name_to_index.get(name)
                if col is not None and col < cols:
                    header.setSectionResizeMode(col, QHeaderView.Fixed)
                    header.resizeSection(col, int(w))

            header.setMinimumSectionSize(30)
        except Exception:
            pass

    def _action_btn_size(self):
        try:
            return int(getattr(self, "_selector_btn_size", 22))
        except Exception:
            return 22

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._apply_selector_table_density()
            self._fit_selector_table_columns()
        except Exception:
            pass

    def load_products(self):
        try:
            current_cat = (
                self.category_combo.currentText()
                if getattr(self, "category_combo", None)
                else "Todas las categorias"
            )
            current_fab = (
                self.fabricante_combo.currentText()
                if getattr(self, "fabricante_combo", None)
                else "Fabricante"
            )
            selected_local = (
                _clean_local_label(self.local_combo.currentData() or "")
                if getattr(self, "local_combo", None)
                else ""
            )
            if not selected_local:
                selected_local = _clean_local_label(self.local)
            if not selected_local:
                selected_local = self.local

            self._pending_category = current_cat
            self._pending_fabricante = current_fab
            self._load_counter += 1
            self._current_load_id = self._load_counter

            if getattr(self, "_load_thread", None) and self._load_thread.isRunning():
                try:
                    self._load_thread.requestInterruption()
                except Exception:
                    pass

            self._load_thread = ProductLoadWorker(
                selected_local, self._current_load_id, self
            )
            self._load_thread.data_loaded.connect(self._on_products_loaded)
            self._load_thread.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error cargando productos: {e}")

    def _on_products_loaded(self, load_id, products):
        try:
            if load_id != self._current_load_id:
                return
            current_cat = self._pending_category or "Todas las categorias"
            current_fab = self._pending_fabricante or "Fabricante"

            categories = sorted(
                set(p.get("categoria", "") for p in products if p.get("categoria"))
            )
            if getattr(self, "category_combo", None):
                self.category_combo.blockSignals(True)
                self.category_combo.clear()
                self.category_combo.addItem("Todas las categorias")
                for cat in categories:
                    self.category_combo.addItem(cat)
                idx = self.category_combo.findText(current_cat)
                self.category_combo.setCurrentIndex(idx if idx >= 0 else 0)
                self.category_combo.blockSignals(False)

            try:
                fabricantes = sorted(
                    set(
                        (p.get("fabricante") or "").strip()
                        for p in products
                        if (p.get("fabricante") or "").strip()
                    )
                )
                if getattr(self, "fabricante_combo", None):
                    self.fabricante_combo.blockSignals(True)
                    self.fabricante_combo.clear()
                    self.fabricante_combo.addItem("Fabricante")
                    for fab in fabricantes:
                        self.fabricante_combo.addItem(fab)
                    idx = self.fabricante_combo.findText(current_fab)
                    self.fabricante_combo.setCurrentIndex(idx if idx >= 0 else 0)
                    self.fabricante_combo.blockSignals(False)
            except Exception:
                pass

            self.all_products = products
            self.products_data = {p["id"]: p for p in products}
            self.filter_products()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error cargando productos: {e}")

    def on_search_changed(self, text):
        """Maneja cambios en el campo de búsqueda, incluyendo limpiar con la X"""
        # Forzar que se filtre siempre, incluso si el text está vacío
        self.filter_products()

    def reset_filters(self):
        try:
            if getattr(self, "search_input", None):
                self.search_input.setText("")
            if getattr(self, "category_combo", None):
                self.category_combo.setCurrentIndex(0)
            if getattr(self, "plaza_combo", None):
                self.plaza_combo.setCurrentIndex(0)
            if getattr(self, "rodado_combo", None):
                self.rodado_combo.setCurrentIndex(0)
            if getattr(self, "medida_combo", None):
                self.medida_combo.setCurrentIndex(0)
            if getattr(self, "fabricante_combo", None):
                self.fabricante_combo.setCurrentIndex(0)
        except Exception:
            pass
        self.filter_products()

    def filter_products(self):
        # Obtener valores de b??squeda y filtros
        search = (self.search_input.text() or "").strip().lower()
        search_terms = _split_search_terms(search)
        categoria = (self.category_combo.currentText() or "").strip()
        if categoria == "Todas las categorias":
            categoria = ""

        fabricante = (
            (getattr(self, "fabricante_combo", None).currentText() or "").strip()
            if getattr(self, "fabricante_combo", None)
            else ""
        )
        if fabricante == "Fabricante":
            fabricante = ""

        medidas = []
        try:
            if (
                getattr(self, "plaza_combo", None)
                and self.plaza_combo.currentIndex() > 0
            ):
                medidas.append(self.plaza_combo.currentText())
            if (
                getattr(self, "rodado_combo", None)
                and self.rodado_combo.currentIndex() > 0
            ):
                medidas.append(self.rodado_combo.currentText())
            if (
                getattr(self, "medida_combo", None)
                and self.medida_combo.currentIndex() > 0
            ):
                medidas.append(self.medida_combo.currentText())
        except Exception:
            medidas = []

        filtered = list(self.all_products or [])
        if search_terms:
            filtered = [
                p
                for p in filtered
                if _match_terms_prefix((p.get("nombre", "") or ""), search_terms)
            ]
        if categoria:
            cat_norm = categoria.strip().lower()
            filtered = [
                p
                for p in filtered
                if (p.get("categoria", "") or "").strip().lower() == cat_norm
            ]
        if fabricante:
            fab_norm = fabricante.strip().lower()
            filtered = [
                p
                for p in filtered
                if (p.get("fabricante", "") or "").strip().lower() == fab_norm
            ]
        if medidas:
            filtered = [p for p in filtered if (p.get("medida", "") or "") in medidas]

        # Actualizar modelo
        self.table_model.set_cart(self.cart)
        self.table_model.set_products(filtered)
        self._apply_selector_table_density()
        self._fit_selector_table_columns()
        self._refresh_table_buttons()

    def _refresh_table_buttons(self):
        try:
            model = self.table_model
            for row in range(model.rowCount()):
                p = model.product_at(row)
                if not p:
                    continue
                pid = p.get("id")

                plus_btn = QPushButton("+")
                btn_sz = min(24, self._action_btn_size())
                plus_btn.setFixedSize(btn_sz, btn_sz)
                plus_btn.clicked.connect(lambda _, p=pid: self.increment_quantity(p))
                plus_wrap = QWidget()
                plus_layout = QHBoxLayout(plus_wrap)
                plus_layout.setContentsMargins(0, 0, 0, 0)
                plus_layout.setAlignment(Qt.AlignCenter)
                plus_layout.addWidget(plus_btn)
                self.table.setIndexWidget(model.index(row, 9), plus_wrap)

                minus_btn = QPushButton("-")
                btn_sz = min(24, self._action_btn_size())
                minus_btn.setFixedSize(btn_sz, btn_sz)
                minus_btn.clicked.connect(lambda _, p=pid: self.decrement_quantity(p))
                minus_wrap = QWidget()
                minus_layout = QHBoxLayout(minus_wrap)
                minus_layout.setContentsMargins(0, 0, 0, 0)
                minus_layout.setAlignment(Qt.AlignCenter)
                minus_layout.addWidget(minus_btn)
                self.table.setIndexWidget(model.index(row, 10), minus_wrap)
        except Exception:
            pass

    def increment_quantity(self, product_id):
        product = self.products_data.get(product_id)
        if not product:
            return
        stock = int(product.get("cantidad", 0))
        current_qty = self.cart.get(product_id, 0)
        if current_qty >= stock:
            if self._should_warn_insufficient(product_id):
                QMessageBox.warning(
                    self,
                    "Stock insuficiente",
                    f"No hay mas stock disponible. Maximo: {stock}",
                )
            return
        self.cart[product_id] = current_qty + 1
        self.cart_products[product_id] = product
        self.update_quantity_display(product_id)
        self.accept_btn.setEnabled(len(self.cart) > 0)

    def decrement_quantity(self, product_id):
        current_qty = self.cart.get(product_id, 0)
        if current_qty > 0:
            self.cart[product_id] = current_qty - 1
            if self.cart[product_id] == 0:
                del self.cart[product_id]
                if product_id in self.cart_products:
                    del self.cart_products[product_id]
        self.update_quantity_display(product_id)
        self.accept_btn.setEnabled(len(self.cart) > 0)

    def update_quantity_display(self, product_id):
        try:
            self.table_model.set_cart(self.cart)
            self.table_model.update_qty(product_id)
        except Exception:
            pass

    def _should_warn_insufficient(
        self, product_id: str, cooldown_ms: int = 1200
    ) -> bool:
        now = int(time.time() * 1000)
        last = int(self._insufficient_warn_at.get(product_id, 0))
        if now - last < cooldown_ms:
            return False
        self._insufficient_warn_at[product_id] = now
        return True

    def accept_selection(self):
        if not self.cart:
            QMessageBox.warning(self, "Sin productos", "No hay productos seleccionados")
            return
        items = []
        for pid, qty in self.cart.items():
            # Preferir datos frescos del stock para mantener precio correcto
            product = self.products_data.get(pid) or self.cart_products.get(pid)
            if product:
                price_val = product.get("precio_venta", None)
                if price_val in (None, ""):
                    price_val = product.get("precio_unitario", 0)
                items.append(
                    {
                        "producto_id": pid,
                        "cantidad": qty,
                        "precio_unitario": float(price_val or 0),
                        "nombre": product.get("nombre", ""),
                        "categoria": product.get("categoria", ""),
                        "material": product.get("material", ""),
                        "medida": product.get("medida", ""),
                        "estado": product.get("estado", ""),
                        "color": product.get("color", ""),
                        "fabricante": product.get("fabricante", ""),
                        "stock_local": product.get("local")
                        or product.get("stock_local")
                        or (
                            self.local_combo.currentData()
                            or self.local_combo.currentText()
                            .replace("\U0001F3EA ", "")
                            .replace("\U0001F4CD ", "")
                            if getattr(self, "local_combo", None)
                            else self.local
                        ),
                        "stock_actual": int(product.get("cantidad", 0) or 0),
                        "is_combo": int(product.get("is_combo") or 0),
                        "combo_id": int(product.get("combo_id") or pid)
                        if int(product.get("is_combo") or 0)
                        else None,
                        "combo_items": product.get("combo_items") or [],
                    }
                )
        self.products_selected.emit(items)
        self.accept()


class BoletaView(QMainWindow):
    def __init__(self, username: str, role: str, local: str, back_command=None):
        super().__init__()
        self.username = username
        self.role = role
        self.local = _clean_local_label(local or "")
        self.back_command = back_command

        self.cart_items = []
        self._last_total_base = None
        self._interes_monto = 0
        self._credito_personal_mode = False
        self._credito_personal_split_mode = False
        self._emit_thread = None

        self._setup_ui()

    def _setup_ui(self):
        # Forzar fullscreen sin barra de tareas (como Gestionar Stock)
        try:
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        except Exception:
            pass
        self.setWindowTitle("Emitir Boleta")
        self.resize(1500, 900)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.back_btn = QPushButton("← Volver")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setStyleSheet(
            "QPushButton{background:#34343a;color:#C9A040;border:1px solid #3e3e44;"
            "border-radius:10px;padding:8px 14px;font-weight:700;}"
            "QPushButton:hover{background:#3e3e44;}"
        )
        self.back_btn.setVisible(bool(self.back_command))
        if self.back_command:
            self.back_btn.clicked.connect(self.back_command)
        header.addWidget(self.back_btn)
        title = QLabel("Emitir Boleta")
        title.setFont(QFont("Segoe UI", 22, QFont.Black))
        title.setStyleSheet(f"color:{DORADO};")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(14)

        # Left column: cliente + productos
        left_col = QVBoxLayout()
        left_col.setSpacing(12)
        self._build_cliente_section(left_col)
        self._build_productos_section(left_col)
        content.addLayout(left_col, 3)

        # Right column: pago + totales
        right_col = QVBoxLayout()
        right_col.setSpacing(12)
        self._build_pago_section(right_col)
        self._build_totales_section(right_col)
        content.addLayout(right_col, 2)

        layout.addLayout(content)

        self._update_totales()
        try:
            QTimer.singleShot(0, self._force_fullscreen)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._force_fullscreen()
        except Exception:
            pass

    def _force_fullscreen(self):
        try:
            screen = None
            try:
                if self.windowHandle():
                    screen = self.windowHandle().screen()
            except Exception:
                screen = None
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.setGeometry(geo)
        except Exception:
            pass
        try:
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        except Exception:
            pass
        try:
            self.setWindowState(self.windowState() | Qt.WindowFullScreen)
        except Exception:
            pass
        try:
            self.showFullScreen()
        except Exception:
            try:
                self.showMaximized()
            except Exception:
                pass

    def set_back_command(self, callback):
        self.back_command = callback
        try:
            self.back_btn.clicked.disconnect()
        except Exception:
            pass
        if callable(callback):
            self.back_btn.clicked.connect(callback)
            self.back_btn.setVisible(True)
        else:
            self.back_btn.setVisible(False)

    def _card(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame{{background:{CARD};border:1px solid {BORDER};border-radius:14px;}}"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        lbl = QLabel(title)
        lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        lbl.setStyleSheet(f"color:{DORADO};")
        v.addWidget(lbl)
        return frame

    def _checkbox_style(self) -> str:
        return (
            "QCheckBox{color:#e5e7eb;spacing:8px;font-weight:600;}"
            "QCheckBox::indicator{width:18px;height:18px;border-radius:4px;"
            "border:2px solid #4b5563;background:#111827;}"
            "QCheckBox::indicator:hover{border-color:#facc15;}"
            "QCheckBox::indicator:checked{background:#facc15;border-color:#facc15;}"
        )

    def _pill_checkbox_style(self) -> str:
        return (
            "QCheckBox{color:#e5e7eb;font-weight:700;padding:6px 10px;"
            "border:1px solid #374151;border-radius:12px;background:#111827;}"
            "QCheckBox::indicator{width:0;height:0;}"
            "QCheckBox:checked{background:#fbbf24;color:#1a202c;border-color:#d97706;}"
        )

    def _combo_style(self) -> str:
        return (
            f"QComboBox{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
            "QComboBox::drop-down{border:0;}"
            f"QComboBox QAbstractItemView{{background:{CARD};color:{TEXT};"
            f"border:1px solid {BORDER};selection-background-color:#fbbf24;"
            "selection-color:#1a202c;outline:0;}}"
        )

    def _format_currency_text(self, raw_text: str) -> str:
        digits = "".join(ch for ch in (raw_text or "") if ch.isdigit())
        if not digits:
            return ""
        try:
            formatted = "{:,}".format(int(digits)).replace(",", ".")
        except Exception:
            formatted = digits
        return f"$ {formatted}"

    def _format_percent_text(self, raw_text: str) -> str:
        digits = "".join(ch for ch in (raw_text or "") if ch.isdigit())
        if not digits:
            return ""
        try:
            pct = int(digits)
        except Exception:
            pct = 0
        pct = max(0, min(100, pct))
        return f"{pct}%"

    def _parse_currency_value(self, raw_text: str) -> int:
        digits = "".join(ch for ch in (raw_text or "") if ch.isdigit())
        try:
            return int(digits) if digits else 0
        except Exception:
            return 0

    def _get_envio_value(self) -> int:
        raw = (self.envio_monto.text() or "") if hasattr(self, "envio_monto") else ""
        return self._parse_currency_value(raw)

    def _get_sena_value(self) -> int:
        raw = (self.sena_monto.text() or "") if hasattr(self, "sena_monto") else ""
        return self._parse_currency_value(raw)

    def _get_amount1_value(self) -> int:
        raw = (
            (self.amount1_input.text() or "") if hasattr(self, "amount1_input") else ""
        )
        return self._parse_currency_value(raw)

    def _get_amount2_value(self) -> int:
        raw = (
            (self.amount2_input.text() or "") if hasattr(self, "amount2_input") else ""
        )
        return self._parse_currency_value(raw)

    def _get_descuento_info(self):
        if not hasattr(self, "descuento_input"):
            return ("monto", 0)
        raw = (self.descuento_input.text() or "").strip()
        if not raw:
            return ("monto", 0)
        if "%" in raw:
            digits = "".join(ch for ch in raw if ch.isdigit())
            try:
                pct = int(digits) if digits else 0
            except Exception:
                pct = 0
            pct = max(0, min(100, pct))
            return ("porcentaje", pct)
        return ("monto", self._parse_currency_value(raw))

    def _get_total_con_envio(self) -> int:
        total_base = self._get_total_base()
        envio = (
            self._get_envio_value()
            if hasattr(self, "envio_cb") and self.envio_cb.isChecked()
            else 0
        )
        return int(total_base + envio)

    def _on_envio_text_changed(self):
        if not hasattr(self, "envio_monto"):
            return
        current = self.envio_monto.text() or ""
        formatted = self._format_currency_text(current)
        if formatted == current:
            return
        self.envio_monto.blockSignals(True)
        self.envio_monto.setText(formatted)
        self.envio_monto.setCursorPosition(len(formatted))
        self.envio_monto.blockSignals(False)
        self._on_values_changed()

    def _on_sena_text_changed(self):
        if not hasattr(self, "sena_monto"):
            return
        current = self.sena_monto.text() or ""
        formatted = self._format_currency_text(current)
        if formatted == current:
            return
        self.sena_monto.blockSignals(True)
        self.sena_monto.setText(formatted)
        self.sena_monto.setCursorPosition(len(formatted))
        self.sena_monto.blockSignals(False)
        self._on_values_changed()

    def _on_amount1_text_changed(self):
        if not hasattr(self, "amount1_input"):
            return
        current = self.amount1_input.text() or ""
        formatted = self._format_currency_text(current)
        if formatted == current:
            return
        self.amount1_input.blockSignals(True)
        self.amount1_input.setText(formatted)
        self.amount1_input.setCursorPosition(len(formatted))
        self.amount1_input.blockSignals(False)
        self._on_values_changed()

    def _on_amount2_text_changed(self):
        if not hasattr(self, "amount2_input"):
            return
        if self.amount2_input.isReadOnly():
            return
        current = self.amount2_input.text() or ""
        formatted = self._format_currency_text(current)
        if formatted == current:
            return
        self.amount2_input.blockSignals(True)
        self.amount2_input.setText(formatted)
        self.amount2_input.setCursorPosition(len(formatted))
        self.amount2_input.blockSignals(False)
        self._on_values_changed()

    def _set_money_text(self, widget, value: int):
        if not widget:
            return
        new_text = self._format_currency_text(str(max(0, int(value or 0))))
        if (widget.text() or "") == new_text:
            return
        widget.blockSignals(True)
        widget.setText(new_text)
        widget.setCursorPosition(len(widget.text()))
        widget.blockSignals(False)

    def _on_descuento_text_changed(self):
        if not hasattr(self, "descuento_input"):
            return
        current = self.descuento_input.text() or ""
        if "%" in current:
            formatted = self._format_percent_text(current)
        else:
            formatted = self._format_currency_text(current)
        if formatted == current:
            return
        self.descuento_input.blockSignals(True)
        self.descuento_input.setText(formatted)
        self.descuento_input.setCursorPosition(len(formatted))
        self.descuento_input.blockSignals(False)
        self._on_values_changed()

    def _build_cliente_section(self, parent_layout: QVBoxLayout):
        card = self._card("Datos del cliente")
        body = QGridLayout()
        body.setHorizontalSpacing(12)
        body.setVerticalSpacing(8)

        self.cliente_nombre = QLineEdit()
        self.cliente_tel = QLineEdit()
        self.cliente_calle = QLineEdit()
        self.cliente_numero = QLineEdit()
        self.cliente_localidad = QLineEdit()

        for w in [
            self.cliente_nombre,
            self.cliente_tel,
            self.cliente_calle,
            self.cliente_numero,
            self.cliente_localidad,
        ]:
            w.setStyleSheet(
                f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
                "border-radius:10px;padding:8px 10px;}}"
            )

        body.setColumnStretch(1, 1)
        body.setColumnStretch(3, 1)
        body.setColumnStretch(5, 1)

        body.addWidget(QLabel("Nombre y apellido"), 0, 0)
        body.addWidget(self.cliente_nombre, 0, 1)
        body.addWidget(QLabel("Telefono"), 0, 2)
        body.addWidget(self.cliente_tel, 0, 3)

        body.addWidget(QLabel("Calle"), 1, 0)
        body.addWidget(self.cliente_calle, 1, 1)
        body.addWidget(QLabel("Numero"), 1, 2)
        body.addWidget(self.cliente_numero, 1, 3)
        body.addWidget(QLabel("Localidad"), 1, 4)
        body.addWidget(self.cliente_localidad, 1, 5)

        card.layout().addLayout(body)

        envio_row = QHBoxLayout()
        self.envio_cb = QCheckBox("Con envio")
        self.envio_cb.setStyleSheet(self._pill_checkbox_style())
        self.envio_cb.setCursor(Qt.PointingHandCursor)
        self.envio_cb.toggled.connect(self._on_values_changed)
        self.envio_monto_label = QLabel("Monto envio")
        self.envio_monto_label.setStyleSheet("color:#d7dbe3;font-weight:600;")
        self.envio_monto = QLineEdit()
        self.envio_monto.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.envio_monto.textChanged.connect(self._on_envio_text_changed)
        self.entre_calles_input = QLineEdit()
        self.entre_calles_input.setPlaceholderText("Entre calles")
        self.entre_calles_input.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.entre_calles_input.textChanged.connect(self._on_values_changed)
        self.envio_monto_label.setVisible(False)
        self.envio_monto.setVisible(False)
        self.entre_calles_input.setEnabled(False)
        self.entre_calles_input.setVisible(False)
        envio_row.addWidget(self.envio_cb)
        envio_row.addSpacing(12)
        envio_row.addWidget(self.envio_monto_label)
        envio_row.addWidget(self.envio_monto)
        envio_row.addStretch()
        envio_row.addWidget(self.entre_calles_input)
        card.layout().addLayout(envio_row)

        self.envio_pago_row = QWidget()
        envio_pago_layout = QHBoxLayout(self.envio_pago_row)
        envio_pago_layout.setContentsMargins(0, 0, 0, 0)
        envio_pago_layout.setSpacing(8)
        self.envio_pago_info = QLabel("El envio siempre se cobra en el domicilio.")
        self.envio_pago_info.setStyleSheet("color:#d7dbe3;font-weight:700;")
        envio_pago_layout.addWidget(self.envio_pago_info)
        envio_pago_layout.addStretch()
        self.envio_pago_row.setVisible(False)
        card.layout().addWidget(self.envio_pago_row)

        self.entrega_programada_row = QWidget()
        entrega_programada_layout = QHBoxLayout(self.entrega_programada_row)
        entrega_programada_layout.setContentsMargins(0, 0, 0, 0)
        entrega_programada_layout.setSpacing(8)
        entrega_programada_layout.addWidget(QLabel("Entregar a partir de"))
        self.entrega_programada_cb = QComboBox()
        self.entrega_programada_cb.setStyleSheet(self._combo_style())
        self._refresh_entrega_programada_options()
        self.entrega_programada_cb.currentIndexChanged.connect(self._on_values_changed)
        entrega_programada_layout.addWidget(self.entrega_programada_cb)
        entrega_programada_layout.addStretch()
        self.entrega_programada_row.setVisible(False)
        card.layout().addWidget(self.entrega_programada_row)
        parent_layout.addWidget(card)

    def _build_productos_section(self, parent_layout: QVBoxLayout):
        card = self._card("Productos")
        header = QHBoxLayout()
        add_btn = QPushButton("Agregar productos")
        add_btn.setStyleSheet(
            f"QPushButton{{background:{DORADO};color:#171717;border:none;border-radius:10px;"
            "padding:8px 14px;font-weight:800;}}"
        )
        add_btn.clicked.connect(self._open_product_selection)
        clear_btn = QPushButton("Limpiar")
        clear_btn.clicked.connect(self._clear_cart)
        header.addWidget(add_btn)
        header.addWidget(clear_btn)
        header.addStretch()
        card.layout().addLayout(header)

        self.products_table = QTableWidget()
        self.products_table.setColumnCount(6)
        self.products_table.setHorizontalHeaderLabels(
            ["Producto", "Cant", "Precio", "Subtotal", "Local", "Quitar"]
        )
        self.products_table.setAlternatingRowColors(True)
        self.products_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.products_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.products_table.verticalHeader().setVisible(False)
        self.products_table.verticalHeader().setDefaultSectionSize(46)
        self.products_table.setStyleSheet(
            "QTableWidget{background:#1f1f22;color:#e5e7eb;alternate-background-color:#1b1b1f;"
            "border:1px solid #2a2a2e;border-radius:10px;}"
            "QHeaderView::section{background:#222226;color:#e5e7eb;font-weight:700;padding:8px;border:none;}"
        )
        header = self.products_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        card.layout().addWidget(self.products_table)

        parent_layout.addWidget(card)

    def _build_pago_section(self, parent_layout: QVBoxLayout):
        card = self._card("Pago")

        tipo_row = QHBoxLayout()
        tipo_row.addWidget(QLabel("Como paga"))
        self.pago_completo_cb = QCheckBox("Pago completo")
        self.pago_sena_cb = QCheckBox("Con sena")
        self.pago_domicilio_cb = QCheckBox("Pago en domicilio")
        for cb in [self.pago_completo_cb, self.pago_sena_cb, self.pago_domicilio_cb]:
            cb.setStyleSheet(self._pill_checkbox_style())
            cb.setCursor(Qt.PointingHandCursor)
        self.pago_group = QButtonGroup(self)
        self.pago_group.setExclusive(True)
        self.pago_group.addButton(self.pago_completo_cb)
        self.pago_group.addButton(self.pago_sena_cb)
        self.pago_group.addButton(self.pago_domicilio_cb)
        self.pago_completo_cb.setChecked(True)
        self.pago_completo_cb.toggled.connect(self._on_values_changed)
        self.pago_sena_cb.toggled.connect(self._on_values_changed)
        self.pago_domicilio_cb.toggled.connect(self._on_values_changed)
        tipo_row.addWidget(self.pago_completo_cb)
        tipo_row.addWidget(self.pago_sena_cb)
        tipo_row.addWidget(self.pago_domicilio_cb)
        tipo_row.addStretch()
        card.layout().addLayout(tipo_row)
        self.tipo_pago_row = tipo_row

        self.sena_row_widget = QWidget()
        sena_row = QHBoxLayout(self.sena_row_widget)
        sena_row.setContentsMargins(0, 0, 0, 0)
        sena_row.addWidget(QLabel("Monto sena"))
        self.sena_monto = QLineEdit()
        self.sena_monto.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.sena_monto.textChanged.connect(self._on_sena_text_changed)
        self.sena_monto.setEnabled(False)
        sena_row.addWidget(self.sena_monto)
        sena_row.addStretch()
        self.sena_row_widget.setVisible(False)
        card.layout().addWidget(self.sena_row_widget)

        metodo_row = QHBoxLayout()
        metodo_row.addWidget(QLabel("Metodo de pago"))
        self.method1_cb = QComboBox()
        self.method1_cb.addItem("")
        self.method1_cb.addItems(CheckoutDialog.PAYMENT_METHODS)
        try:
            self.method1_cb.setPlaceholderText("Seleccionar...")
        except Exception:
            pass
        self.method1_cb.setStyleSheet(self._combo_style())
        self.method1_cb.currentIndexChanged.connect(self._on_values_changed)
        metodo_row.addWidget(self.method1_cb)
        self.split_amount1_label = QLabel("Monto metodo 1")
        self.split_amount1_label.setVisible(False)
        metodo_row.addWidget(self.split_amount1_label)
        self.amount1_input = QLineEdit()
        self.amount1_input.setPlaceholderText("$ 0")
        self.amount1_input.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.amount1_input.textChanged.connect(self._on_amount1_text_changed)
        self.amount1_input.setVisible(False)
        metodo_row.addWidget(self.amount1_input)
        metodo_row.addStretch()
        card.layout().addLayout(metodo_row)
        self.metodo_row = metodo_row

        split_row = QHBoxLayout()
        self.split_cb = QCheckBox("Pago dividido")
        self.split_cb.setStyleSheet(self._pill_checkbox_style())
        self.split_cb.setCursor(Qt.PointingHandCursor)
        self.split_cb.toggled.connect(self._on_values_changed)
        split_row.addWidget(self.split_cb)
        self.credito_personal_btn = QPushButton("Credito personal")
        self.credito_personal_btn.setCheckable(True)
        self.credito_personal_btn.setStyleSheet(
            f"QPushButton{{background:{DARK};color:{DORADO};border:1px solid {DORADO};"
            "border-radius:10px;padding:6px 12px;font-weight:700;}}"
            f"QPushButton:checked{{background:{DORADO};color:#111827;}}"
        )
        self.credito_personal_btn.clicked.connect(self._on_credito_personal_toggled)
        split_row.addSpacing(8)
        split_row.addWidget(self.credito_personal_btn)
        split_row.addStretch()
        card.layout().addLayout(split_row)

        credito_split_row = QHBoxLayout()
        credito_split_row.setContentsMargins(0, 0, 0, 0)
        credito_split_row.addSpacing(120)
        self.credito_split_btn = QPushButton("Pago dividido")
        self.credito_split_btn.setCheckable(True)
        self.credito_split_btn.setVisible(False)
        self.credito_split_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{DORADO};border:1px solid {DORADO};"
            "border-radius:10px;padding:6px 12px;font-weight:800;}}"
            f"QPushButton:checked{{background:{DORADO};color:#111827;}}"
        )
        self.credito_split_btn.clicked.connect(self._on_credito_split_toggled)
        credito_split_row.addWidget(self.credito_split_btn)
        credito_split_row.addStretch()
        card.layout().addLayout(credito_split_row)

        self.split_block = QFrame()
        self.split_block.setStyleSheet(
            "QFrame{background:#1b1b1f;border:1px solid #2a2a2e;border-radius:12px;}"
        )
        split_layout = QVBoxLayout(self.split_block)
        split_layout.setContentsMargins(12, 10, 12, 10)
        split_layout.setSpacing(8)
        self.split_title_label = QLabel("Dividir el pago")
        self.split_title_label.setStyleSheet("color:#e5e7eb;font-weight:700;")
        self.split_help_label = QLabel(
            "Elegi hasta tres metodos distintos. "
            "Ingresas monto 1, monto 2 y el resto se asigna al ultimo metodo."
        )
        self.split_help_label.setStyleSheet("color:#9ca3af;")
        self.split_help_label.setWordWrap(True)
        split_layout.addWidget(self.split_title_label)
        split_layout.addWidget(self.split_help_label)

        split_modes_row = QHBoxLayout()
        split_modes_row.setSpacing(10)
        self.split_three_methods_cb = QCheckBox("Dividir en 3 metodos")
        self.split_three_methods_cb.setStyleSheet(self._pill_checkbox_style())
        self.split_three_methods_cb.setCursor(Qt.PointingHandCursor)
        self.split_three_methods_cb.toggled.connect(self._on_values_changed)
        split_modes_row.addWidget(self.split_three_methods_cb)
        split_modes_row.addStretch()
        split_layout.addLayout(split_modes_row)

        split_row2 = QHBoxLayout()
        split_row2.setSpacing(10)
        self.split_method2_label = QLabel("Metodo 2")
        split_row2.addWidget(self.split_method2_label)
        self.method2_cb = QComboBox()
        self.method2_cb.setStyleSheet(self._combo_style())
        self.method2_cb.currentIndexChanged.connect(self._on_values_changed)
        split_row2.addWidget(self.method2_cb)
        self.split_amount2_label = QLabel("Resto metodo 2")
        split_row2.addWidget(self.split_amount2_label)
        self.amount2_input = QLineEdit()
        self.amount2_input.setPlaceholderText("$ 0")
        self.amount2_input.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.amount2_input.textChanged.connect(self._on_amount2_text_changed)
        split_row2.addWidget(self.amount2_input)
        split_layout.addLayout(split_row2)

        self.split_row3_widget = QWidget()
        split_row3 = QHBoxLayout(self.split_row3_widget)
        split_row3.setContentsMargins(0, 0, 0, 0)
        split_row3.setSpacing(10)
        self.split_method3_label = QLabel("Metodo 3")
        split_row3.addWidget(self.split_method3_label)
        self.method3_cb = QComboBox()
        self.method3_cb.setStyleSheet(self._combo_style())
        self.method3_cb.currentIndexChanged.connect(self._on_values_changed)
        split_row3.addWidget(self.method3_cb)
        self.split_amount3_label = QLabel("Resto metodo 3")
        split_row3.addWidget(self.split_amount3_label)
        self.amount3_input = QLineEdit()
        self.amount3_input.setPlaceholderText("$ 0")
        self.amount3_input.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.amount3_input.setReadOnly(True)
        split_row3.addWidget(self.amount3_input)
        split_layout.addWidget(self.split_row3_widget)
        self.split_resto_label = QLabel("")
        self.split_resto_label.setStyleSheet("color:#9ca3af;font-weight:600;")
        self.split_resto_label.setVisible(False)
        split_layout.addWidget(self.split_resto_label)
        self.split_block.setVisible(False)
        card.layout().addWidget(self.split_block)

        # Tarjeta / interes
        self.cuotas_row_widget = QWidget()
        cuotas_row = QHBoxLayout(self.cuotas_row_widget)
        cuotas_row.setContentsMargins(0, 0, 0, 0)
        cuotas_row.addWidget(QLabel("Interes %"))
        self.tarjeta_interes_pct = QSpinBox()
        self.tarjeta_interes_pct.setRange(0, 100)
        self.tarjeta_interes_pct.setValue(0)
        self.tarjeta_interes_pct.setStyleSheet(
            f"QSpinBox{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.tarjeta_interes_pct.valueChanged.connect(self._on_values_changed)
        cuotas_row.addWidget(self.tarjeta_interes_pct)
        # Mantener campos legacy para compatibilidad interna (cuotas fijo)
        self.tarjeta_cuotas = QSpinBox()
        self.tarjeta_cuotas.setRange(1, 36)
        self.tarjeta_cuotas.setValue(1)
        self.cuotas_row_widget.setVisible(False)
        card.layout().addWidget(self.cuotas_row_widget)

        self.interes_info_label = QLabel("Interes: $0")
        self.interes_info_label.setStyleSheet("color:#cfd1d7;")
        card.layout().addWidget(self.interes_info_label)

        parent_layout.addWidget(card)
        self.pago_card = card

    def _build_totales_section(self, parent_layout: QVBoxLayout):
        card = self._card("Totales")
        self.desc_row_widget = QWidget()
        desc_row = QHBoxLayout(self.desc_row_widget)
        desc_row.setContentsMargins(0, 0, 0, 0)
        desc_row.addWidget(QLabel("Descuento"))
        self.descuento_input = QLineEdit()
        self.descuento_input.setPlaceholderText("Ej: $ 5.000 o 10%")
        self.descuento_input.setStyleSheet(
            f"QLineEdit{{background:{DARK};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:6px 10px;}}"
        )
        self.descuento_input.textChanged.connect(self._on_descuento_text_changed)
        desc_row.addWidget(self.descuento_input)
        card.layout().addWidget(self.desc_row_widget)
        self.subtotal_label = QLabel("Subtotal: $0")
        self.descuento_label = QLabel("Descuento: $0")
        self.interes_total_label = QLabel("Interes: $0")
        self.envio_label = QLabel("Envio: $0")
        self.total_label = QLabel("TOTAL: $0")

        for lbl in [
            self.subtotal_label,
            self.descuento_label,
            self.interes_total_label,
            self.envio_label,
        ]:
            lbl.setStyleSheet("color:#d7dbe3;font-weight:600;")
        self.total_label.setStyleSheet("color:#fef08a;font-weight:800;font-size:18px;")

        card.layout().addWidget(self.subtotal_label)
        card.layout().addWidget(self.descuento_label)
        card.layout().addWidget(self.interes_total_label)
        card.layout().addWidget(self.envio_label)
        card.layout().addWidget(self.total_label)
        self.credito_label = QLabel("CREDITO PERSONAL")
        self.credito_label.setStyleSheet(
            "color:#fbbf24;font-weight:900;font-size:18px;"
        )
        self.credito_label.setVisible(False)
        card.layout().addWidget(self.credito_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.emitir_btn = QPushButton("Emitir boleta")
        self.emitir_btn.setStyleSheet(
            f"QPushButton{{background:{DORADO};color:#171717;border:none;border-radius:12px;"
            "padding:10px 18px;font-weight:800;}}"
        )
        self.emitir_btn.clicked.connect(self._emit_boleta)
        btn_row.addWidget(self.emitir_btn)
        card.layout().addLayout(btn_row)

        parent_layout.addWidget(card)
        self.totales_card = card

    def _on_credito_personal_toggled(self):
        self._apply_credito_personal_mode(self.credito_personal_btn.isChecked())
        self._on_values_changed()

    def _on_credito_split_toggled(self):
        self._credito_personal_split_mode = bool(self.credito_split_btn.isChecked())
        self._on_values_changed()

    def _on_sena_toggled(self, checked: bool):
        pass

    def _on_domicilio_toggled(self, checked: bool):
        pass

    def _on_values_changed(self):
        credit_mode = bool(getattr(self, "_credito_personal_mode", False))
        if (
            hasattr(self, "credito_personal_btn")
            and self.credito_personal_btn.isChecked()
        ):
            credit_mode = True
            self._apply_credito_personal_mode(True)
        # Toggle envio fields
        if hasattr(self, "envio_cb"):
            envio_on = self.envio_cb.isChecked()
            if hasattr(self, "entrega_programada_row"):
                self._refresh_entrega_programada_options()
                self.entrega_programada_row.setVisible(envio_on)
            self.envio_monto.setVisible(envio_on)
            if hasattr(self, "envio_monto_label"):
                self.envio_monto_label.setVisible(envio_on)
            self.entre_calles_input.setEnabled(envio_on)
            self.entre_calles_input.setVisible(envio_on)
            if hasattr(self, "envio_pago_row"):
                self.envio_pago_row.setVisible(envio_on)
            if not envio_on:
                self.envio_monto.blockSignals(True)
                self.envio_monto.clear()
                self.envio_monto.blockSignals(False)
                if hasattr(self, "entrega_programada_cb"):
                    self.entrega_programada_cb.setCurrentIndex(0)

        split_allowed = bool(self.pago_completo_cb.isChecked()) and not credit_mode
        if (
            not split_allowed
            and hasattr(self, "split_cb")
            and self.split_cb.isChecked()
        ):
            self.split_cb.blockSignals(True)
            self.split_cb.setChecked(False)
            self.split_cb.blockSignals(False)
        if (
            not split_allowed
            and hasattr(self, "split_three_methods_cb")
            and self.split_three_methods_cb.isChecked()
        ):
            self.split_three_methods_cb.blockSignals(True)
            self.split_three_methods_cb.setChecked(False)
            self.split_three_methods_cb.blockSignals(False)

        split_on = (split_allowed and bool(self.split_cb.isChecked())) or (
            credit_mode and bool(getattr(self, "_credito_personal_split_mode", False))
        )
        if hasattr(self, "split_block"):
            self.split_block.setVisible(split_on)
            if credit_mode:
                self.split_title_label.setText("Pago dividido de credito personal")
                self.split_help_label.setText(
                    "Ingresa el metodo y el monto de la entrega inicial. El resto queda como credito personal."
                )
                self.split_method2_label.setText("Saldo")
                self.split_amount1_label.setText("Monto entrega inicial")
                if hasattr(self, "split_three_methods_cb"):
                    self.split_three_methods_cb.setVisible(False)
                if hasattr(self, "split_row3_widget"):
                    self.split_row3_widget.setVisible(False)
            else:
                self.split_title_label.setText("Dividir el pago")
                self.split_help_label.setText(
                    "Elegi hasta tres metodos distintos. Ingresas monto 1, monto 2 y el resto se asigna al ultimo metodo."
                )
                self.split_method2_label.setText("Metodo 2")
                self.split_amount1_label.setText("Monto metodo 1")
                if hasattr(self, "split_three_methods_cb"):
                    self.split_three_methods_cb.setVisible(True)
            split_three = bool(
                split_on and not credit_mode and self.split_three_methods_cb.isChecked()
            )
            self.split_amount1_label.setVisible(split_on)
            self.amount1_input.setVisible(split_on)
            if hasattr(self, "split_row3_widget"):
                self.split_row3_widget.setVisible(split_three)
            self._refresh_method_options()
            if hasattr(self, "split_resto_label"):
                if split_on:
                    total_split = self._get_total_con_envio()
                    amount1 = self._get_amount1_value()
                    if amount1 > total_split:
                        amount1 = total_split
                        self._set_money_text(self.amount1_input, total_split)
                    amount2 = self._get_amount2_value() if split_three else 0
                    remainder_after_1 = max(0, int(total_split) - int(amount1))
                    if split_three and amount2 > remainder_after_1:
                        amount2 = remainder_after_1
                        self._set_money_text(self.amount2_input, amount2)
                    remainder = max(0, remainder_after_1 - int(amount2))
                    if credit_mode:
                        self.split_amount2_label.setText("Saldo credito")
                        self.amount2_input.setReadOnly(True)
                        self._set_money_text(self.amount2_input, remainder)
                        self.split_resto_label.setText(
                            f"Saldo en credito personal: ${_fmt_money(remainder)}"
                        )
                    else:
                        if split_three:
                            self.split_amount2_label.setText("Monto metodo 2")
                            self.amount2_input.setReadOnly(False)
                            self.split_amount3_label.setText("Resto metodo 3")
                            self._set_money_text(self.amount3_input, remainder)
                        else:
                            self.split_amount2_label.setText("Resto metodo 2")
                            self.amount2_input.setReadOnly(True)
                            self._set_money_text(self.amount2_input, remainder)
                        resto_label = (
                            "Resto (metodo 3)" if split_three else "Resto (metodo 2)"
                        )
                        self.split_resto_label.setText(
                            f"{resto_label}: ${_fmt_money(remainder)}"
                        )
                    self.split_resto_label.setVisible(True)
                else:
                    if hasattr(self, "amount1_input"):
                        total_split = self._get_total_con_envio()
                        self._set_money_text(self.amount1_input, total_split)
                    if hasattr(self, "amount2_input"):
                        self.amount2_input.blockSignals(True)
                        self.amount2_input.clear()
                        self.amount2_input.blockSignals(False)
                    if hasattr(self, "amount3_input"):
                        self.amount3_input.blockSignals(True)
                        self.amount3_input.clear()
                        self.amount3_input.blockSignals(False)
                    self.split_amount1_label.setVisible(False)
                    self.amount1_input.setVisible(False)
                    self.split_resto_label.setVisible(False)

        if hasattr(self, "pago_sena_cb"):
            sena_on = self.pago_sena_cb.isChecked() and not credit_mode
            self.sena_row_widget.setVisible(sena_on)
            self.sena_monto.setEnabled(sena_on)
            if not sena_on:
                self.sena_monto.blockSignals(True)
                self.sena_monto.clear()
                self.sena_monto.blockSignals(False)

        if hasattr(self, "method1_cb"):
            metodo = self.method1_cb.currentText().strip()
            method2 = (
                "Credito personal"
                if (credit_mode and split_on)
                else (
                    self.method2_cb.currentText().strip()
                    if self.split_cb.isChecked()
                    else ""
                )
            )
            method3 = (
                self.method3_cb.currentText().strip()
                if (
                    self.split_cb.isChecked()
                    and hasattr(self, "split_three_methods_cb")
                    and self.split_three_methods_cb.isChecked()
                )
                else ""
            )
            show_interes = (
                _is_credit_card_method(metodo)
                or _is_credit_card_method(method2)
                or _is_credit_card_method(method3)
            )
            self.cuotas_row_widget.setVisible(show_interes)
            if not show_interes:
                self.tarjeta_interes_pct.blockSignals(True)
                self.tarjeta_interes_pct.setValue(0)
                self.tarjeta_interes_pct.blockSignals(False)
        if hasattr(self, "split_cb"):
            self.split_cb.setVisible(
                (not credit_mode) and bool(self.pago_completo_cb.isChecked())
            )
        split_fields_visible = bool(
            split_on and ((not self.pago_domicilio_cb.isChecked()) or credit_mode)
        )
        if hasattr(self, "split_amount1_label"):
            self.split_amount1_label.setVisible(split_fields_visible)
        if hasattr(self, "amount1_input"):
            self.amount1_input.setVisible(split_fields_visible)
        if hasattr(self, "metodo_row"):
            show_method_row = (not credit_mode) or bool(
                getattr(self, "_credito_personal_split_mode", False)
            )
            for i in range(self.metodo_row.count()):
                item = self.metodo_row.itemAt(i)
                w = item.widget() if item else None
                if w:
                    if w in (
                        getattr(self, "split_amount1_label", None),
                        getattr(self, "amount1_input", None),
                    ):
                        w.setVisible(show_method_row and split_fields_visible)
                    else:
                        w.setVisible(show_method_row)
        self._update_totales()
        self._invalidate_payment_if_needed()

    def _apply_credito_personal_mode(self, enabled: bool):
        self._credito_personal_mode = bool(enabled)
        if enabled:
            self.pago_completo_cb.setChecked(True)
            self.pago_sena_cb.setChecked(False)
            self.pago_domicilio_cb.setChecked(False)
            self.sena_monto.clear()
            self.descuento_input.clear()
        else:
            self._credito_personal_split_mode = False
            if hasattr(self, "credito_split_btn"):
                self.credito_split_btn.blockSignals(True)
                self.credito_split_btn.setChecked(False)
                self.credito_split_btn.blockSignals(False)
            self.method2_cb.setCurrentIndex(0)
            if hasattr(self, "method3_cb"):
                self.method3_cb.setCurrentIndex(0)
            self.amount1_input.clear()
            if hasattr(self, "amount2_input"):
                self.amount2_input.clear()
            if hasattr(self, "amount3_input"):
                self.amount3_input.clear()
        # Ocultar filas de pago incompatibles
        self.sena_row_widget.setVisible(False)
        self.sena_monto.setEnabled(False)
        self.interes_info_label.setVisible(True)
        split_credit = bool(
            enabled and getattr(self, "_credito_personal_split_mode", False)
        )
        for row in [self.tipo_pago_row]:
            try:
                for i in range(row.count()):
                    w = row.itemAt(i).widget()
                    if w:
                        w.setVisible(not enabled)
            except Exception:
                pass
        self.split_cb.setVisible(not enabled)
        if hasattr(self, "split_cb"):
            self.split_cb.setEnabled(not enabled)
        if hasattr(self, "credito_split_btn"):
            self.credito_split_btn.setVisible(enabled)
        for i in range(self.metodo_row.count()):
            item = self.metodo_row.itemAt(i)
            w = item.widget() if item else None
            if w:
                w.setVisible((not enabled) or split_credit)
        self.subtotal_label.setVisible(not enabled)
        self.descuento_label.setVisible(not enabled)
        self.interes_total_label.setVisible(not enabled)
        self.total_label.setVisible(not enabled)
        self.descuento_input.setEnabled(not enabled)
        if hasattr(self, "desc_row_widget"):
            self.desc_row_widget.setVisible(not enabled)
        self.credito_label.setVisible(enabled)
        self.envio_label.setVisible(True)
        self._update_totales()

    def _refresh_method_options(self):
        if not hasattr(self, "method2_cb"):
            return
        if getattr(self, "_credito_personal_mode", False):
            self.method2_cb.blockSignals(True)
            self.method2_cb.clear()
            self.method2_cb.addItem("Credito personal")
            self.method2_cb.setCurrentIndex(0)
            self.method2_cb.setEnabled(False)
            self.method2_cb.blockSignals(False)
            if hasattr(self, "method3_cb"):
                self.method3_cb.blockSignals(True)
                self.method3_cb.clear()
                self.method3_cb.setEnabled(False)
                self.method3_cb.blockSignals(False)
            return
        method1 = self.method1_cb.currentText()
        current2 = self.method2_cb.currentText()
        current3 = self.method3_cb.currentText() if hasattr(self, "method3_cb") else ""
        opts = [m for m in CheckoutDialog.PAYMENT_METHODS if m != method1]
        self.method2_cb.blockSignals(True)
        self.method2_cb.clear()
        self.method2_cb.addItem("")
        self.method2_cb.addItems(opts)
        try:
            self.method2_cb.setPlaceholderText("Seleccionar...")
        except Exception:
            pass
        if current2 in opts:
            self.method2_cb.setCurrentText(current2)
        self.method2_cb.setEnabled(True)
        self.method2_cb.blockSignals(False)
        if hasattr(self, "method3_cb"):
            method2 = self.method2_cb.currentText()
            opts3 = [
                m for m in CheckoutDialog.PAYMENT_METHODS if m not in {method1, method2}
            ]
            self.method3_cb.blockSignals(True)
            self.method3_cb.clear()
            self.method3_cb.addItem("")
            self.method3_cb.addItems(opts3)
            try:
                self.method3_cb.setPlaceholderText("Seleccionar...")
            except Exception:
                pass
            if current3 in opts3:
                self.method3_cb.setCurrentText(current3)
            split_three = bool(
                hasattr(self, "split_three_methods_cb")
                and self.split_three_methods_cb.isChecked()
            )
            self.method3_cb.setEnabled(split_three)
            self.method3_cb.blockSignals(False)

    def _refresh_entrega_programada_options(self):
        if not hasattr(self, "entrega_programada_cb"):
            return
        current_data = self.entrega_programada_cb.currentData()
        self.entrega_programada_cb.blockSignals(True)
        self.entrega_programada_cb.clear()
        for value, label in _build_delivery_day_options():
            self.entrega_programada_cb.addItem(label, value)
        if current_data:
            idx = self.entrega_programada_cb.findData(current_data)
            if idx >= 0:
                self.entrega_programada_cb.setCurrentIndex(idx)
            else:
                self.entrega_programada_cb.setCurrentIndex(0)
        else:
            self.entrega_programada_cb.setCurrentIndex(0)
        self.entrega_programada_cb.blockSignals(False)

    def _invalidate_payment_if_needed(self):
        total_base = self._get_total_base()
        if self._last_total_base is None:
            self._last_total_base = total_base
            return
        if int(total_base) != int(self._last_total_base):
            pass
        self._last_total_base = total_base

    def _get_total_base(self) -> int:
        subtotal = self._calc_subtotal()
        descuento = self._calc_descuento(subtotal)
        interes = self._calc_interes(subtotal - descuento)
        return int(max(0, subtotal - descuento + interes))

    def _calc_subtotal(self) -> int:
        subtotal = 0
        for it in self.cart_items:
            try:
                subtotal += int(it.get("cantidad") or 0) * int(
                    it.get("precio_unitario") or 0
                )
            except Exception:
                pass
        return int(subtotal)

    def _calc_descuento(self, subtotal: int) -> int:
        tipo, val = self._get_descuento_info()
        if tipo == "porcentaje" and val > 0:
            return int(subtotal * (val / 100.0))
        if tipo == "monto" and val > 0:
            return min(val, subtotal)
        return 0

    def _calc_interes(self, base: int) -> int:
        metodo = (
            self.method1_cb.currentText().strip().lower()
            if hasattr(self, "method1_cb")
            else ""
        )
        if getattr(self, "_credito_personal_mode", False):
            method2 = "credito personal"
            method3 = ""
        else:
            method2 = (
                self.method2_cb.currentText().strip().lower()
                if self.split_cb.isChecked()
                else ""
            )
            method3 = (
                self.method3_cb.currentText().strip().lower()
                if (
                    self.split_cb.isChecked()
                    and hasattr(self, "split_three_methods_cb")
                    and self.split_three_methods_cb.isChecked()
                )
                else ""
            )
        if (
            not _is_credit_card_method(metodo)
            and not _is_credit_card_method(method2)
            and not _is_credit_card_method(method3)
        ):
            return 0
        pct = int(self.tarjeta_interes_pct.value() or 0)
        if pct <= 0:
            return 0
        # El interes aplica solo a productos (no al envio)
        total_no_interes = int(max(0, base))
        # Si pago dividido, aplicar interes solo a la parte con tarjeta de credito
        split_on = self.split_cb.isChecked() or bool(
            getattr(self, "_credito_personal_split_mode", False)
        )
        if split_on:
            amount1 = int(self._get_amount1_value() or 0)
            amount1 = max(0, min(amount1, total_no_interes))
            split_three = bool(
                not getattr(self, "_credito_personal_mode", False)
                and hasattr(self, "split_three_methods_cb")
                and self.split_three_methods_cb.isChecked()
            )
            amount2 = int(self._get_amount2_value() or 0) if split_three else 0
            amount2 = max(0, min(amount2, max(0, total_no_interes - amount1)))
            amount3 = max(0, total_no_interes - amount1 - amount2)
            if _is_credit_card_method(metodo):
                credit_amount = amount1
            elif _is_credit_card_method(method2):
                credit_amount = (
                    amount2 if split_three else max(0, total_no_interes - amount1)
                )
            elif _is_credit_card_method(method3):
                credit_amount = amount3
            else:
                credit_amount = total_no_interes
        else:
            credit_amount = total_no_interes
        return int(max(0, credit_amount) * (pct / 100.0))

    def _update_totales(self):
        subtotal = self._calc_subtotal()
        descuento = self._calc_descuento(subtotal)
        interes = self._calc_interes(subtotal - descuento)
        self._interes_monto = interes

        total_base = max(0, subtotal - descuento + interes)
        envio = self._get_envio_value() if self.envio_cb.isChecked() else 0

        total = total_base
        self.subtotal_label.setText(f"Subtotal: ${_fmt_money(subtotal)}")
        self.descuento_label.setText(f"Descuento: ${_fmt_money(descuento)}")
        if hasattr(self, "interes_info_label"):
            self.interes_info_label.setText(f"Interes: ${_fmt_money(interes)}")
        if hasattr(self, "interes_total_label"):
            self.interes_total_label.setText(f"Interes: ${_fmt_money(interes)}")
        if getattr(self, "_credito_personal_mode", False):
            envio_extra = ""
            if envio > 0:
                envio_extra = " (aparte, se cobra en domicilio)"
            self.envio_label.setText(f"Envio aparte: ${_fmt_money(envio)}{envio_extra}")
        else:
            self.envio_label.setText(f"Envio: ${_fmt_money(envio)}")
        self.total_label.setText(f"TOTAL: ${_fmt_money(total)}")

    def _set_emit_busy(self, busy: bool):
        if hasattr(self, "emitir_btn"):
            self.emitir_btn.setEnabled(not busy)
            self.emitir_btn.setText("Registrando..." if busy else "Emitir boleta")
        try:
            self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)
        except Exception:
            pass

    def _on_emit_finished(self, ok: bool, msg: str, venta_id):
        self._set_emit_busy(False)
        self._emit_thread = None
        if not ok:
            QMessageBox.warning(self, "Boleta", f"No se pudo registrar la venta: {msg}")
            return

        resp = QMessageBox.question(self, "Boleta", "Venta registrada. ¿Generar PDF?")
        if resp == QMessageBox.Yes and venta_id:
            ok_pdf, path = vm.generar_pdf_boleta(int(venta_id))
            if ok_pdf:
                self._open_pdf(path)
            else:
                QMessageBox.warning(
                    self, "Boleta", f"No se pudo generar el PDF: {path}"
                )

        self._clear_form()

    def _open_product_selection(self):
        current_cart = list(self.cart_items or [])
        dlg = ProductSelectionDialog(self.local, self, current_cart)
        dlg.products_selected.connect(self._set_cart_items)
        dlg.exec_()

    def _set_cart_items(self, items: list):
        self.cart_items = items or []
        self._update_products_table()
        self._on_values_changed()

    def _clear_cart(self):
        self.cart_items = []
        self._update_products_table()
        self._on_values_changed()

    def _update_products_table(self):
        items = self.cart_items or []
        items_sorted = sorted(
            items,
            key=lambda it: (
                0 if (it.get("stock_local") or "") == self.local else 1,
                str(it.get("stock_local") or ""),
            ),
        )

        rows = []
        current_local = None
        for it in items_sorted:
            loc = (it.get("stock_local") or "").strip()
            if loc != current_local:
                rows.append({"type": "header", "local": loc})
                current_local = loc
            rows.append({"type": "item", "data": it})

        self.products_table.clearSpans()
        self.products_table.setRowCount(len(rows))
        col_count = self.products_table.columnCount()
        row_idx = 0
        for row in rows:
            if row["type"] == "header":
                local_txt = row["local"] or "Sin local"
                title = QTableWidgetItem(f"Stock: {local_txt}")
                title.setFlags(Qt.ItemIsEnabled)
                title.setBackground(QColor("#2c2c31"))
                title.setForeground(QColor(TEXT))
                self.products_table.setItem(row_idx, 0, title)
                self.products_table.setSpan(row_idx, 0, 1, col_count)
                row_idx += 1
                continue

            it = row["data"]
            name = it.get("nombre", "")
            cat = it.get("categoria", "")
            material = it.get("material", "")
            medida = it.get("medida", "")
            estado = it.get("estado", "")
            extra = " - ".join([p for p in [cat, material, medida, estado] if p])
            if extra:
                name = f"{name} ({extra})"

            qty = int(it.get("cantidad") or 0)
            precio = int(it.get("precio_unitario") or 0)
            subtotal = qty * precio
            stock_local = (it.get("stock_local") or "").strip()
            local_label = (
                f"\U0001F3EA {stock_local}"
                if stock_local == self.local
                else f"\U0001F4CD {stock_local}"
            )

            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(TEXT))
            try:
                name_item.setData(
                    Qt.UserRole, (int(it.get("producto_id") or 0), stock_local)
                )
            except Exception:
                name_item.setData(Qt.UserRole, (0, stock_local))
            qty_item = QTableWidgetItem(str(qty))
            price_item = QTableWidgetItem(f"${_fmt_money(precio)}")
            subtotal_item = QTableWidgetItem(f"${_fmt_money(subtotal)}")
            local_item = QTableWidgetItem(local_label)

            for item in [qty_item, price_item, subtotal_item, local_item]:
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(QColor(TEXT))

            self.products_table.setItem(row_idx, 0, name_item)
            self.products_table.setItem(row_idx, 1, qty_item)
            self.products_table.setItem(row_idx, 2, price_item)
            self.products_table.setItem(row_idx, 3, subtotal_item)
            self.products_table.setItem(row_idx, 4, local_item)

            btn = QPushButton("Quitar")
            btn.clicked.connect(lambda _, idx=row_idx: self._remove_product_row(idx))
            self.products_table.setCellWidget(row_idx, 5, btn)

            if stock_local and stock_local != self.local:
                for c in range(col_count):
                    item = self.products_table.item(row_idx, c)
                    if item:
                        item.setBackground(QColor("#3b2f2a"))
            else:
                for c in range(col_count):
                    item = self.products_table.item(row_idx, c)
                    if item:
                        item.setBackground(QColor("#1f1f22"))
            row_idx += 1

        self.products_table.resizeRowsToContents()

    def _remove_product_row(self, row: int):
        if row < 0 or row >= self.products_table.rowCount():
            return
        item = self.products_table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.UserRole)
        pid = None
        stock_local = ""
        try:
            if isinstance(data, tuple):
                pid = int(data[0])
                stock_local = str(data[1] or "")
        except Exception:
            pid = None
        name = item.text().strip()
        # Remove matching item by name
        new_items = []
        removed = False
        for it in self.cart_items:
            nm = it.get("nombre", "")
            if not removed:
                if (
                    pid is not None
                    and int(it.get("producto_id") or 0) == pid
                    and (it.get("stock_local") or "") == stock_local
                ):
                    removed = True
                    continue
                if pid is None and name.startswith(nm):
                    removed = True
                    continue
            new_items.append(it)
        self.cart_items = new_items
        self._update_products_table()
        self._on_values_changed()

    def _on_define_payment(self):
        pass

    def _update_payment_summary(self):
        pass

    def showEvent(self, event):
        super().showEvent(event)
        try:
            app = QApplication.instance()
            if app and app.property("manarey_kiosk"):
                if not self.isFullScreen():
                    self.showFullScreen()
        except Exception:
            pass

    def _emit_boleta(self):
        if not self.cart_items:
            QMessageBox.warning(self, "Boleta", "Agrega productos antes de emitir.")
            return
        nombre = (self.cliente_nombre.text() or "").strip()
        if not nombre:
            QMessageBox.warning(self, "Cliente", "Debe ingresar el nombre del cliente.")
            return
        telefono = (self.cliente_tel.text() or "").strip()
        calle = (self.cliente_calle.text() or "").strip()
        numero = (self.cliente_numero.text() or "").strip()
        localidad = (self.cliente_localidad.text() or "").strip()
        if not telefono or not calle or not numero or not localidad:
            QMessageBox.warning(
                self, "Cliente", "Completa todos los datos del cliente."
            )
            return
        if self.envio_cb.isChecked():
            if not (self.envio_monto.text() or "").strip():
                QMessageBox.warning(self, "Envio", "Indica el monto de envio.")
                return
            if not (self.entre_calles_input.text() or "").strip():
                QMessageBox.warning(self, "Envio", "Indica entre calles.")
                return

        subtotal = self._calc_subtotal()
        descuento = self._calc_descuento(subtotal)
        interes = self._interes_monto
        total_base = max(0, subtotal - descuento + interes)
        envio = self._get_envio_value() if self.envio_cb.isChecked() else 0
        total = total_base

        cliente = {
            "nombre": nombre,
            "telefono": telefono,
            "calle": calle,
            "numero": numero,
            "localidad": localidad,
            "entre_calles": (self.entre_calles_input.text() or "").strip()
            if self.envio_cb.isChecked()
            else "",
        }

        tipo_pago = "completo"
        monto_pagado = float(total)
        monto_pendiente = 0.0
        forma_pago = self.method1_cb.currentText()
        pagos = []
        notas = ""

        if getattr(self, "_credito_personal_mode", False):
            tipo_pago = "credito_personal"
            forma_pago = "Credito personal"
            if getattr(self, "_credito_personal_split_mode", False):
                monto1 = int(self._get_amount1_value() or 0)
                forma1 = (self.method1_cb.currentText() or "").strip()
                if not forma1:
                    QMessageBox.warning(
                        self,
                        "Credito personal",
                        "Selecciona el metodo del pago inicial.",
                    )
                    return
                if monto1 <= 0 or monto1 >= total:
                    QMessageBox.warning(
                        self,
                        "Credito personal",
                        "El pago inicial debe ser mayor a 0 y menor al total.",
                    )
                    return
                if _is_credit_card_method(forma1):
                    monto1 = int(monto1 + interes)
                monto2 = int(total) - int(monto1)
                monto_pagado = float(monto1)
                monto_pendiente = float(monto2)
                pagos = []
                if monto1 > 0:
                    pagos.append({"forma": forma1, "monto": float(monto1)})
                    forma_pago = f"{forma1} + Credito personal"
                if monto2 > 0:
                    pagos.append({"forma": "Credito personal", "monto": float(monto2)})
                notas = (
                    f"Pago dividido credito personal: {forma1}=${monto1}, Credito personal=${monto2}"
                    if monto1 > 0 and forma1
                    else f"Credito personal=${monto2}"
                )
            else:
                monto_pagado = 0.0
                monto_pendiente = float(total)
                pagos = [{"forma": "Credito personal", "monto": float(total)}]
                notas = f"Credito personal=${int(total)}"
        else:
            if not forma_pago:
                QMessageBox.warning(self, "Pago", "Selecciona un metodo de pago.")
                return
            if self.pago_domicilio_cb.isChecked():
                tipo_pago = "domicilio"
                monto_pagado = 0.0
                monto_pendiente = float(total)
            elif self.pago_sena_cb.isChecked():
                tipo_pago = "sena"
                sena_val = int(self._get_sena_value() or 0)
                if sena_val <= 0 or sena_val >= total:
                    QMessageBox.warning(
                        self, "Sena", "La sena debe ser mayor a 0 y menor al total."
                    )
                    return
                monto_pagado = float(sena_val)
                monto_pendiente = float(max(0, total - sena_val))

            if self.pago_completo_cb.isChecked() and self.split_cb.isChecked():
                split_three = bool(
                    hasattr(self, "split_three_methods_cb")
                    and self.split_three_methods_cb.isChecked()
                )
                method2 = self.method2_cb.currentText()
                if not method2:
                    QMessageBox.warning(
                        self, "Pago dividido", "Selecciona el segundo metodo."
                    )
                    return
                if method2 == forma_pago:
                    QMessageBox.warning(
                        self, "Pago dividido", "El segundo metodo debe ser distinto."
                    )
                    return
                monto1 = self._get_amount1_value()
                if monto1 <= 0 or monto1 >= total:
                    QMessageBox.warning(
                        self,
                        "Pago dividido",
                        "El monto 1 debe ser mayor a 0 y menor al total.",
                    )
                    return
                monto2_manual = self._get_amount2_value() if split_three else 0
                resto_base = int(total) - int(monto1)
                if split_three:
                    method3 = self.method3_cb.currentText()
                    if not method3:
                        QMessageBox.warning(
                            self, "Pago dividido", "Selecciona el tercer metodo."
                        )
                        return
                    if len({forma_pago, method2, method3}) != 3:
                        QMessageBox.warning(
                            self,
                            "Pago dividido",
                            "Los tres metodos deben ser distintos.",
                        )
                        return
                    if monto2_manual <= 0 or monto2_manual >= resto_base:
                        QMessageBox.warning(
                            self,
                            "Pago dividido",
                            "El monto 2 debe ser mayor a 0 y menor al resto.",
                        )
                        return
                if _is_credit_card_method(forma_pago):
                    monto1 = int(monto1 + interes)
                elif _is_credit_card_method(method2):
                    monto2_manual = (
                        int(monto2_manual + interes)
                        if split_three
                        else int(resto_base + interes)
                    )
                monto_resto = int(total) - int(monto1) - int(monto2_manual)
                if monto_resto < 0:
                    QMessageBox.warning(
                        self, "Pago dividido", "La distribucion de montos no es valida."
                    )
                    return
                if split_three:
                    pagos = [
                        {"forma": forma_pago, "monto": float(monto1)},
                        {"forma": method2, "monto": float(monto2_manual)},
                        {"forma": method3, "monto": float(monto_resto)},
                    ]
                    forma_pago = f"{forma_pago} + {method2} + {method3}"
                    notas = (
                        f"Pago dividido: {pagos[0]['forma']}=${monto1}, "
                        f"{pagos[1]['forma']}=${monto2_manual}, {pagos[2]['forma']}=${monto_resto}"
                    )
                else:
                    monto2 = int(total) - int(monto1)
                    pagos = [
                        {"forma": forma_pago, "monto": float(monto1)},
                        {"forma": method2, "monto": float(monto2)},
                    ]
                    forma_pago = f"{forma_pago} + {method2}"
                    notas = f"Pago dividido: {pagos[0]['forma']}=${monto1}, {pagos[1]['forma']}=${monto2}"
            else:
                pagos = [
                    {
                        "forma": forma_pago,
                        "monto": float(
                            total if tipo_pago == "completo" else monto_pagado
                        ),
                    }
                ]

        incluye_envio = 1 if self.envio_cb.isChecked() else 0
        precio_envio = float(self._get_envio_value()) if incluye_envio else 0.0
        entrega_programada = (
            self.entrega_programada_cb.currentData()
            if incluye_envio and hasattr(self, "entrega_programada_cb")
            else ""
        )
        forma_pago_envio = "domicilio" if incluye_envio else ""
        desc_tipo, desc_val = self._get_descuento_info()
        descuento_tipo = None
        if desc_val > 0 and not getattr(self, "_credito_personal_mode", False):
            descuento_tipo = "porcentaje" if desc_tipo == "porcentaje" else "monto"

        payload = {
            "local": self.local,
            "vendedor": self.username,
            "cliente_data": cliente,
            "items": self.cart_items,
            "descuento_tipo": descuento_tipo,
            "descuento_valor": float(desc_val or 0),
            "forma_pago": forma_pago or "Pendiente",
            "tipo_pago": tipo_pago,
            "monto_pagado": monto_pagado,
            "monto_pendiente": monto_pendiente,
            "incluye_envio": incluye_envio,
            "precio_envio": precio_envio,
            "forma_pago_envio": forma_pago_envio,
            "entrega_programada": entrega_programada,
            "notas": notas,
            "pagos": pagos,
            "tarjeta_cuotas": int(self.tarjeta_cuotas.value() or 1),
            "tarjeta_interes_pct": float(self.tarjeta_interes_pct.value() or 0),
            "tarjeta_interes_monto": float(interes or 0),
        }
        if self._emit_thread and self._emit_thread.isRunning():
            return
        self._set_emit_busy(True)
        self._emit_thread = VentaRegisterWorker(payload, self)
        self._emit_thread.finished_with_result.connect(self._on_emit_finished)
        self._emit_thread.start()

    def _open_pdf(self, filepath):
        try:
            if os.name == "nt":
                os.startfile(filepath)
            else:
                import subprocess

                subprocess.Popen(["xdg-open", filepath])
        except Exception as e:
            QMessageBox.warning(self, "PDF", f"No se pudo abrir el PDF: {e}")

    def _clear_form(self):
        self.cliente_nombre.clear()
        self.cliente_tel.clear()
        self.cliente_calle.clear()
        self.cliente_numero.clear()
        self.cliente_localidad.clear()
        self.cart_items = []
        if hasattr(self, "descuento_input"):
            self.descuento_input.clear()
        self.tarjeta_cuotas.setValue(1)
        self.tarjeta_interes_pct.setValue(0)
        self.pago_completo_cb.setChecked(True)
        self.pago_sena_cb.setChecked(False)
        self.pago_domicilio_cb.setChecked(False)
        if hasattr(self, "credito_personal_btn"):
            self.credito_personal_btn.setChecked(False)
            self._credito_personal_mode = False
        if hasattr(self, "credito_split_btn"):
            self.credito_split_btn.setChecked(False)
            self._credito_personal_split_mode = False
        self.sena_monto.clear()
        self.split_cb.setChecked(False)
        self.method1_cb.setCurrentIndex(0)
        if hasattr(self, "method2_cb"):
            self.method2_cb.setCurrentIndex(0)
        if hasattr(self, "method3_cb"):
            self.method3_cb.setCurrentIndex(0)
        if hasattr(self, "amount1_input"):
            self.amount1_input.clear()
        if hasattr(self, "amount2_input"):
            self.amount2_input.clear()
        if hasattr(self, "amount3_input"):
            self.amount3_input.clear()
        if hasattr(self, "split_three_methods_cb"):
            self.split_three_methods_cb.setChecked(False)
        self.envio_cb.setChecked(False)
        self.envio_monto.clear()
        self.entre_calles_input.clear()
        self._update_products_table()
        self._update_totales()
