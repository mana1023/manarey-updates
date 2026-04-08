import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import (
    QByteArray,
    QEvent,
    QRect,
    QRegularExpression,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIntValidator,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QRegularExpressionValidator,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRubberBand,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from models import db as db_mod
from models import offline_store
from models import stock_model as sm
from models import stock_model as sm_legacy
from models import stock_queue_api as qa
from models import ventas_model as vm
from models.firestore_db import get_all_locals, list_products_by_local
from styles import (
    BUTTON_PRIMARY,
    BUTTON_SECONDARY,
    INPUT_STYLE,
    MAIN_STYLE_SHEET,
    Colors,
    ThemeManager,
)
from utils.flow_layout import FlowContainer, FlowLayout
from utils.ui_scale import scale, scale_font
from workers.operation_queue import OperationQueue
from workers.stock_worker import StockWorker

logger = logging.getLogger(__name__)

# ==================== CONSTANTES Y CONFIGURACIÓN ====================

# Medidas por tipo
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
PULGADAS = [
    '32"',
    '40"',
    '43"',
    '50"',
    '55"',
    '60"',
    '65"',
]
LITROS = sm.ALLOWED_LITROS
MEDIDAS_ESTANDAR = sm.ALLOWED_MEDIDAS  # cm, m, kg, unidad, etc.

# Keywords para detección inteligente
CAMA_KEYWORDS = [
    "cama",
    "colchon",
    "colchones",
    "respaldo",
    "respaldos",
    "somier",
    "sommier",
    "acolchado",
    "sabana",
    "sabanas",
    "almohada",
    "alm ohadas",
    "funda",
    "fundas",
]
BICI_KEYWORDS = ["bicicleta", "bici", "bicis", "bicicl eta", " rodado"]

ESTADOS = sm.ESTADOS
LOCALES = ["Cane", "Vidriera", "Longchamps", "Glew"]  # respaldo si no cargan desde BD
LOW_STOCK_THRESHOLD = int(os.environ.get("MANAREY_LOW_STOCK_THRESHOLD", "3"))
_APPDATA = os.environ.get("APPDATA")
if _APPDATA:
    PREFS_DIR = Path(_APPDATA) / "Manarey"
else:
    PREFS_DIR = Path(os.path.expanduser("~")) / ".manarey_prefs"
PREFS_PATH = PREFS_DIR / "user_prefs.json"


def _ensure_prefs_dir():
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _dedupe(seq):
    seen = set()
    out = []
    for s in seq:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


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


def _set_combo_popup_width(combo, extra_px=40, min_width=None):
    """Ajusta el ancho del desplegable para evitar texto recortado."""
    try:
        view = combo.view()
        view.setTextElideMode(Qt.TextElideMode.ElideNone)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        fm = combo.fontMetrics()
        max_text = 0
        for i in range(combo.count()):
            max_text = max(max_text, fm.horizontalAdvance(combo.itemText(i)))
        popup_w = max_text + int(extra_px)
        if min_width is not None:
            popup_w = max(popup_w, int(min_width))
        view.setMinimumWidth(popup_w)
    except Exception:
        pass


_LITROS_RE = re.compile(r"^\d+(?:[.,]\d+)?l$", re.IGNORECASE)


class ElideDelegate(QStyledItemDelegate):
    """Dibuja texto con elipsis si no entra en la celda."""

    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is None:
            return super().paint(painter, option, index)
        painter.save()
        fm = option.fontMetrics
        elided = fm.elidedText(
            str(text), Qt.TextElideMode.ElideRight, option.rect.width() - 8
        )
        painter.setFont(option.font)
        painter.drawText(
            option.rect.adjusted(4, 0, -4, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )
        painter.restore()


class LoadingThread(QThread):
    """Hilo para carga de datos en background"""

    data_loaded = pyqtSignal(object, list)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        local,
        search="",
        categoria="",
        medidas=None,
        fabricante="",
        estado="",
        color="",
        codigo="",
        load_id=None,
        apply_reservas=False,
        parent=None,
    ):
        self._queue_need_full_reload = False
        self._NO_RELOAD_OPS = {"update_field", "change_state", "increment", "decrement"}

        super().__init__(parent)
        self.local = local
        self.search = search
        self.categoria = categoria
        self.medidas = medidas or []
        self.fabricante = fabricante or ""
        self.estado = estado or ""
        self.color = color or ""
        self.codigo = codigo or ""
        self.load_id = load_id
        self.apply_reservas = bool(apply_reservas)

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            rows = sm.get_stock_filtered(
                self.local,
                self.search,
                self.categoria,
                self.medidas,
                self.fabricante,
                self.estado,
                self.color,
                codigo=self.codigo,
                apply_reservas=self.apply_reservas,
            )
            if self.isInterruptionRequested():
                return
            self.data_loaded.emit(self.load_id, rows)
        except Exception as e:
            logger.error(f"Error cargando datos: {e}")
            try:
                self.error_occurred.emit(str(e))
            except Exception:
                pass
            self.data_loaded.emit(self.load_id, [])


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, ev):
        try:
            self.clicked.emit()
        except Exception:
            pass


class ComboSyncThread(QThread):
    done = pyqtSignal(str, bool)

    def __init__(self, local: str, parent=None):
        super().__init__(parent)
        self.local = local

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            sm.sync_all_products_across_locals()
            sm.sync_combos_for_local(self.local)
            self.done.emit(self.local, True)
        except Exception as e:
            logger.error(f"Error sync combos para {self.local}: {e}")
            self.done.emit(self.local, False)


class FilterOptionsThread(QThread):
    data_ready = pyqtSignal(object)

    def __init__(
        self, local: str, consolidated: bool, apply_reservas: bool, parent=None
    ):
        super().__init__(parent)
        self.local = local
        self.consolidated = consolidated
        self.apply_reservas = apply_reservas

    @staticmethod
    def _is_litros_medida(text: str) -> bool:
        return bool(_LITROS_RE.match(str(text or "").strip()))

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            if self.consolidated:
                locales_raw = get_all_locals() or []
                products = []
                if locales_raw:
                    for loc in locales_raw:
                        products.extend(list_products_by_local(loc))
                else:
                    products = list_products_by_local(None)
            else:
                products = []
                try:
                    rows = sm.list_by_local(self.local, "", "", "", "", "")
                    products = [sm._row_to_dict(r) for r in rows]
                except Exception:
                    products = sm.get_stock_filtered(
                        self.local,
                        "",
                        "",
                        [],
                        "",
                        "",
                        "",
                        apply_reservas=self.apply_reservas,
                    )
            if self.isInterruptionRequested():
                return

            # Precalcular opciones en el thread para no bloquear la UI
            try:
                categories = sorted(
                    {
                        (p.get("categoria") or "").strip().lower()
                        for p in products
                        if (p.get("categoria") or "").strip()
                    }
                )
                fabricantes = sorted(
                    {
                        (p.get("fabricante") or "").strip()
                        for p in products
                        if (p.get("fabricante") or "").strip()
                        and (p.get("fabricante") or "").strip() != "-"
                    }
                )
                estados = sorted(
                    {
                        (p.get("estado") or "").strip()
                        for p in products
                        if (p.get("estado") or "").strip()
                    }
                )
                colores = sorted(
                    {
                        (p.get("color") or "").strip()
                        for p in products
                        if (p.get("color") or "").strip()
                    }
                )
                medidas_vals = [
                    (p.get("medida") or "").strip()
                    for p in products
                    if (p.get("medida") or "").strip()
                ]
                plazas_present = sorted({m for m in medidas_vals if m in PLAZAS})
                rodados_present = sorted({m for m in medidas_vals if m in RODADOS})
                pulgadas_present = sorted({m for m in medidas_vals if m in PULGADAS})
                litros_present = sorted(
                    {m for m in medidas_vals if self._is_litros_medida(m)},
                    key=lambda value: float(
                        (value or "").lower().replace("l", "").replace(",", ".") or 0
                    ),
                )
                medidas_std = [m for m in MEDIDAS_ESTANDAR if m in medidas_vals]
                extras = sorted(
                    {
                        m
                        for m in medidas_vals
                        if m not in PLAZAS
                        and m not in RODADOS
                        and m not in PULGADAS
                        and not self._is_litros_medida(m)
                        and m not in MEDIDAS_ESTANDAR
                    }
                )
                if not medidas_std and not extras:
                    medidas_std = MEDIDAS_ESTANDAR
                else:
                    medidas_std = medidas_std + extras
                opciones = {
                    "categories": categories,
                    "fabricantes": fabricantes,
                    "estados": estados,
                    "colores": colores,
                    "plazas": plazas_present,
                    "rodados": rodados_present,
                    "pulgadas": pulgadas_present,
                    "litros": litros_present,
                    "medidas_std": medidas_std,
                }
                self.data_ready.emit(opciones)
            except Exception as e:
                logger.error(f"Error precalculando opciones de filtros: {e}")
                self.data_ready.emit(
                    {
                        "categories": [],
                        "plazas": [],
                        "rodados": [],
                        "pulgadas": [],
                        "litros": [],
                        "medidas_std": [],
                    }
                )
        except Exception as e:
            logger.error(f"Error en FilterOptionsThread: {e}")
            self.data_ready.emit(
                {
                    "categories": [],
                    "plazas": [],
                    "rodados": [],
                    "pulgadas": [],
                    "litros": [],
                    "medidas_std": [],
                }
            )


class ConsolidatedInventoryThread(QThread):
    data_ready = pyqtSignal(object, object, object, int)

    def __init__(self, parent, load_id: int):
        super().__init__(parent)
        self._load_id = int(load_id or 0)

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            try:
                sm.ensure_products_in_all_locals_once()
            except Exception:
                pass
            (
                headers,
                rows,
                products_by_id,
            ) = self.parent()._build_consolidated_inventory()
            if self.isInterruptionRequested():
                return
            self.data_ready.emit(headers, rows, products_by_id, self._load_id)
        except Exception as e:
            logger.error(f"Error en ConsolidatedInventoryThread: {e}")
            self.data_ready.emit([], [], {}, self._load_id)


class ReservasThread(QThread):
    data_ready = pyqtSignal(str, list, list, list)

    def __init__(self, local: str, parent=None):
        super().__init__(parent)
        self.local = local

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            senas = []
            envios = []
            interlocal = []
            try:
                vm.reconciliar_entregas_interlocales_pendientes(usuario="sistema")
            except Exception:
                logger.exception("No se pudo reconciliar interlocales pendientes")
            try:
                senas = sm.get_reservas_senas(self.local)
            except Exception:
                senas = []
            try:
                envios = sm.get_reservas_envios(self.local)
            except Exception:
                envios = []
            try:
                interlocal = sm.get_reservas_interlocal(self.local)
            except Exception:
                interlocal = []
            if self.isInterruptionRequested():
                return
            self.data_ready.emit(
                self.local, senas or [], envios or [], interlocal or []
            )
        except Exception as e:
            logger.error(f"Error en ReservasThread: {e}")
            self.data_ready.emit(self.local, [], [], [])


class QueueWorker(QThread):
    """Hilo que monitorea y procesa la cola de operaciones en background."""

    queue_count = pyqtSignal(int)
    processing = pyqtSignal(bool)

    def __init__(self, interval_ms: int = 5000, parent=None):
        super().__init__(parent)
        self.interval_ms = int(interval_ms)
        self._running = True
        self._processor = None

    def run(self):
        from models.queue_processor import QueueProcessor

        # Crear procesador una sola vez
        if not self._processor:
            self._processor = QueueProcessor(
                max_batch=10, max_retries=3, retry_delay=1.0
            )
            self._processor.start()

        # Loop principal
        while self._running:
            if self.isInterruptionRequested():
                self._running = False
                break
            try:
                cnt = qa.get_queue_count()
                self.queue_count.emit(int(cnt or 0))
                if cnt and cnt > 0:
                    self.processing.emit(True)
                    try:
                        # Intentar procesar una pasada (wrapper hace fallback a SQL si Firestore no está disponible)
                        qa.process_queue_once(limit=10)
                    except Exception as e:
                        logger.error(f"Error procesando queue en worker: {e}")
                    self.processing.emit(False)

                # Dormir en bloques pequeños para poder detener rápido
                slept = 0
                step = 200
                while self._running and slept < self.interval_ms:
                    self.msleep(step)
                    slept += step
            except Exception as e:
                logger.error(f"QueueWorker error: {e}")
                self.msleep(1000)

    def stop(self):
        self._running = False
        if self._processor:
            self._processor.stop()
            self._processor = None


# ==================== COMPONENTES MEJORADOS ====================


class SmartLineEdit(QLineEdit):
    """LineEdit con funciones avanzadas + estado inválido"""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setup_style()

    def setup_style(self):
        self.setStyleSheet(
            f"""
            QLineEdit {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.95), stop:1 rgba(255,255,255,0.85));
                color: #1A202C;
                border: 2px solid rgba(45,55,72,0.1);
                border-radius: 16px;
                padding: 14px 18px;
                font-size: 14px;
                font-weight: 600;
                box-shadow: 0 4px 6px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1);
            }}
            QLineEdit:focus {{
                border-color: {Colors.PRIMARY};
                background: rgba(255,255,255,1);
                box-shadow: 0 0 0 3px rgba(255,193,7,0.2), 0 4px 12px rgba(0,0,0,0.1);
            }}
            QLineEdit:hover {{
                background: rgba(255,255,255,1);
                border-color: rgba(45,55,72,0.2);
            }}
            QLineEdit::placeholder {{
                color: #718096;
                font-style: italic;
            }}
            /* Modo inválido */
            QLineEdit[invalid="true"] {{
                border: 2px solid #E53E3E;
                box-shadow: 0 0 0 3px rgba(229,62,62,0.15);
            }}
        """
        )


class ModernComboBox(QComboBox):
    """ComboBox con estilo mejorado + estado inválido"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_style()

    def setup_style(self):
        self.setStyleSheet(
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
                min-width: 120px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1);
            }}
            QComboBox:focus {{
                border-color: {Colors.PRIMARY};
                box-shadow: 0 0 0 3px rgba(255,193,7,0.2), 0 4px 12px rgba(0,0,0,0.1);
            }}
            QComboBox:hover {{
                background: rgba(255,255,255,1);
                border-color: rgba(45,55,72,0.2);
            }}
            QComboBox::drop-down {{
                border: none;
                width: 25px;
                padding-right: 10px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 6px solid transparent;
                border-right: 6px solid transparent;
                border-top: 6px solid #4A5568;
            }}
            QComboBox QAbstractItemView {{
                background: rgba(255,255,255,0.98);
                color: #1A202C;
                selection-background-color: {Colors.PRIMARY}33;
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                padding: 8px;
                font-size: 13px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.1), 0 6px 12px rgba(0,0,0,0.05);
            }}
            QComboBox QAbstractItemView::item {{
                padding: 10px 12px;
                border-radius: 6px;
                margin: 1px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {Colors.PRIMARY}22;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background: {Colors.PRIMARY}44;
                color: #1A202C;
            }}
            /* Modo inválido */
            QComboBox[invalid="true"] {{
                border: 2px solid #E53E3E;
                box-shadow: 0 0 0 3px rgba(229,62,62,0.15);
            }}
        """
        )


# ==================== DIÁLOGOS MODERNOS ====================


class ModernDialog(QDialog):
    """Base para diálogos con estilo moderno"""

    def __init__(self, title, width=400, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        try:
            self.setFixedWidth(scale(width))
        except Exception:
            self.setFixedWidth(width)
        self.setup_style()

    def setup_style(self):
        self.setStyleSheet(
            f"""
            QDialog {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {Colors.SURFACE}, stop:1 rgba(45,55,72,0.95));
                color: {Colors.TEXT_PRIMARY};
                border: 2px solid {Colors.BORDER_LIGHT};
                border-radius: 20px;
                box-shadow: 0 25px 50px rgba(0,0,0,0.25);
            }}
        """
        )


class PriceEditDialog(ModernDialog):
    """Diálogo para edición de precios con preview"""

    def __init__(self, current_price=0, parent=None):
        super().__init__("Editar Precio", 480, parent)
        self.current_price = current_price
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 20)
        layout.setSpacing(20)

        # Precio actual
        current_label = QLabel(
            f"Precio actual: ${self.format_price(self.current_price)}"
        )
        current_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 14px; font-weight: 600;"
        )
        layout.addWidget(current_label)

        # Input principal
        input_label = QLabel("Nuevo precio:")
        input_label.setStyleSheet(
            f"color: {Colors.PRIMARY}; font-weight: 700; margin-bottom: 5px;"
        )
        layout.addWidget(input_label)

        self.price_input = SmartLineEdit("Ej: 15000, +20%, -1000, +15%")
        self.price_input.textChanged.connect(self.update_preview)
        layout.addWidget(self.price_input)

        # Preview del resultado
        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet(
            f"""
            background: {Colors.BACKGROUND};
            color: {Colors.SUCCESS};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 12px;
            font-size: 16px;
            font-weight: 700;
        """
        )
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_label)

        # Ejemplos de uso
        examples = QLabel(
            """
        <b>Ejemplos de uso:</b><br>
        • <code>25000</code> - Precio exacto<br>
        • <code>+20%</code> - Aumentar 20%<br>
        • <code>-15%</code> - Reducir 15%<br>
        • <code>+2000</code> - Sumar $2000<br>
        • <code>-500</code> - Restar $500
        """
        )
        examples.setStyleSheet(
            f"""
            color: {Colors.TEXT_MUTED};
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 12px;
            font-size: 11px;
        """
        )
        layout.addWidget(examples)

        # Botones
        buttons = QHBoxLayout()

        self.ok_btn = QPushButton("Aplicar")
        ThemeManager.apply_primary_button(self.ok_btn)
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setEnabled(False)

        cancel_btn = QPushButton("Cancelar")
        ThemeManager.apply_secondary_button(cancel_btn)
        cancel_btn.clicked.connect(self.reject)

        buttons.addWidget(self.ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        self.price_input.setFocus()
        # Forzar flush para ver cambios inmediatamente al entrar
        try:
            sm.flush_notifications_for_local(self.local, force=True)
        except Exception:
            pass

    def update_preview(self):
        """Actualiza el preview del precio calculado"""
        text = self.price_input.text().strip()
        if not text:
            self.preview_label.setText("")
            self.ok_btn.setEnabled(False)
            return

        try:
            new_price = sm._compute_new_price_from_str(self.current_price, text)
            if new_price != self.current_price:
                change = new_price - self.current_price
                change_text = (
                    f" ({'+' if change > 0 else ''}{self.format_price(change)})"
                )
                self.preview_label.setText(
                    f"Nuevo precio: ${self.format_price(new_price)}{change_text}"
                )
                self.ok_btn.setEnabled(True)
            else:
                self.preview_label.setText("Sin cambios")
                self.ok_btn.setEnabled(True)
        except:
            self.preview_label.setText("❌ Formato inválido")
            self.ok_btn.setEnabled(False)

    def format_price(self, price):
        """Formatea precio con separadores de miles"""
        return "{:,}".format(int(price)).replace(",", ".")

    def get_value(self):
        return self.price_input.text().strip()


class StockAdjustDialog(ModernDialog):
    """Dialogo para ajustar stock (incremento o decremento)."""

    def __init__(self, product, mode="increment", parent=None):
        self.product = product or {}
        self.mode = mode if mode in ("increment", "decrement") else "increment"
        self.current_qty = int(self.product.get("cantidad", 0) or 0)
        self.min_qty = 0 if self.mode == "increment" else 1
        self.max_qty = self.current_qty if self.mode == "decrement" else 9999
        if self.max_qty < self.min_qty:
            self.max_qty = self.min_qty
        default_qty = 0 if self.mode == "increment" else 1
        self.quantity = min(default_qty, self.max_qty)

        title = "Incrementar Stock" if self.mode == "increment" else "Reducir Stock"
        super().__init__(title, 560, parent)
        self._needs_reason = self.mode == "decrement"
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 20)
        layout.setSpacing(16)

        name = (self.product.get("nombre") or "Producto").strip()
        header = QLabel(name)
        header.setStyleSheet(
            f"""
            color: {Colors.PRIMARY};
            font-size: 18px;
            font-weight: 900;
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 10px;
            padding: 14px;
        """
        )
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        info_frame = QFrame()
        info_frame.setStyleSheet(
            f"""
            QFrame {{
                background: {Colors.SURFACE_LIGHT};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """
        )
        info_layout = QGridLayout(info_frame)
        info_layout.setContentsMargins(12, 12, 12, 12)
        info_layout.setHorizontalSpacing(12)
        info_layout.setVerticalSpacing(8)

        info_items = [
            ("Categoria", self.product.get("categoria")),
            ("Fabricante", self.product.get("fabricante")),
            ("Medida", self.product.get("medida")),
            ("Estado", self.product.get("estado")),
            ("Precio", self._format_price(self.product.get("precio_venta"))),
            ("Stock actual", str(self.current_qty)),
            ("Local", self.product.get("local")),
        ]

        row = 0
        col = 0
        for label, value in info_items:
            item = self._make_info_item(label, self._safe_text(value))
            info_layout.addWidget(item, row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        layout.addWidget(info_frame)

        qty_label = QLabel(
            "Cantidad a sumar:" if self.mode == "increment" else "Cantidad a restar:"
        )
        qty_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: 700;")
        layout.addWidget(qty_label)

        qty_row = QHBoxLayout()
        qty_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qty_row.setSpacing(12)

        minus_btn = QPushButton("-")
        try:
            minus_btn.setFixedSize(scale(42), scale(42))
        except Exception:
            minus_btn.setFixedSize(42, 42)
        minus_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.SURFACE_LIGHT};
                color: {Colors.PRIMARY};
                border: 2px solid {Colors.BORDER};
                border-radius: 21px;
                font-weight: 900;
                font-size: 18px;
            }}
            QPushButton:hover {{
                background: {Colors.OVERLAY};
                border-color: {Colors.PRIMARY};
            }}
        """
        )
        minus_btn.clicked.connect(self._decrease_qty)

        self.qty_edit = QLineEdit(str(self.quantity))
        try:
            self.qty_edit.setFixedWidth(scale(120))
        except Exception:
            self.qty_edit.setFixedWidth(120)
        self.qty_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qty_edit.setValidator(
            QIntValidator(self.min_qty, self.max_qty, self.qty_edit)
        )
        self.qty_edit.setStyleSheet(
            f"""
            QLineEdit {{
                background: {Colors.PRIMARY};
                color: #1A202C;
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 18px;
                font-weight: 900;
            }}
        """
        )
        self.qty_edit.textChanged.connect(self._on_qty_changed)
        self.qty_edit.editingFinished.connect(self._coerce_qty)

        plus_btn = QPushButton("+")
        try:
            plus_btn.setFixedSize(scale(42), scale(42))
        except Exception:
            plus_btn.setFixedSize(42, 42)
        plus_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.PRIMARY};
                color: #1A202C;
                border: none;
                border-radius: 21px;
                font-weight: 900;
                font-size: 18px;
            }}
            QPushButton:hover {{ background: {Colors.PRIMARY_LIGHT}; }}
        """
        )
        plus_btn.clicked.connect(self._increase_qty)

        qty_row.addWidget(minus_btn)
        qty_row.addWidget(self.qty_edit)
        qty_row.addWidget(plus_btn)
        layout.addLayout(qty_row)

        hint = QLabel("Podes escribir o usar + / -")
        hint.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-weight: 700;"
        )
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_label)
        self._on_qty_changed(self.qty_edit.text())

        if self._needs_reason:
            reason_label = QLabel("Motivo (obligatorio):")
            reason_label.setStyleSheet(
                f"color: {Colors.TEXT_PRIMARY}; font-weight: 700;"
            )
            layout.addWidget(reason_label)

            self.reason_edit = SmartLineEdit("Ej: rotura, devolucion")
            layout.addWidget(self.reason_edit)
        else:
            self.reason_edit = None

        buttons = QHBoxLayout()
        action_text = (
            "Confirmar incremento" if self.mode == "increment" else "Confirmar baja"
        )
        self.ok_btn = QPushButton(action_text)
        if self.mode == "increment":
            ThemeManager.apply_primary_button(self.ok_btn)
        else:
            self.ok_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {Colors.WARNING};
                    color: white;
                    border: none;
                    border-radius: 10px;
                    padding: 12px 24px;
                    font-weight: 700;
                    font-size: 14px;
                }}
                QPushButton:hover {{ background: #c0841a; }}
            """
            )
        self.ok_btn.clicked.connect(self._on_accept)

        cancel_btn = QPushButton("Cancelar")
        ThemeManager.apply_secondary_button(cancel_btn)
        cancel_btn.clicked.connect(self.reject)

        buttons.addWidget(self.ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        self.qty_edit.setFocus()

    def _safe_text(self, value):
        if value is None:
            return "-"
        text = str(value).strip()
        return text if text else "-"

    def _format_price(self, value):
        try:
            val = int(float(value))
            return f"${val:,.0f}".replace(",", ".")
        except Exception:
            return self._safe_text(value)

    def _make_info_item(self, label_text, value_text):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        label = QLabel(label_text)
        label.setStyleSheet(
            f"color: {Colors.TEXT_MUTED}; font-size: 11px; font-weight: 700;"
        )
        value = QLabel(value_text)
        value.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: 13px; font-weight: 800;"
        )
        value.setWordWrap(True)
        lay.addWidget(label)
        lay.addWidget(value)
        return box

    def _read_qty(self):
        text = self.qty_edit.text().strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _preview_text(self, qty):
        if self.mode == "increment":
            return f"Nuevo stock: {self.current_qty + qty}"
        remaining = self.current_qty - qty
        if remaining < 0:
            remaining = 0
        return f"Stock restante: {remaining}"

    def _on_qty_changed(self, _text):
        qty = self._read_qty()
        if qty is None:
            self.preview_label.setText("")
            return
        qty = max(self.min_qty, min(self.max_qty, qty))
        self.quantity = qty
        self.preview_label.setText(self._preview_text(qty))

    def _coerce_qty(self):
        qty = self._read_qty()
        if qty is None:
            qty = self.min_qty
        qty = max(self.min_qty, min(self.max_qty, qty))
        self.quantity = qty
        if self.qty_edit.text().strip() != str(qty):
            self.qty_edit.setText(str(qty))

    def _increase_qty(self):
        self._coerce_qty()
        if self.quantity < self.max_qty:
            self.quantity += 1
            self.qty_edit.setText(str(self.quantity))

    def _decrease_qty(self):
        self._coerce_qty()
        if self.quantity > self.min_qty:
            self.quantity -= 1
            self.qty_edit.setText(str(self.quantity))

    def _on_accept(self):
        qty = self._read_qty()
        if qty is None or qty <= 0:
            QMessageBox.warning(
                self, "Cantidad invalida", "Ingresa una cantidad mayor a 0."
            )
            return
        qty = max(self.min_qty, min(self.max_qty, qty))
        self.quantity = qty
        if self.qty_edit.text().strip() != str(qty):
            self.qty_edit.setText(str(qty))
        if self._needs_reason:
            motivo = self.reason_edit.text().strip() if self.reason_edit else ""
            if not motivo:
                QMessageBox.warning(
                    self, "Falta motivo", "Tenes que escribir un motivo."
                )
                return
        self.accept()

    def get_quantity(self):
        return int(self.quantity or 0)

    def get_motivo(self):
        if not self._needs_reason or not self.reason_edit:
            return ""
        return self.reason_edit.text().strip()


class StockDecreaseDialog(ModernDialog):
    """Diálogo para reducir stock con motivo escrito por el usuario"""

    def __init__(self, product_name, current_qty, parent=None):
        super().__init__("Reducir Stock", 450, parent)
        self.product_name = product_name
        self.current_qty = int(current_qty or 0)
        self.quantity = 1
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 20)
        layout.setSpacing(20)

        # Header con información del producto
        header = QLabel(f"📦 {self.product_name}")
        header.setStyleSheet(
            f"""
            color: {Colors.PRIMARY};
            font-size: 18px;
            font-weight: 900;
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 10px;
            padding: 15px;
        """
        )
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Cantidad disponible
        available_label = QLabel(f"Cantidad disponible: {self.current_qty}")
        available_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 14px; font-weight: 600;"
        )
        available_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(available_label)

        # Selector de cantidad a reducir (+ / −)
        quantity_layout = QHBoxLayout()
        quantity_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quantity_layout.setSpacing(15)

        minus_btn = QPushButton("−")
        minus_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.SURFACE_LIGHT};
                color: {Colors.PRIMARY};
                border: 2px solid {Colors.BORDER};
                border-radius: 20px;       /* redondeado */
                width: 40px; height: 40px;
                font-size: 18px; font-weight: 900;
            }}
            QPushButton:hover {{
                background: {Colors.OVERLAY};
                border-color: {Colors.PRIMARY};
            }}
        """
        )
        minus_btn.clicked.connect(self.decrease_quantity)

        self.quantity_label = QLabel(str(self.quantity))
        self.quantity_label.setStyleSheet(
            f"""
            color: {Colors.TEXT_PRIMARY};
            font-size: 20px; font-weight: 900;
            min-width: 50px;
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 8px;
        """
        )
        self.quantity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        plus_btn = QPushButton("+")
        plus_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.WARNING};
                color: white;
                border: none;
                border-radius: 20px;       /* redondeado */
                width: 40px; height: 40px;
                font-size: 18px; font-weight: 900;
            }}
            QPushButton:hover {{ background: #e69500; }}
        """
        )
        plus_btn.clicked.connect(self.increase_quantity)

        quantity_layout.addWidget(minus_btn)
        quantity_layout.addWidget(self.quantity_label)
        quantity_layout.addWidget(plus_btn)
        layout.addLayout(quantity_layout)

        # Motivo (obligatorio, tipeado por el usuario)
        motivo_label = QLabel("Motivo de la baja (obligatorio):")
        motivo_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-weight: 700; margin-top: 10px;"
        )
        layout.addWidget(motivo_label)

        self.motivo_input = SmartLineEdit("Escribí el motivo…")
        layout.addWidget(self.motivo_input)

        # Botones de acción
        buttons = QHBoxLayout()
        confirm_btn = QPushButton("✓ Confirmar Baja")
        confirm_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.WARNING};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 12px 24px;
                font-weight: 700; font-size: 14px;
            }}
            QPushButton:hover {{ background: #e69500; }}
        """
        )
        confirm_btn.clicked.connect(self._on_accept)

        cancel_btn = QPushButton("Cancelar")
        ThemeManager.apply_secondary_button(cancel_btn)
        cancel_btn.clicked.connect(self.reject)

        buttons.addWidget(confirm_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    # --- lógica ---
    def increase_quantity(self):
        if self.quantity < self.current_qty:
            self.quantity += 1
            self.quantity_label.setText(str(self.quantity))

    def decrease_quantity(self):
        if self.quantity > 1:
            self.quantity -= 1
            self.quantity_label.setText(str(self.quantity))

    def _on_accept(self):
        if not self.motivo_input.text().strip():
            from PyQt5.QtWidgets import QMessageBox

            QMessageBox.warning(
                self, "Falta motivo", "Tenés que escribir un motivo para la baja."
            )
            return
        self.accept()

    def get_result(self):
        motivo = self.motivo_input.text().strip()
        # mantenemos la firma (qty, motivo, detalle) para no tocar el resto del código
        return self.quantity, motivo, ""


class StateChangeDialog(ModernDialog):
    def __init__(self, max_qty, current_state, current_price, parent=None):
        super().__init__("Cambiar estado", 480, parent)
        self.max_qty = int(max_qty)
        self.current_state = current_state or "Nuevo"
        self.current_price = int(current_price or 0)
        self.qty = 1
        self.setup_ui()

    def setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 22, 30, 20)
        lay.setSpacing(16)

        # Cantidad con +/- (igual estilo que transferir)
        row_qty = QHBoxLayout()
        row_qty.setSpacing(12)
        row_qty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        minus = QPushButton("−")
        try:
            minus.setFixedSize(scale(40), scale(40))
        except Exception:
            minus.setFixedSize(40, 40)
        minus.setStyleSheet(
            "QPushButton{background:#1f2937;color:#ffc107;border:2px solid #334155;border-radius:20px;font:900 18px 'Segoe UI'}"
        )
        minus.clicked.connect(self.dec_qty)

        self.qty_label = QLabel(str(self.qty))
        self.qty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qty_label.setStyleSheet(
            "QLabel{color:white;background:#0b1220;border:1px solid #334155;border-radius:8px;padding:8px 16px;font:900 18px 'Segoe UI';min-width:60px}"
        )

        plus = QPushButton("+")
        try:
            plus.setFixedSize(scale(40), scale(40))
        except Exception:
            plus.setFixedSize(40, 40)
        plus.setStyleSheet(
            "QPushButton{background:#ffc107;color:#111827;border:none;border-radius:20px;font:900 18px 'Segoe UI'}"
        )
        plus.clicked.connect(self.inc_qty)

        row_qty.addWidget(minus)
        row_qty.addWidget(self.qty_label)
        row_qty.addWidget(plus)
        lay.addLayout(row_qty)

        # Estado
        lbl_state = QLabel("Nuevo estado:")
        lbl_state.setStyleSheet("color:#ffc107;font-weight:700")
        lay.addWidget(lbl_state)
        self.state_combo = ModernComboBox()
        self.state_combo.addItems(ESTADOS)
        self.state_combo.setCurrentText(self.current_state)
        self.state_combo.currentTextChanged.connect(self._on_state_changed)
        lay.addWidget(self.state_combo)

        # Motivo (solo si Reacondicionado)
        self.reason_label = QLabel("Motivo (requerido para Reacondicionado):")
        self.reason_label.setStyleSheet("color:#e5e7eb;font-weight:600")
        self.reason_edit = SmartLineEdit("Escribí el motivo…")
        self.reason_label.setVisible(False)
        self.reason_edit.setVisible(False)
        lay.addWidget(self.reason_label)
        lay.addWidget(self.reason_edit)

        # Precio nuevo (opcional)
        lbl_price = QLabel(f"Nuevo precio (opcional, actual: ${self.current_price:,})")
        lbl_price.setStyleSheet("color:#e5e7eb;font-weight:600")
        lay.addWidget(lbl_price)
        self.price_edit = SmartLineEdit("Ej: 25000, +10%, -500…")
        lay.addWidget(self.price_edit)

        # Botones
        row_btn = QHBoxLayout()
        ok = QPushButton("Aplicar")
        ThemeManager.apply_primary_button(ok)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancelar")
        ThemeManager.apply_secondary_button(cancel)
        cancel.clicked.connect(self.reject)
        row_btn.addWidget(ok)
        row_btn.addWidget(cancel)
        lay.addLayout(row_btn)

        self._on_state_changed(self.state_combo.currentText())

    def inc_qty(self):
        if self.qty < self.max_qty:
            self.qty += 1
            self.qty_label.setText(str(self.qty))

    def dec_qty(self):
        if self.qty > 1:
            self.qty -= 1
            self.qty_label.setText(str(self.qty))

    def _on_state_changed(self, text):
        needs_reason = text.strip().lower() == "reacondicionado"
        self.reason_label.setVisible(needs_reason)
        self.reason_edit.setVisible(needs_reason)

    def values(self):
        # precio: parse con la misma función del modelo
        txt = self.price_edit.text().strip()
        new_price = None
        if txt:
            try:
                new_price = sm._compute_new_price_from_str(self.current_price, txt)
            except Exception:
                new_price = None

        # SIEMPRE leemos el motivo; la validación se hace afuera según estado
        motivo = (self.reason_edit.text() or "").strip()

        return self.qty, self.state_combo.currentText(), new_price, motivo


class BulkPriceDialog(ModernDialog):
    """Diálogo para cambio masivo de precios"""

    def __init__(self, product_count, parent=None):
        super().__init__(f"Cambio Masivo - {product_count} productos", 500, parent)
        self.product_count = product_count
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 20)
        layout.setSpacing(20)

        # Información
        info_label = QLabel(
            f"🔄 Aplicar cambio de precio a {self.product_count} productos seleccionados"
        )
        info_label.setStyleSheet(
            f"""
            color: {Colors.PRIMARY};
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 12px;
            font-weight: 700;
            font-size: 14px;
        """
        )
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)

        # Input
        input_label = QLabel("Cambio de precio:")
        input_label.setStyleSheet(
            f"color: {Colors.PRIMARY}; font-weight: 700; margin-bottom: 5px;"
        )
        layout.addWidget(input_label)

        self.price_input = SmartLineEdit("Ej: +20%, -15%, +1000, 25000")
        layout.addWidget(self.price_input)

        # Botones
        buttons = QHBoxLayout()

        ok_btn = QPushButton("Aplicar a Todos")
        ThemeManager.apply_primary_button(ok_btn)
        ok_btn.clicked.connect(self.accept)

        cancel_btn = QPushButton("Cancelar")
        ThemeManager.apply_secondary_button(cancel_btn)
        cancel_btn.clicked.connect(self.reject)

        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        self.price_input.setFocus()

    def get_value(self):
        return self.price_input.text().strip()


class TransferDialog(ModernDialog):
    """Diálogo mejorado para transferencias"""

    def __init__(self, local_actual, nombre, cantidad_actual, locales, parent=None):
        super().__init__("Transferir Producto", 450, parent)
        self.local_actual = local_actual
        self.nombre = nombre
        self.cantidad_actual = cantidad_actual
        self.cantidad = 1
        self.locales = locales or []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 20)
        layout.setSpacing(20)

        # Header con información del producto
        header = QLabel(f"📦 {self.nombre}")
        header.setStyleSheet(
            f"""
            color: {Colors.PRIMARY};
            font-size: 18px;
            font-weight: 900;
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 10px;
            padding: 15px;
        """
        )
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Cantidad disponible
        available_label = QLabel(f"Cantidad disponible: {self.cantidad_actual}")
        available_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 14px; font-weight: 600;"
        )
        available_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(available_label)

        # Selector de cantidad
        quantity_layout = QHBoxLayout()
        quantity_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quantity_layout.setSpacing(15)

        minus_btn = QPushButton("−")
        minus_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.SURFACE_LIGHT};
                color: {Colors.PRIMARY};
                border: 2px solid {Colors.BORDER};
                border-radius: 20px;
                width: 40px;
                height: 40px;
                font-size: 18px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: {Colors.OVERLAY};
                border-color: {Colors.PRIMARY};
            }}
        """
        )
        minus_btn.clicked.connect(self.decrease_quantity)

        self.quantity_label = QLabel(str(self.cantidad))
        self.quantity_label.setStyleSheet(
            f"""
            color: {Colors.TEXT_PRIMARY};
            font-size: 20px;
            font-weight: 900;
            min-width: 50px;
            background: {Colors.BACKGROUND};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 8px;
        """
        )
        self.quantity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        plus_btn = QPushButton("+")
        plus_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {Colors.PRIMARY};
                color: #1A202C;
                border: none;
                border-radius: 20px;
                width: 40px;
                height: 40px;
                font-size: 18px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background: {Colors.PRIMARY_LIGHT};
            }}
        """
        )
        plus_btn.clicked.connect(self.increase_quantity)

        quantity_layout.addWidget(minus_btn)
        quantity_layout.addWidget(self.quantity_label)
        quantity_layout.addWidget(plus_btn)
        layout.addLayout(quantity_layout)

        # Selector de local destino
        local_label = QLabel("Local de destino:")
        local_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-weight: 700; margin-top: 10px;"
        )
        layout.addWidget(local_label)

        self.local_combo = ModernComboBox()
        available_locals = [loc for loc in self.locales if loc != self.local_actual]
        self.local_combo.addItems(available_locals)
        layout.addWidget(self.local_combo)

        # Botones de acción
        buttons = QHBoxLayout()

        transfer_btn = QPushButton("🚚 Transferir")
        ThemeManager.apply_primary_button(transfer_btn)
        transfer_btn.clicked.connect(self.accept)

        cancel_btn = QPushButton("Cancelar")
        ThemeManager.apply_secondary_button(cancel_btn)
        cancel_btn.clicked.connect(self.reject)

        buttons.addWidget(transfer_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    def increase_quantity(self):
        if self.cantidad < self.cantidad_actual:
            self.cantidad += 1
            self.quantity_label.setText(str(self.cantidad))

    def decrease_quantity(self):
        if self.cantidad > 1:
            self.cantidad -= 1
            self.quantity_label.setText(str(self.cantidad))

    def get_result(self):
        return self.local_combo.currentText(), self.cantidad


# ==================== TABLA CON SELECCIÓN MEJORADA ====================


class AdvancedTableWidget(QTableWidget):
    """Tabla con funciones avanzadas de selección y edición"""

    selection_changed = pyqtSignal(int)  # Número de elementos seleccionados

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_table()
        self.setup_selection_tracking()

    def setup_table(self):
        """Configura la tabla con estilos mejorados (tema oscuro/alto contraste)"""
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.NoEditTriggers)

        # Header
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)

        # ── Paleta de colores de alto contraste
        BG = "#0f172a"  # slate-900
        BG_ALT = "#111827"  # gray-900
        TEXT = "#e5e7eb"  # gray-200
        TEXT_MUTED = "#94a3b8"  # slate-400
        GRID = "#334155"  # slate-700
        HEAD_BG = "#0b1220"
        PRIMARY = "#ffc107"  # mismo amarillo de tu app

        self.setStyleSheet(
            f"""
            QTableWidget {{
                background: {BG};
                alternate-background-color: {BG_ALT};
                color: {TEXT};
                gridline-color: {GRID};
                border: 1px solid #1f2937;
                border-radius: 12px;
                selection-background-color: rgba(255,193,7,0.22);
                selection-color: {TEXT};
                font-size: 13px;
                outline: none;
            }}
            QTableWidget::item {{
                padding: 12px 10px;
                border-bottom: 1px solid #1f2937;
            }}
            QTableWidget::item:selected {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(255,193,7,0.28), stop:1 rgba(255,193,7,0.18));
                color: {TEXT};
                font-weight: 600;
            }}
            QTableWidget::item:hover {{
                background: rgba(255,255,255,0.03);
            }}

            /* Header */
            QHeaderView::section {{
                background: {HEAD_BG};
                color: {TEXT};
                font-weight: 800;
                font-size: 12.5px;
                padding: 14px 10px;
                border: none;
                border-bottom: 3px solid {PRIMARY};
                border-right: 1px solid #1f2937;
                text-transform: uppercase;
                letter-spacing: .4px;
            }}
            QHeaderView::section:hover {{
                background: #131a2a;
            }}
            QHeaderView::section:first {{
                border-top-left-radius: 12px;
            }}
            QHeaderView::section:last {{
                border-top-right-radius: 12px;
            }}
        """
        )

    def setup_selection_tracking(self):
        """Configura el seguimiento de selección"""
        self.itemSelectionChanged.connect(self.on_selection_changed)

    def on_selection_changed(self):
        """Emite señal cuando cambia la selección"""
        selected_rows = len(set(index.row() for index in self.selectedIndexes()))
        self.selection_changed.emit(selected_rows)

    def get_selected_product_ids(self):
        """Obtiene IDs de productos seleccionados"""
        selected_rows = set(index.row() for index in self.selectedIndexes())
        product_ids = []

        for row in selected_rows:
            id_item = self.item(
                row, 0
            )  # ID está en columna 0 (guardado en UserRole del nombre)
            if id_item and id_item.data(Qt.ItemDataRole.UserRole):
                product_ids.append(id_item.data(Qt.ItemDataRole.UserRole))

        return product_ids


# ==================== VISTA PRINCIPAL MEJORADA ====================


class StockView(QMainWindow):
    """Vista principal de stock con todas las mejoras + validación en tiempo real"""

    def __init__(self, username: str, role: str, local_name: str, back_command=None):
        super().__init__()
        self.username = username
        self.role = role
        self.local = self._clean_local_label(local_name)
        self._edit_unlocked = self.role == "admin"
        self._admin_add_unlocked = False
        self.locales = self._load_locales(local_name)
        # Ajustar local activo para admins (por defecto al primer local disponible)
        if self.role == "admin":
            if not self.local or (self.local not in self.locales):
                self.local = self.locales[0] if self.locales else ""
            # Mostrar por defecto el inventario consolidado (todos los locales)
            self.view_local = "Todos los locales"
        else:
            self.view_local = self.local
        self.read_only = False
        if self.role == "admin" and self.view_local in ("Todos", "Todos los locales"):
            self.read_only = True
        self.back_command = back_command

        # Estado
        self.loading_thread = None
        self.last_search_time = 0
        self.categories_cache = []
        self.fabricantes_cache = []
        self._products_by_id = {}
        self._row_by_product_id = {}
        self._totals = {"productos": 0, "unidades": 0, "valor": 0}

        # Workers para operaciones de stock
        # Cola centralizada para procesar operaciones (evita crear Hilos por clic)
        self.operation_queue = OperationQueue(
            retry=1, delay_between=0.05, max_workers=5
        )
        self.operation_queue.operation_finished.connect(self._on_queue_finished)
        self.operation_queue.operation_error.connect(self._on_queue_error)
        self.operation_queue.queue_count.connect(self._on_queue_count_changed)
        self.operation_queue.start()

        # Lista opcional para referencias a threads o pools (legacy)
        self.stock_operation_threads = (
            []
        )  # Lista para mantener referencias a threads activos

        # Throttling por producto (ms)
        self._last_op_time_by_pid = {}
        # Evitar bombardear alertas de stock bajo por el mismo producto
        self._low_stock_alerted_at = {}
        self._combo_sync_running = False
        self._combo_sync_last_local = ""
        self._combo_sync_last_ts = 0
        self._combo_sync_thread = None
        self._pending_reload = False
        self._last_filter_signature = None
        self._measure_options_cache = {}

        # Timers reutilizables para evitar crear handles en cada acción
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self.load_data)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(
            lambda: self._delayed_search(self.last_search_time)
        )

        # Timer para limpiar resaltados sin crear timers por celda
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._flush_highlight_queue)
        self._highlight_clear_queue = []

        # Batch de actualizaciones provenientes de la cola (para no saturar la UI)
        self._pending_updates = []
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._flush_pending_updates)

        self._edit_timeout_timer = QTimer(self)
        self._edit_timeout_timer.setSingleShot(True)
        self._edit_timeout_timer.timeout.connect(self._auto_lock_edit_mode)

        # Nota: no tocar schema aquí para no bloquear UI si la BD no responde.

        # Ventana MÁS GRANDE
        self.setWindowTitle(
            f"Stock - {self.local if self.role=='local' else 'Administrador'}"
        )
        self.setProperty("manarey_no_scale", False)
        self.setMinimumSize(1100, 650)
        try:
            self.resize(scale(1300), scale(760))
        except Exception:
            self.resize(1300, 760)
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(26,32,44,0.97), stop:1 rgba(45,55,72,0.95));
            }}
            {MAIN_STYLE_SHEET}
        """
        )

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        self.main_layout = QVBoxLayout(central)
        self.main_layout.setContentsMargins(20, 12, 20, 20)
        self.main_layout.setSpacing(22)

        # Restaurar tamaño/estado si existe
        self._restore_window_state()

        # Crear header (botón ← Volver | título | stretch | indicadores)
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(14)

        if self.back_command:
            back_btn = QPushButton("← Volver")
            back_btn.setCursor(Qt.PointingHandCursor)
            back_btn.setStyleSheet(
                "QPushButton{background:#34343a;color:#C9A040;border:1px solid #3e3e44;"
                "border-radius:10px;padding:8px 14px;font-weight:700;}"
                "QPushButton:hover{background:#3e3e44;}"
            )
            back_btn.clicked.connect(self.back_command)
            header_layout.addWidget(back_btn)

        self.header_title = QLabel(f"Stock de {self.view_local}")
        self.header_title.setStyleSheet(
            "color: #C9A040; font-size: 26px; font-weight: 900;"
        )
        header_layout.addWidget(self.header_title)

        header_layout.addStretch()

        # Indicador de cola (oculto por defecto)
        self.queue_banner = QLabel("")
        self.queue_banner.setStyleSheet(
            "color: #ffffff; background: #111827; padding: 6px 10px; border-radius: 8px;"
        )
        self.queue_banner.setVisible(False)
        header_layout.addWidget(self.queue_banner)

        self.meta_label = QLabel("")
        self.meta_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.meta_label.setStyleSheet(
            "color:#e5e7eb; font-weight:700; padding: 2px 10px;"
        )
        header_layout.addWidget(self.meta_label)

        self.main_layout.addWidget(header_widget)

        # Meta label (local + hora) auto-actualizada
        self._meta_timer = QTimer(self)
        self._meta_timer.setInterval(60_000)
        self._meta_timer.timeout.connect(self._update_meta_label)
        self._update_meta_label()
        self._meta_timer.start()

        # Filtros
        self.create_filters()

        # Tabla
        self.create_table()

        # Acciones (Excel / Editar / Crear combos) mas abajo
        self.create_actions_row()

        # Reservas (señas / envios)
        self.create_reserved_sections()

        # Formulario (solo si no es read-only)
        if not self.read_only:
            self.create_form()

        # Cargar datos
        self.load_data()

        # Live refresh ligero (solo actualiza filas cambiadas) - desactivado para evitar bloqueos
        self._live_timer = None
        # Watchdog para diagnosticar freezes: registra stack del hilo principal cada pocos segundos
        try:
            import sys
            import threading
            import time
            import traceback

            def _watchdog_loop():
                while True:
                    time.sleep(5)
                    try:
                        frames = sys._current_frames()
                        main_id = threading.main_thread().ident
                        frame = frames.get(main_id)
                        if frame is not None:
                            stack = "".join(traceback.format_stack(frame))
                            logger.info(f"MAIN_THREAD_STACK:\n{stack}")
                    except Exception:
                        try:
                            logger.exception("Watchdog error")
                        except Exception:
                            pass

            wd = threading.Thread(
                target=_watchdog_loop, daemon=True, name="ui_watchdog"
            )
            wd.start()
            self._watchdog_thread = wd
        except Exception:
            pass

    def _load_locales(self, current_local):
        """Carga lista de locales desde BD; usa LOCALES como respaldo."""
        try:
            locs = [l for l in get_all_locals() if l]
        except Exception:
            locs = []
        if current_local and current_local not in locs:
            locs.append(current_local)
        if not locs:
            locs = LOCALES[:]
        # normaliza y elimina duplicados preservando orden
        norm = []
        for l in locs:
            l = self._clean_local_label(l)
            l = (l or "").strip() or "Sin local"
            norm.append(l)
        return _dedupe(norm)

    def create_search_bar(self):
        """Barra superior removida; se usan los filtros del bloque principal."""
        return

    def create_summary_card(self):
        """Tarjeta superior con totales y contexto."""
        pass

    def _enforce_lowercase_input(self, widget):
        """Fuerza entrada en minúsculas en campos de texto del stock."""
        from PyQt5.QtWidgets import QLineEdit

        if not isinstance(widget, QLineEdit):
            return
        try:
            hints = widget.inputMethodHints() | Qt.ImhPreferLowercase
            hints |= getattr(Qt, "ImhNoAutoUppercase", Qt.InputMethodHints())
            widget.setInputMethodHints(hints)
        except Exception:
            pass

        def _normalize(text):
            lowered = text.lower()
            if text == lowered:
                return
            cursor = widget.cursorPosition()
            widget.blockSignals(True)
            widget.setText(lowered)
            widget.blockSignals(False)
            widget.setCursorPosition(min(cursor, len(lowered)))

        widget.textChanged.connect(_normalize)
        _normalize(widget.text())

    def _clean_local_label(self, name: str) -> str:
        """Quita prefijos visuales (emoji/mojibake) en nombres de local."""
        val = (name or "").strip()
        if " " in val:
            token, rest = val.split(" ", 1)
            if token and set(token) == {"?"}:
                val = rest.lstrip()
            elif (
                len(token) <= 6
                and any(ord(c) > 127 for c in token)
                and not token.isalpha()
            ):
                val = rest.lstrip()
        return val.strip()

    def _normalize_local_name(self, name: str) -> str:
        val = self._clean_local_label(name).lower()
        # Remove known placeholder markers.
        for token in ("????", "???????", "??", "???"):
            val = val.replace(token, "")
        return val.strip()

    def _update_meta_label(self):
        """Actualiza texto de contexto (local + hora actual)."""
        try:
            # Protección defensiva: asegurarse de que los atributos existen
            if not hasattr(self, "meta_label"):
                return
            if not hasattr(self, "view_local"):
                view_local = getattr(self, "local", "") or ""
            else:
                view_local = self.view_local or ""
            now = datetime.now().strftime("%d/%m %H:%M")
            self.meta_label.setText(f"{view_local} | {now}")
        except Exception as e:
            logger.exception(f"Error actualizando meta label: {e}")

    def _live_refresh_rows(self):
        """Refresca solo filas cambiadas para este local (sin recargar toda la tabla)."""
        try:
            rows = sm.get_stock_filtered(
                self.view_local, apply_reservas=self._use_reservas_stock()
            )
            new_map = {p.get("id"): p for p in rows if p.get("id")}
            old_ids = set(self._products_by_id.keys())
            new_ids = set(new_map.keys())

            # Si hay nuevos o faltantes, recargar completa para mantener consistencia
            if old_ids != new_ids:
                self.load_data()
                return

            for pid, new_prod in new_map.items():
                old_prod = self._products_by_id.get(pid, {})
                for field in (
                    "nombre",
                    "cantidad",
                    "categoria",
                    "fabricante",
                    "medida",
                    "estado",
                    "precio_venta",
                ):
                    if str(new_prod.get(field, "")) != str(old_prod.get(field, "")):
                        self._update_product_field(pid, field, new_prod.get(field))
                # actualizar cache
                self._products_by_id[pid] = new_prod
        except Exception as e:
            logger.error(f"Live refresh error: {e}")

    def create_filters(self):
        """Crea la sección de filtros"""
        filters_frame = QFrame()
        compact_height = scale(28)
        filters_frame.setStyleSheet(
            f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.12), stop:1 rgba(255,255,255,0.08));
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 20px;
                padding: 22px;
            }}
            QLineEdit[filter="true"], QComboBox[filter="true"] {{
                background: #FCD34D;
                color: #111827;
                border: 1px solid #92400E;
                border-radius: 8px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 700;
                min-height: {compact_height}px;
                selection-background-color: #F59E0B;
                selection-color: #111827;
            }}
            QLineEdit[filter="true"]::placeholder {{
                color: #7C5B0B;
            }}
            QLineEdit[filter="true"]:disabled, QComboBox[filter="true"]:disabled {{
                background: #E5E7EB;
                color: #6B7280;
                border-color: #D1D5DB;
            }}
            QComboBox[filter="true"]::drop-down {{
                border: none;
                width: 18px;
            }}
            QComboBox[filter="true"]::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #92400E;
                width: 0;
                height: 0;
            }}
            QComboBox[filter="true"] QAbstractItemView {{
                background: #FCD34D;
                color: #111827;
                selection-background-color: #F59E0B;
                selection-color: #111827;
                border: 1px solid #92400E;
                border-radius: 8px;
                padding: 6px;
            }}
            QComboBox[filter="true"] QAbstractItemView::item {{
                padding: 6px 8px;
                border-radius: 6px;
            }}
            QComboBox[filter="true"] QAbstractItemView::item:selected {{
                background: #F59E0B;
                color: #111827;
            }}
        """
        )

        filters_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(filters_frame)
        layout.setSpacing(scale(12))

        # Fila con título y botones de tipo de medida
        title_buttons_widget = QFrame()
        title_buttons_widget.setStyleSheet("background: transparent; border: none;")
        title_buttons_layout = QHBoxLayout(title_buttons_widget)
        title_buttons_layout.setSpacing(scale(16))
        title_buttons_layout.setContentsMargins(0, 0, 0, 0)

        # Título (más legible)
        title = QLabel("Filtros de busqueda")
        title.setStyleSheet(
            """
            QLabel {
                color: #111827;
                background: #F59E0B;
                border: 1px solid #D97706;
                border-radius: 10px;
                padding: 4px 10px;
                font-size: 15px;
                font-weight: 900;
                letter-spacing: 0.6px;
            }
        """
        )
        title.setMinimumHeight(scale(30))
        title.setMinimumWidth(scale(220))
        title_buttons_layout.addWidget(title)

        # Botones de tipo de medida (en la misma fila que el título)
        self._filter_measure_type_checks = {}
        self._filter_measure_type_group = QButtonGroup(self)
        self._filter_measure_type_group.setExclusive(True)

        for label in ("Metro", "Plaza", "Rodado", "Pulgada", "Litros", "Codigo"):
            cb = QCheckBox(label)
            cb.setProperty("measure_mode", label.lower())
            cb.setCursor(Qt.PointingHandCursor)
            cb.setStyleSheet(
                """
                QCheckBox {
                    color: #e5e7eb;
                    font-weight: 700;
                    padding: 6px 12px;
                    border: 1px solid #374151;
                    border-radius: 8px;
                    background: #111827;
                    font-size: 13px;
                }
                QCheckBox::indicator { width: 0; height: 0; }
                QCheckBox:checked {
                    background: #3b82f6;
                    color: #ffffff;
                    border-color: #1e40af;
                }
            """
            )
            self._filter_measure_type_group.addButton(cb)
            self._filter_measure_type_checks[label.lower()] = cb
            title_buttons_layout.addWidget(cb)

        self._filter_measure_type_group.buttonClicked.connect(
            self._on_filter_measure_type_clicked
        )

        title_buttons_layout.addStretch()
        layout.addWidget(title_buttons_widget)

        # Fila de filtros con wrap automático
        row_widget = FlowContainer()
        row_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = FlowLayout(row_widget, margin=0, spacing=scale(10))

        def _mark_filter(widget):
            widget.setProperty("filter", True)
            try:
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            except Exception:
                pass

        # Busqueda
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar producto...")
        self.search_input.setMinimumWidth(scale(150))
        self.search_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.search_input.setClearButtonEnabled(True)
        _mark_filter(self.search_input)
        self.search_input.textChanged.connect(self.on_search_changed)
        row.addWidget(self.search_input)

        # Categoria
        self.category_combo = QComboBox()
        self.category_combo.addItem("Todas las categorias")
        self.category_combo.setMinimumWidth(scale(150))
        self.category_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _mark_filter(self.category_combo)
        self.category_combo.currentIndexChanged.connect(self.load_data)
        row.addWidget(self.category_combo)

        # Fabricante
        self.fabricante_combo = QComboBox()
        self.fabricante_combo.addItem("Fabricante")
        self.fabricante_combo.setMinimumWidth(scale(140))
        self.fabricante_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _mark_filter(self.fabricante_combo)
        self.fabricante_combo.currentIndexChanged.connect(self.load_data)
        _set_combo_popup_width(self.fabricante_combo)
        row.addWidget(self.fabricante_combo)

        # Estado (se crea aquí porque la lógica de carga/filtrado ya lo soporta)
        self.estado_filter = QComboBox()
        self.estado_filter.addItem("Estado")
        # usar valores estáticos para no depender de la carga inicial de productos
        try:
            for est in ESTADOS:
                self.estado_filter.addItem(est)
        except Exception:
            pass
        self.estado_filter.setMinimumWidth(scale(120))
        self.estado_filter.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _mark_filter(self.estado_filter)
        self.estado_filter.currentIndexChanged.connect(self.load_data)
        _set_combo_popup_width(self.estado_filter)
        row.addWidget(self.estado_filter)

        # Color (filtro opcional, lista dinámica)
        self.color_combo = QComboBox()
        self.color_combo.addItem("Color")
        self.color_combo.setMinimumWidth(scale(120))
        self.color_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _mark_filter(self.color_combo)
        self.color_combo.currentIndexChanged.connect(self.load_data)
        _set_combo_popup_width(self.color_combo)
        row.addWidget(self.color_combo)

        # Combo dinámico para valores de medida
        self.filter_medida_combo = QComboBox()
        self.filter_medida_combo.setMinimumWidth(scale(120))
        self.filter_medida_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _mark_filter(self.filter_medida_combo)
        self.filter_medida_combo.currentIndexChanged.connect(self.load_data)
        _set_combo_popup_width(self.filter_medida_combo)
        row.addWidget(self.filter_medida_combo)

        # Input de código (se muestra cuando el modo es "codigo")
        self.codigo_input = QLineEdit()
        self.codigo_input.setPlaceholderText("Codigo")
        self.codigo_input.setMinimumWidth(scale(120))
        self.codigo_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.codigo_input.setClearButtonEnabled(True)
        _mark_filter(self.codigo_input)
        self._enforce_lowercase_input(self.codigo_input)
        self.codigo_input.textChanged.connect(self._on_codigo_filter_changed)
        self.codigo_input.hide()
        row.addWidget(self.codigo_input)

        # Inicializar modo de medida para filtros
        saved_filter_mode = self._load_filter_measure_mode_pref()
        if saved_filter_mode not in (
            "metro",
            "plaza",
            "rodado",
            "pulgada",
            "litros",
            "codigo",
        ):
            saved_filter_mode = "metro"
        self._set_filter_measure_mode(saved_filter_mode)

        clear_btn = QPushButton("X")
        clear_btn.setToolTip("Limpiar filtros")
        clear_btn.setStyleSheet(
            """
            QPushButton {
                background: #374151;
                color: #f8fafc;
                border: 1px solid #4b5563;
                border-radius: 8px;
                padding: 2px 4px;
                font-weight: 900;
            }
            QPushButton:hover { background: #4b5563; }
        """
        )
        clear_btn.clicked.connect(lambda: self.reset_filters())
        try:
            clear_btn.setMinimumSize(scale(30), scale(30))
        except Exception:
            clear_btn.setMinimumSize(30, 30)
        row.addWidget(clear_btn)

        layout.addWidget(row_widget)

        # Fila separada para selección de local
        local_widget = FlowContainer()
        local_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        local_row = FlowLayout(local_widget, margin=0, spacing=scale(10))
        local_label = QLabel("Ver stock de:")
        local_label.setStyleSheet("color:#e5e7eb;font-weight:700; padding: 4px 0;")
        local_label.setMinimumHeight(scale(32))
        local_row.addWidget(local_label)

        self.ver_stock_combo = QComboBox()
        if self.role == "admin":
            # Mostrar opción para ver todos los locales en modo admin
            try:
                self.ver_stock_combo.addItem("Todos los locales")
            except Exception:
                self.ver_stock_combo.addItem("Todos")
            for local in self.locales:
                self.ver_stock_combo.addItem(f"{local}")
        else:
            self.ver_stock_combo.addItem(f"{self.local}")
            for local in self.locales:
                if local != self.local:
                    self.ver_stock_combo.addItem(f"{local}")
        try:
            self.ver_stock_combo.setMinimumWidth(scale(180))
        except Exception:
            self.ver_stock_combo.setMinimumWidth(180)
        _mark_filter(self.ver_stock_combo)
        self.ver_stock_combo.currentIndexChanged.connect(self.on_ver_stock_changed)
        local_row.addWidget(self.ver_stock_combo)

        view_mode_label = QLabel("Ver:")
        view_mode_label.setStyleSheet("color:#e5e7eb;font-weight:700; padding: 4px 0;")
        view_mode_label.setMinimumHeight(scale(32))
        local_row.addWidget(view_mode_label)

        self.stock_view_mode_combo = QComboBox()
        if self.role == "admin":
            self.stock_view_mode_combo.addItems(["Disponibles"])
            self.stock_view_mode_combo.setEnabled(False)
        else:
            self.stock_view_mode_combo.addItems(
                ["Disponibles", "Señados", "Envíos", "Vendidos por otros"]
            )
        try:
            self.stock_view_mode_combo.setMinimumWidth(scale(150))
        except Exception:
            self.stock_view_mode_combo.setMinimumWidth(150)
        _mark_filter(self.stock_view_mode_combo)
        self.stock_view_mode_combo.currentIndexChanged.connect(
            self.on_view_mode_changed
        )
        local_row.addWidget(self.stock_view_mode_combo)
        excel_btn = QPushButton("Excel")
        excel_btn.setToolTip("Exportar PDF por categorias")
        excel_btn.setStyleSheet(
            """
            QPushButton {
                background: #F59E0B;
                color: #111827;
                border: 1px solid #D97706;
                border-radius: 8px;
                padding: 4px 6px;
                font-weight: 800;
                font-size: 11px;
            }
            QPushButton:hover { background: #D97706; }
        """
        )
        excel_btn.clicked.connect(self.export_stock_pdf_by_category)
        try:
            excel_btn.setMinimumSize(scale(70), scale(26))
        except Exception:
            excel_btn.setMinimumSize(70, 26)
        self.excel_btn = excel_btn
        local_row.addWidget(self.excel_btn)

        self.edit_btn = QPushButton("Editar")
        self.edit_btn.setToolTip("Desbloquear edición de stock")
        self.edit_btn.setStyleSheet(
            """
            QPushButton {
                background: #111827;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 4px 8px;
                font-weight: 800;
                font-size: 11px;
            }
            QPushButton:hover { background: #1f2937; }
        """
        )
        self.edit_btn.clicked.connect(self._toggle_edit_mode)
        try:
            self.edit_btn.setMinimumSize(scale(90), scale(26))
        except Exception:
            self.edit_btn.setMinimumSize(90, 26)
        self.edit_btn.setVisible(True)
        local_row.addWidget(self.edit_btn)

        self.combo_btn = QPushButton("Crear combos")
        self.combo_btn.setToolTip("Crear combos de productos")
        self.combo_btn.setStyleSheet(
            """
            QPushButton {
                background: #0f766e;
                color: #ecfeff;
                border: 1px solid #134e4a;
                border-radius: 8px;
                padding: 4px 8px;
                font-weight: 800;
                font-size: 11px;
            }
            QPushButton:hover { background: #115e59; }
        """
        )
        self.combo_btn.clicked.connect(self._open_combo_dialog)
        try:
            self.combo_btn.setMinimumSize(scale(110), scale(26))
        except Exception:
            self.combo_btn.setMinimumSize(110, 26)
        if self.role == "admin":
            self.combo_btn.setVisible(True)
        else:
            self.combo_btn.setVisible(False)
        local_row.addWidget(self.combo_btn)

        layout.addWidget(local_widget)

        self.main_layout.addWidget(filters_frame)
        # Cargar opciones iniciales (defer para no bloquear UI)
        QTimer.singleShot(50, self._post_init_load)

    def _post_init_load(self):
        try:
            if getattr(self, "role", "") != "admin":
                self.refresh_filter_options()
            self._update_edit_button()
            self._apply_edit_lock_state()
            self.load_data()
        except Exception as e:
            logger.error(f"Error en post_init_load: {e}")

    def export_stock_pdf_by_category(self):
        """Genera un PDF con productos agrupados por categoría."""
        try:

            def _qty_to_int(value):
                try:
                    text = str(value or "").strip()
                    if not text:
                        return 0
                    digits = "".join(ch for ch in text if ch.isdigit() or ch == "-")
                    return int(digits or 0)
                except Exception:
                    return 0

            products = (
                list(self._products_by_id.values()) if self._products_by_id else []
            )
            products = [p for p in products if _qty_to_int(p.get("cantidad", 0)) >= 1]
            if not products:
                # fallback: leer desde la tabla visible
                products = []
                for r in range(self.table.rowCount()):
                    try:
                        name = (self.table.item(r, 0).text() or "").strip()
                        material = (self.table.item(r, 1).text() or "").strip()
                        qty = (self.table.item(r, 7).text() or "").strip()
                        categoria = (
                            self.table.item(r, 2).text() or ""
                        ).strip() or "Sin categoría"
                        medida = (self.table.item(r, 3).text() or "").strip()
                        fabricante = (self.table.item(r, 4).text() or "").strip()
                        color = (self.table.item(r, 5).text() or "").strip()
                        estado = (self.table.item(r, 6).text() or "").strip()
                        precio = (self.table.item(r, 8).text() or "").strip()
                        if _qty_to_int(qty) < 1:
                            continue
                        products.append(
                            {
                                "nombre": name,
                                "material": material,
                                "cantidad": qty,
                                "categoria": categoria,
                                "color": color,
                                "fabricante": fabricante,
                                "medida": medida,
                                "estado": estado,
                                "precio_venta": precio,
                            }
                        )
                    except Exception:
                        continue

            if not products:
                QMessageBox.information(
                    self, "Excel", "No hay productos para exportar."
                )
                return

            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.platypus import (
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )

            # Agrupar por categoria
            groups = {}
            for p in products:
                cat = (p.get("categoria") or "Sin categoría").strip() or "Sin categoría"
                groups.setdefault(cat, []).append(p)

            for cat in groups:
                groups[cat].sort(key=lambda x: (x.get("nombre") or "").lower())

            base_dir = Path(__file__).resolve().parent.parent
            out_dir = base_dir / "exports"
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = out_dir / f"stock_por_categoria_{ts}.pdf"

            styles = getSampleStyleSheet()
            doc = SimpleDocTemplate(
                str(out_path),
                pagesize=A4,
                leftMargin=16 * mm,
                rightMargin=16 * mm,
                topMargin=16 * mm,
                bottomMargin=16 * mm,
            )

            elements = []
            title = f"Stock por categoría - {self.view_local}"
            elements.append(Paragraph(title, styles["Title"]))
            elements.append(
                Paragraph(datetime.now().strftime("%d/%m/%Y %H:%M"), styles["Normal"])
            )
            elements.append(Spacer(1, 8))

            col_widths = [
                48 * mm,
                12 * mm,
                20 * mm,
                26 * mm,
                20 * mm,
                28 * mm,
                18 * mm,
                10 * mm,
            ]

            for cat in sorted(groups.keys(), key=lambda x: x.lower()):
                elements.append(Spacer(1, 6))
                elements.append(Paragraph(cat, styles["Heading3"]))

                data = [
                    [
                        "Nombre",
                        "Cant",
                        "Color",
                        "Fabricante",
                        "Medida",
                        "Estado",
                        "Precio",
                        "OK",
                    ]
                ]
                for p in groups[cat]:
                    precio = p.get("precio_venta", "")
                    if isinstance(precio, (int, float)):
                        precio = f"${int(precio):,}".replace(",", ".")
                    data.append(
                        [
                            (p.get("nombre") or "").strip(),
                            str(p.get("cantidad") or ""),
                            (p.get("color") or "").strip(),
                            (p.get("fabricante") or "").strip(),
                            (p.get("medida") or "").strip(),
                            (p.get("estado") or "").strip(),
                            str(precio or ""),
                            "",
                        ]
                    )

                table = Table(data, colWidths=col_widths, hAlign="LEFT")
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F59E0B")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, 0), 9),
                            (
                                "GRID",
                                (0, 0),
                                (-1, -1),
                                0.25,
                                colors.HexColor("#CBD5E1"),
                            ),
                            ("FONTSIZE", (0, 1), (-1, -1), 8),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("ALIGN", (-1, 1), (-1, -1), "CENTER"),
                        ]
                    )
                )
                elements.append(table)

            doc.build(elements)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_path)))
            QMessageBox.information(self, "Excel", f"PDF generado:\n{out_path}")
        except Exception as e:
            QMessageBox.warning(self, "Excel", f"No se pudo generar el PDF:\n{e}")

    def create_table(self):
        """Crea la tabla de productos"""
        self.loading_bar = QProgressBar()
        self.loading_bar.setVisible(False)
        self.main_layout.addWidget(self.loading_bar)

        self.table = QTableWidget()
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Configurar tabla: esquema de columnas según rol (se ajusta en _apply_local_table_schema)
        self._apply_local_table_schema()
        self.table.setMinimumHeight(420)

        # Configuración
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionsMovable(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMouseTracking(True)

        # Permitir edición con doble click
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self.on_item_changed)

        # Conectar señal de cambio de items
        self._editing_enabled = False
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.on_cell_double_clicked)

        # Estilo base (se ajusta dinámicamente según ancho disponible)
        self._table_style_template = """
            QTableWidget {{
                background-color: #0f172a;
                alternate-background-color: #131f33;
                color: #f1f5f9;
                gridline-color: #334155;
                border: none;
                border-radius: 12px;
                font-size: {font}px;
            }}
            QHeaderView::section {{
                background-color: #0f172a;
                color: #fbbf24;
                padding: {header_pad}px;
                border: none;
                font-weight: bold;
                font-size: {header_font}px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            QTableWidget::item {{
                padding: {item_pad_v}px {item_pad_h}px;
                border-bottom: 1px solid #334155;
            }}
            QTableWidget::item:selected {{
                background-color: rgba(255,193,7,0.28);
                border: none;
            }}
            QTableWidget::item:alternate {{
                background-color: #131f33;
            }}
            QTableWidget::item:hover {{
                background-color: rgba(255,255,255,0.05);
            }}
        """
        self._table_density = None
        self._apply_table_density()

        # Ajuste de columnas
        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        self._fit_main_table_columns()
        # Elipsis en nombres largos
        try:
            self.table.setItemDelegateForColumn(0, ElideDelegate(self.table))
        except Exception:
            pass

        self.table_container = QFrame()
        self.table_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setMinimumHeight(300)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        try:
            self.table.verticalScrollBar().valueChanged.connect(
                self._populate_action_buttons_visible
            )
            self.table.horizontalScrollBar().valueChanged.connect(
                self._populate_action_buttons_visible
            )
        except Exception:
            pass
        table_layout = QVBoxLayout(self.table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table)
        self.main_layout.addWidget(self.table_container, 1)
        self.main_layout.addSpacing(20)

    def create_actions_row(self):
        """Fila inferior para acciones (Excel / Editar / Crear combos)."""
        # Los botones ahora están al lado del selector "Ver"
        return

    def _apply_table_density(self):
        """Ajusta tamaÃ±os de fuente/padding segÃºn ancho disponible."""
        try:
            if not hasattr(self, "table") or not hasattr(self, "_table_style_template"):
                return
            width = int(self.table.viewport().width() or self.table.width() or 0)
            if width < 1100:
                font = 12
                header_font = 11
                header_pad = 10
                item_pad_v = 10
                item_pad_h = 8
                row_h = 40
            elif width < 1500:
                font = 13
                header_font = 12
                header_pad = 12
                item_pad_v = 12
                item_pad_h = 10
                row_h = 44
            else:
                font = 15
                header_font = 14
                header_pad = 16
                item_pad_v = 14
                item_pad_h = 12
                row_h = 50
            density = (font, header_font, header_pad, item_pad_v, item_pad_h, row_h)
            if getattr(self, "_table_density", None) == density:
                return
            self._table_density = density
            self.table.setStyleSheet(
                self._table_style_template.format(
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

    def _normalize_medida_text(self, text: str) -> str:
        if text is None:
            return ""
        s = str(text).strip()
        if not s:
            return ""
        s = s.replace(" ", "")
        return s

    def _is_litros_medida(self, text: str) -> bool:
        try:
            value = self._normalize_medida_text(text)
            return bool(value and _LITROS_RE.match(value))
        except Exception:
            return False

    def _get_measure_options_for_mode(self, mode: str) -> list[str]:
        cache = getattr(self, "_measure_options_cache", {}) or {}
        if mode == "plaza":
            return list(cache.get("plaza") or PLAZAS)
        if mode == "rodado":
            return list(cache.get("rodado") or RODADOS)
        if mode == "pulgada":
            return list(cache.get("pulgada") or PULGADAS)
        if mode == "litros":
            return list(cache.get("litros") or [])
        return list(cache.get("metro") or self._sort_medidas(MEDIDAS_ESTANDAR))

    def _measure_sort_key(self, medida: str):
        s = self._normalize_medida_text(medida).lower()
        if not s:
            return (2, 0.0, "")
        num = None
        unit = ""
        try:
            if s.endswith("cm"):
                unit = "cm"
                num = float(s[:-2].replace(",", "."))
            elif s.endswith("l"):
                unit = "l"
                num = float(s[:-1].replace(",", "."))
            elif s.endswith("m"):
                unit = "m"
                num = float(s[:-1].replace(",", "."))
        except Exception:
            num = None
        if num is not None:
            if unit == "m":
                return (0, num * 100.0, s)
            if unit == "cm":
                return (0, num, s)
            if unit == "l":
                return (1, num, s)
            return (0, num, s)
        return (2, 0.0, s)

    def _sort_medidas(self, medidas: list) -> list:
        uniq = []
        seen = set()
        for m in medidas or []:
            t = self._normalize_medida_text(m)
            if not t or t in seen:
                continue
            seen.add(t)
            uniq.append(t)
        uniq.sort(key=self._measure_sort_key)
        return uniq

    def _maybe_add_custom_medida(self, text: str):
        try:
            mode = getattr(self, "_measure_mode", "metro")
            if mode != "metro":
                return
            value = self._normalize_medida_text(text)
            if not value or value == "Medida":
                return
            global MEDIDAS_ESTANDAR
            if value not in MEDIDAS_ESTANDAR:
                MEDIDAS_ESTANDAR.append(value)
                MEDIDAS_ESTANDAR = self._sort_medidas(MEDIDAS_ESTANDAR)
            if hasattr(self, "form_fields") and self.form_fields.get("medida"):
                combo = self.form_fields["medida"]
                current = value
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(["Medida"] + MEDIDAS_ESTANDAR)
                idx = combo.findText(current)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
        except Exception:
            pass

    def _populate_action_buttons_visible(self, *_args):
        """Renderiza botones de acciones solo en filas visibles para acelerar la carga."""
        t0 = time.perf_counter()
        try:
            logger.info("_populate_action_buttons_visible: start")
            # Permitir desactivar la creación de botones por variable de entorno
            try:
                if os.environ.get("MANAREY_DISABLE_ACTION_BUTTONS", "0") == "1":
                    logger.info(
                        "_populate_action_buttons_visible: disabled via MANAREY_DISABLE_ACTION_BUTTONS"
                    )
                    return
            except Exception:
                pass
            if getattr(self, "read_only", False):
                return
            if not getattr(self, "_actions_lazy", False):
                return
            table = self.table
            if table.rowCount() == 0:
                return
            if table.columnCount() <= 11:
                return
            top = table.rowAt(0)
            if top < 0:
                top = 0
            bottom = table.rowAt(table.viewport().height() - 1)
            if bottom < 0:
                bottom = min(table.rowCount() - 1, top + 50)
            for row in range(top, bottom + 1):
                if table.cellWidget(row, 9) is not None:
                    continue
                item = table.item(row, 0)
                if not item:
                    continue
                pid = item.data(Qt.ItemDataRole.UserRole)
                if pid is None:
                    continue
                product = self._products_by_id.get(pid)
                if not product:
                    continue
                self.add_action_buttons_new(row, product)
        except Exception:
            pass
        finally:
            try:
                logger.info(
                    f"_populate_action_buttons_visible: done in {time.perf_counter() - t0:.3f}s"
                )
            except Exception:
                pass

    def _fill_table_chunk(self, chunk_size: int = 120):
        """Llena la tabla por partes para mostrarla mas rapido sin congelar la UI."""
        try:
            if not hasattr(self, "_pending_products"):
                return
            total = len(self._pending_products)
            if total == 0:
                return
            start = int(getattr(self, "_pending_fill_index", 0) or 0)
            if start >= total:
                return

            # Ajustar chunk si hay configuración global
            eff_chunk = int(getattr(self, "_fill_chunk_size", chunk_size) or chunk_size)
            eff_chunk = max(10, eff_chunk)
            end = min(total, start + eff_chunk)
            # Aumentar filas de a poco para evitar bloqueos grandes
            try:
                if self.table.rowCount() < end:
                    self.table.setRowCount(end)
            except Exception:
                pass

            t0 = time.perf_counter()
            for i in range(start, end):
                p = self._pending_products[i]
                self._fill_stock_row(i, p)
                # Salir si ya consumimos demasiados ms en este tick
                if (time.perf_counter() - t0) > 0.02:
                    end = i + 1
                    break

            self._pending_fill_index = end
            if end < total:
                logger.info(
                    f"_fill_table_chunk: {start}->{end}/{total} in {time.perf_counter() - t0:.3f}s"
                )
                QTimer.singleShot(10, lambda: self._fill_table_chunk(chunk_size))
            else:
                try:
                    logger.info(
                        f"_fill_table_chunk: completed {total} rows in {time.perf_counter() - t0:.3f}s"
                    )
                except Exception:
                    pass
                try:
                    self.table.setUpdatesEnabled(True)
                except Exception:
                    pass
                QTimer.singleShot(0, self._highlight_duplicates)
                QTimer.singleShot(0, self._populate_action_buttons_visible)
        except Exception:
            pass

    def _fill_stock_row(self, i, p):
        try:
            table = self.table
            pid = p.get("id")

            if pid is not None:
                self._products_by_id[pid] = p
                self._row_by_product_id[pid] = i

            nombre = p.get("nombre", "") or ""
            material = p.get("material") or p.get("materiales") or "-"
            categoria = p.get("categoria", "") or "-"
            fabricante = p.get("fabricante", "") or "-"
            color = p.get("color", "") or "-"
            raw_medida = p.get("medida", "") or ""
            codigo = p.get("codigo", "") or ""
            medida = raw_medida.strip() or (codigo.strip() if codigo else "-")
            estado = p.get("estado", "") or "-"
            precio = p.get("precio_venta", 0) or 0
            cantidad = p.get("cantidad", 0) or 0

            display_name = nombre if nombre.strip() else "???"
            name_item = QTableWidgetItem(display_name)
            if pid is not None:
                name_item.setData(Qt.ItemDataRole.UserRole, pid)
            name_item.setToolTip(nombre or "Sin nombre")
            name_item.setFlags(
                (name_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            name_item.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(i, 0, name_item)

            material_item = QTableWidgetItem(str(material))
            material_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            material_item.setFlags(material_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 1, material_item)

            cat_item = QTableWidgetItem(categoria)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 2, cat_item)

            medida_item = QTableWidgetItem(medida)
            medida_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            medida_item.setFlags(medida_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 3, medida_item)

            fab_item = QTableWidgetItem(fabricante)
            fab_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            fab_item.setFlags(fab_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 4, fab_item)

            color_item = QTableWidgetItem(color)
            color_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            color_item.setFlags(color_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 5, color_item)

            estado_norm = (estado or "").strip()
            estado_key = estado_norm.lower()
            estado_map = {
                "nuevo": "Nuevo",
                "reacondicionado": "Reacondicionado",
                "promocion": "Promocion",
                "promociOKn": "Promocion",
            }
            estado_display = estado_map.get(estado_key, estado_norm) or "-"
            estado_item = QTableWidgetItem(estado_display)
            estado_item.setData(Qt.ItemDataRole.UserRole, estado_norm)
            estado_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            estado_item.setFlags(estado_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 6, estado_item)

            qty_item = QTableWidgetItem(str(int(cantidad)))
            qty_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            qty_item.setFlags(qty_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            qty_item.setForeground(QColor(248, 180, 0))
            # Stock bajo visual eliminado por requerimiento
            table.setItem(i, 7, qty_item)

            price_item = QTableWidgetItem(f"${int(precio):,}".replace(",", "."))
            price_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            price_item.setForeground(QColor(34, 197, 94))
            price_item.setFlags(price_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 8, price_item)

            key = (
                nombre.lower(),
                str(material).lower(),
                categoria.lower(),
                str(medida).lower(),
                estado.lower(),
                (color or "").lower(),
                fabricante.lower(),
                int(precio),
            )
            self._duplicate_keys.setdefault(key, []).append(i)
        except Exception:
            pass

    def _fit_main_table_columns(self):
        """Ajusta columnas principales para evitar solapamientos."""
        try:
            if not hasattr(self, "table"):
                return
            header = self.table.horizontalHeader()
            cols = self.table.columnCount()
            if cols <= 0:
                return
            # Stretch por defecto para columnas de texto
            for col in range(cols):
                header.setSectionResizeMode(col, QHeaderView.Stretch)
            fixed = {
                7: max(60, scale(70)),  # Cant
                8: max(90, scale(100)),  # Precio
                9: max(44, scale(50)),  # +
                10: max(44, scale(50)),  # -
                11: max(110, scale(120)),  # Transferir
            }
            if self.role == "admin":
                fixed[12] = max(90, scale(100))  # Eliminar
            for col, w in fixed.items():
                if col < cols:
                    header.setSectionResizeMode(col, QHeaderView.Fixed)
                    header.resizeSection(col, int(w))
            header.setMinimumSectionSize(40)
        except Exception:
            pass

    def create_reserved_sections(self):
        """Secciones de solo lectura para productos señados y con envío."""
        self.reservas_frame = QFrame()
        self.reservas_frame.setStyleSheet(
            """
            QFrame {
                background: #111827;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 20px;
                padding: 18px;
            }
            """
        )
        layout = QVBoxLayout(self.reservas_frame)
        layout.setSpacing(12)

        header = QLabel("Productos reservados")
        header.setStyleSheet("color: #ffc107; font-size: 18px; font-weight: 900;")
        layout.addWidget(header)

        self.senas_title = QLabel("Productos señados")
        self.senas_title.setStyleSheet("color: #e5e7eb; font-weight: 700;")
        layout.addWidget(self.senas_title)

        self.senas_table = QTableWidget()
        self._setup_reservas_table(self.senas_table)
        layout.addWidget(self.senas_table)

        self.envios_title = QLabel("Productos con envío")
        self.envios_title.setStyleSheet("color: #e5e7eb; font-weight: 700;")
        layout.addWidget(self.envios_title)

        self.envios_table = QTableWidget()
        self._setup_reservas_table(self.envios_table)
        layout.addWidget(self.envios_table)

        self.interlocal_title = QLabel("Vendidos por otros locales")
        self.interlocal_title.setStyleSheet("color: #e5e7eb; font-weight: 700;")
        layout.addWidget(self.interlocal_title)

        self.interlocal_table = QTableWidget()
        self._setup_interlocal_table(self.interlocal_table)
        layout.addWidget(self.interlocal_table)

        self.main_layout.addWidget(self.reservas_frame)
        self.main_layout.addSpacing(20)

    def _setup_reservas_table(self, table: QTableWidget):
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(
            ["Producto", "Cant", "Categoría", "Color", "Fabricante", "Medida", "Estado"]
        )
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        table.setMinimumHeight(300)
        table.setMaximumHeight(380)
        table.setStyleSheet(
            """
            QTableWidget {
                background-color: #0f172a;
                alternate-background-color: #131f33;
                color: #f1f5f9;
                gridline-color: #334155;
                border: none;
                border-radius: 12px;
                font-size: 14px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #fbbf24;
                padding: 12px;
                border: none;
                font-weight: bold;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            QTableWidget::item {
                padding: 10px 10px;
                border-bottom: 1px solid #334155;
            }
        """
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.verticalHeader().setDefaultSectionSize(34)

    def _setup_interlocal_table(self, table: QTableWidget):
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels(
            [
                "Producto",
                "Cant",
                "Categoría",
                "Color",
                "Fabricante",
                "Medida",
                "Estado",
                "Local venta",
                "Acción",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        table.setMinimumHeight(320)
        table.setMaximumHeight(420)
        table.setStyleSheet(
            """
            QTableWidget {
                background-color: #0f172a;
                alternate-background-color: #131f33;
                color: #f1f5f9;
                gridline-color: #334155;
                border: none;
                border-radius: 12px;
                font-size: 14px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #fbbf24;
                padding: 12px;
                border: none;
                font-weight: bold;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            QTableWidget::item {
                padding: 10px 10px;
                border-bottom: 1px solid #334155;
            }
        """
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        table.verticalHeader().setDefaultSectionSize(36)

    def _fill_reservas_table(self, table: QTableWidget, rows: list):
        table.setRowCount(0)
        row_idx = 0
        for r in rows:
            raw_nombre = (r.get("producto_nombre") or "").strip()
            raw_categoria = (r.get("producto_categoria") or "").strip()
            raw_fabricante = (r.get("producto_fabricante") or "").strip()
            raw_medida = (r.get("producto_medida") or "").strip()
            raw_estado = (r.get("producto_estado") or "").strip()
            raw_color = (r.get("color") or r.get("producto_color") or "").strip()
            try:
                qty = int(r.get("cantidad") or 0)
            except Exception:
                qty = 0
            if not (
                raw_nombre
                or raw_categoria
                or raw_fabricante
                or raw_medida
                or raw_estado
                or raw_color
                or qty
            ):
                continue
            nombre = raw_nombre or "-"
            categoria = raw_categoria or "-"
            fabricante = raw_fabricante or "-"
            medida = raw_medida or "-"
            estado = raw_estado or "-"
            color_val = raw_color or "-"

            table.insertRow(row_idx)

            name_item = QTableWidgetItem(nombre)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_idx, 0, name_item)

            qty_item = QTableWidgetItem(str(qty))
            qty_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.setItem(row_idx, 1, qty_item)

            cat_item = QTableWidgetItem(categoria)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, 2, cat_item)

            color_item = QTableWidgetItem(color_val)
            color_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, 3, color_item)

            fab_item = QTableWidgetItem(fabricante)
            fab_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, 4, fab_item)

            medida_item = QTableWidgetItem(medida)
            medida_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, 5, medida_item)

            estado_item = QTableWidgetItem(estado)
            estado_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, 6, estado_item)
            row_idx += 1

    def _fill_interlocal_table(self, rows: list):
        """Carga la tabla de interlocal en chunks para evitar bloqueos."""
        self._pending_interlocal_rows = list(rows or [])
        self._pending_interlocal_index = 0
        self.interlocal_table.setRowCount(0)
        self._fill_interlocal_chunk(60)

    def _fill_interlocal_chunk(self, chunk_size: int = 60):
        try:
            if not hasattr(self, "_pending_interlocal_rows"):
                return
            rows = self._pending_interlocal_rows
            total = len(rows)
            if total == 0:
                return
            start = int(getattr(self, "_pending_interlocal_index", 0) or 0)
            if start >= total:
                return
            eff = max(20, int(chunk_size or 60))
            end = min(total, start + eff)
            # Expand row count gradually
            try:
                if self.interlocal_table.rowCount() < end:
                    self.interlocal_table.setRowCount(end)
            except Exception:
                pass
            t0 = time.perf_counter()
            for i in range(start, end):
                r = rows[i]
                nombre = (r.get("producto_nombre") or "").strip() or "-"
                categoria = (r.get("producto_categoria") or "").strip() or "-"
                fabricante = (r.get("producto_fabricante") or "").strip() or "-"
                medida = (r.get("producto_medida") or "").strip() or "-"
                estado = (r.get("producto_estado") or "").strip() or "-"
                color = (r.get("producto_color") or "").strip() or "-"
                venta_local = (r.get("venta_local") or "").strip() or "-"
                try:
                    qty = int(r.get("cantidad") or 0)
                except Exception:
                    qty = 0

                self.interlocal_table.setItem(i, 0, QTableWidgetItem(nombre))
                qty_item = QTableWidgetItem(str(qty))
                qty_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.interlocal_table.setItem(i, 1, qty_item)

                cat_item = QTableWidgetItem(categoria)
                cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 2, cat_item)

                color_item = QTableWidgetItem(color)
                color_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 3, color_item)

                fab_item = QTableWidgetItem(fabricante)
                fab_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 4, fab_item)

                medida_item = QTableWidgetItem(medida)
                medida_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 5, medida_item)

                estado_item = QTableWidgetItem(estado)
                estado_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 6, estado_item)

                venta_item = QTableWidgetItem(venta_local)
                venta_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 7, venta_item)

                fecha_base = (
                    r.get("entrega_local_fecha") or r.get("venta_fecha") or ""
                ).strip()
                seguimiento = "Visible 4 dias"
                try:
                    fecha_dt = datetime.strptime(
                        fecha_base.split(".")[0].replace("T", " "),
                        "%Y-%m-%d %H:%M:%S",
                    )
                    dias_restantes = max(
                        0, 4 - max(0, (datetime.now() - fecha_dt).days)
                    )
                    seguimiento = f"Desaparece en {dias_restantes} dia(s)"
                except Exception:
                    pass
                seguimiento_item = QTableWidgetItem(seguimiento)
                seguimiento_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.interlocal_table.setItem(i, 8, seguimiento_item)

                if (time.perf_counter() - t0) > 0.02:
                    end = i + 1
                    break

            self._pending_interlocal_index = end
            if end < total:
                QTimer.singleShot(10, lambda: self._fill_interlocal_chunk(chunk_size))
        except Exception:
            pass

    def _populate_interlocal_actions_visible(self):
        return

    def _load_reservas_sections(self):
        if not hasattr(self, "reservas_frame"):
            return
        if getattr(self, "role", "") == "admin":
            self.reservas_frame.setVisible(False)
            return
        logger.info(
            f"_load_reservas_sections: mode={self._get_stock_view_mode()} local={self.view_local}"
        )
        mode = self._get_stock_view_mode()
        if mode == "Disponibles":
            self.reservas_frame.setVisible(False)
            return
        self.reservas_frame.setVisible(True)
        local_arg = self.view_local
        if (
            getattr(self, "_reservas_thread", None)
            and self._reservas_thread.isRunning()
        ):
            return
        self._reservas_thread = ReservasThread(local_arg, parent=self)
        self._reservas_thread.data_ready.connect(self._on_reservas_loaded)
        self._reservas_thread.start()

    def _on_reservas_loaded(
        self, local_arg: str, senas: list, envios: list, interlocal: list
    ):
        try:
            if str(local_arg or "") != str(self.view_local or ""):
                return
            mode = self._get_stock_view_mode()
            if hasattr(self, "senas_title"):
                self.senas_title.setText(f"Productos señados ({len(senas)})")
            if hasattr(self, "envios_title"):
                self.envios_title.setText(f"Productos con env??o ({len(envios)})")
            if hasattr(self, "interlocal_title"):
                self.interlocal_title.setText(
                    f"Vendidos por otros locales ({len(interlocal)})"
                )
            show_senas = mode == "Señados"
            show_envios = mode == "Env??os"
            show_interlocal = mode == "Vendidos por otros"
            self.senas_title.setVisible(show_senas)
            self.senas_table.setVisible(show_senas)
            self.envios_title.setVisible(show_envios)
            self.envios_table.setVisible(show_envios)
            self.interlocal_title.setVisible(show_interlocal)
            self.interlocal_table.setVisible(show_interlocal)
            if show_senas:
                self._fill_reservas_table(self.senas_table, senas)
            if show_envios:
                self._fill_reservas_table(self.envios_table, envios)
            if show_interlocal:
                self._fill_interlocal_table(interlocal)
        except Exception:
            pass

    def _mark_interlocal_delivered(
        self, venta_id: int, producto_id: int, stock_local: str
    ):
        if not venta_id or not producto_id:
            return
        ok, msg = vm.marcar_entrega_interlocal(
            int(venta_id), int(producto_id), stock_local, usuario=self.username
        )
        if not ok:
            QMessageBox.warning(self, "Entrega", msg)
            return
        self._load_reservas_sections()

    def _is_consolidated_view(self) -> bool:
        if self.role != "admin":
            return False
        val = (self.view_local or "").strip().lower()
        return val in ("todos", "todos los locales") or (
            "todos" in val and "local" in val
        )

    def _get_stock_view_mode(self) -> str:
        try:
            if getattr(self, "role", "") == "admin":
                return "Disponibles"
            return self.stock_view_mode_combo.currentText()
        except Exception:
            return "Disponibles"

    def _use_reservas_stock(self) -> bool:
        if getattr(self, "role", "") == "admin":
            return False
        return (not self._is_consolidated_view()) and (
            self._get_stock_view_mode() == "Disponibles"
        )

    def _apply_view_mode(self):
        mode = self._get_stock_view_mode()
        show_main = mode == "Disponibles"
        if hasattr(self, "table_container"):
            self.table_container.setVisible(show_main)
        if hasattr(self, "form_frame"):
            self.form_frame.setVisible(
                show_main and self._can_add_product() and not self.read_only
            )
        if hasattr(self, "reservas_frame"):
            self.reservas_frame.setVisible(not show_main)
        if show_main and getattr(self, "role", "") != "admin":
            self._trigger_combo_sync()

    def _apply_local_table_schema(self):
        """Restaura esquema de columnas para vista de un solo local."""
        # Columnas distintas si el usuario es admin
        if getattr(self, "role", "") == "admin":
            cols = [
                "Nombre",
                "Material",
                "Categor?a",
                "Medida",
                "Fabricante",
                "Color",
                "Estado",
                "Cant",
                "Precio",
                "+",
                "-",
                "Transferir",
                "Eliminar",
            ]
        else:
            cols = [
                "Nombre",
                "Material",
                "Categor?a",
                "Medida",
                "Fabricante",
                "Color",
                "Estado",
                "Cant",
                "Precio",
                "+",
                "-",
                "Transferir",
            ]

        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)

        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setStretchLastSection(False)
        header.setHighlightSections(False)

        self.table.showColumn(11)

        self._fit_main_table_columns()
        if self.role == "admin":
            self.table.showColumn(11)

    def _can_edit_stock(self) -> bool:
        if self.role == "admin":
            return True
        return bool(self._edit_unlocked)

    def _can_add_product(self) -> bool:
        if self.role == "admin":
            return bool(self._admin_add_unlocked)
        return self._can_edit_stock()

    def _get_cash_password(self) -> str:
        pwd_db = vm.get_cash_withdraw_password(default="")
        if pwd_db:
            return pwd_db
        for p in db_mod.CONFIG_PATHS:
            try:
                if p.exists():
                    cfg = json.loads(p.read_text(encoding="utf-8"))
                    pwd = (cfg.get("cash_withdraw_password") or "").strip()
                    if pwd:
                        return pwd
            except Exception:
                continue
        return "Manavella10"

    def _refresh_action_buttons_state(self):
        """Actualiza enabled de + / - / Transferir segun permisos."""
        try:
            can_qty = self._can_edit_stock() and not self.read_only
            can_transfer = not self.read_only
            for row in range(self.table.rowCount()):
                id_item = self.table.item(row, 0)
                if not id_item:
                    continue
                pid = id_item.data(Qt.ItemDataRole.UserRole)
                product = self._products_by_id.get(pid, {}) or {}
                is_combo = int(product.get("is_combo") or 0) == 1
                for col, enabled in ((9, can_qty), (10, can_qty), (11, can_transfer)):
                    w = self.table.cellWidget(row, col)
                    if w is not None:
                        btn = w.property("action_btn")
                        if btn is None:
                            btn = w.findChild(QPushButton)
                        if btn is not None:
                            btn.setEnabled(enabled and not is_combo)
                if self.role == "admin":
                    w = self.table.cellWidget(row, 12)
                    if w is not None:
                        btn = w.property("action_btn")
                        if btn is None:
                            btn = w.findChild(QPushButton)
                        if btn is not None:
                            btn.setEnabled(can_qty)
        except Exception:
            pass

    def _apply_edit_lock_state(self):
        try:
            can_edit = self._can_edit_stock()
            can_add = self._can_add_product()
            if hasattr(self, "add_btn"):
                self.add_btn.setEnabled(can_add)
            if hasattr(self, "combo_btn"):
                self.combo_btn.setEnabled(True if self.role == "admin" else can_edit)
            if hasattr(self, "form_frame"):
                show_main = self._get_stock_view_mode() == "Disponibles"
                self.form_frame.setVisible(show_main and can_add and not self.read_only)
            for key, w in getattr(self, "form_fields", {}).items():
                try:
                    w.setEnabled(can_add)
                except Exception:
                    pass
            self._editing_enabled = bool(can_edit)
            if can_edit and not self.read_only and not self._is_consolidated_view():
                self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
            else:
                self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self._refresh_action_buttons_state()
        except Exception:
            pass

    def _toggle_edit_mode(self):
        if self.role == "admin":
            self._admin_add_unlocked = not bool(
                getattr(self, "_admin_add_unlocked", False)
            )
            self._update_edit_button()
            self._apply_edit_lock_state()
            return
        if self._edit_unlocked:
            self._edit_unlocked = False
            self._edit_timeout_timer.stop()
        else:
            dlg = QDialog(self)
            dlg.setWindowTitle("Editar stock")
            dlg.setStyleSheet(
                """
                QDialog { background: #0f172a; }
                QLabel { color: #e5e7eb; }
                QLineEdit {
                    background: #111827;
                    color: #e5e7eb;
                    border: 1px solid #374151;
                    border-radius: 10px;
                    padding: 8px 10px;
                    font-weight: 700;
                }
                QCheckBox { color: #e5e7eb; font-weight: 600; }
                QPushButton {
                    background: #1f2937;
                    color: #e5e7eb;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 16px;
                    font-weight: 800;
                }
                QPushButton:hover { background: #374151; }
            """
            )
            lay = QVBoxLayout(dlg)
            title = QLabel("Desbloquear edición")
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
                return
            pwd = (pass_input.text() or "").strip()
            if pwd != self._get_cash_password():
                QMessageBox.warning(self, "Editar stock", "Contraseña incorrecta.")
                return
            self._edit_unlocked = True
            self._reset_edit_timeout()
        self._update_edit_button()
        self._apply_edit_lock_state()

    def _open_combo_dialog(self):
        if self.role != "admin":
            if not self._can_edit_stock():
                QMessageBox.warning(
                    self,
                    "Editar stock",
                    "Ingresá la contraseña con el botón Editar para crear combos.",
                )
                return
            if self.read_only:
                QMessageBox.warning(
                    self, "Solo lectura", "No puedes crear combos en otro local"
                )
                return
        combo_local = self.local or self.view_local or ""
        if not combo_local or combo_local in ("Todos", "Todos los locales"):
            try:
                all_locs = sm._get_all_locals_from_db()
                combo_local = all_locs[0] if all_locs else ""
            except Exception:
                combo_local = ""
        try:
            # Si admin en vista consolidada, cargar productos de todos los locales
            if self.role == "admin" and self._is_consolidated_view():
                all_locs = sm._get_all_locals_from_db() or [combo_local]
                if combo_local:
                    all_locs = [combo_local] + [
                        loc for loc in all_locs if loc != combo_local
                    ]
                all_products = []
                for loc in all_locs:
                    all_products.extend(
                        sm.get_stock_filtered(loc, "", "", "", apply_reservas=True)
                    )
                # Deduplicar por nombre+medida+material+categoria (tomar el primero)
                seen = set()
                products = []
                for p in all_products:
                    if int(p.get("is_combo") or 0) == 1:
                        continue
                    k = (
                        (p.get("nombre") or "").lower().strip(),
                        (p.get("medida") or "").lower().strip(),
                        (p.get("material") or "").lower().strip(),
                        (p.get("categoria") or "").lower().strip(),
                    )
                    if k not in seen:
                        seen.add(k)
                        products.append(p)
            else:
                products = sm.get_stock_filtered(
                    combo_local, "", "", "", apply_reservas=True
                )
                products = [p for p in products if int(p.get("is_combo") or 0) == 0]
        except Exception:
            products = []
        products = [p for p in products if (p.get("nombre") or "").strip()]

        dlg = QDialog(self)
        dlg.setWindowTitle("Crear combo")
        dlg.resize(1200, 700)
        dlg.setMinimumSize(1000, 600)
        dlg.setStyleSheet(
            """
            QDialog { background: #0f172a; }
            QLabel { color: #e5e7eb; font-weight: 700; }
            QLineEdit {
                background: #111827;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px 8px;
                font-weight: 700;
            }
            QSpinBox {
                background: #111827;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 2px 4px;
            }
            QPushButton {
                background: #1f2937;
                color: #e5e7eb;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 800;
            }
            QPushButton:hover { background: #374151; }
        """
        )
        layout = QVBoxLayout(dlg)

        name_label = QLabel("Nombre del combo")
        name_input = QLineEdit()
        layout.addWidget(name_label)
        layout.addWidget(name_input)

        price_label = QLabel("Precio de venta")
        price_input = QLineEdit()
        price_input.setPlaceholderText("$0")
        layout.addWidget(price_label)
        layout.addWidget(price_input)
        _fmt_lock = {"busy": False}

        def _format_price_text(txt: str) -> str:
            digits = "".join(ch for ch in (txt or "") if ch.isdigit())
            if not digits:
                return ""
            try:
                val = int(digits)
            except Exception:
                val = 0
            formatted = f"{val:,}".replace(",", ".")
            return f"${formatted}"

        def _on_price_changed(txt: str):
            if _fmt_lock["busy"]:
                return
            _fmt_lock["busy"] = True
            try:
                formatted = _format_price_text(txt)
                if txt != formatted:
                    price_input.setText(formatted)
            finally:
                _fmt_lock["busy"] = False

        price_input.textChanged.connect(_on_price_changed)

        # Atributos del combo (como agregar producto)
        attrs_row = QHBoxLayout()
        attrs_row.setSpacing(10)

        cat_input = QLineEdit()
        cat_input.setPlaceholderText("Categoría")
        cat_input.setText("combo")
        self._enforce_lowercase_input(cat_input)
        attrs_row.addWidget(cat_input)

        material_input = QLineEdit()
        material_input.setPlaceholderText("Material")
        self._enforce_lowercase_input(material_input)
        attrs_row.addWidget(material_input)

        color_input = QLineEdit()
        color_input.setPlaceholderText("Color")
        self._enforce_lowercase_input(color_input)
        attrs_row.addWidget(color_input)

        fabricante_input = QLineEdit()
        fabricante_input.setPlaceholderText("Fabricante")
        self._enforce_lowercase_input(fabricante_input)
        attrs_row.addWidget(fabricante_input)

        medida_input = QComboBox()
        medida_input.setEditable(True)
        medida_input.addItems(["Medida"] + MEDIDAS_ESTANDAR)
        _set_combo_popup_width(medida_input)
        if medida_input.lineEdit():
            self._enforce_lowercase_input(medida_input.lineEdit())
        attrs_row.addWidget(medida_input)

        estado_input = QComboBox()
        estado_input.addItems(ESTADOS)
        _set_combo_popup_width(estado_input)
        attrs_row.addWidget(estado_input)

        codigo_input = QLineEdit()
        codigo_input.setPlaceholderText("Codigo")
        self._enforce_lowercase_input(codigo_input)
        attrs_row.addWidget(codigo_input)

        layout.addLayout(attrs_row)

        filter_row = QHBoxLayout()
        filter_label = QLabel("Buscar")
        filter_input = QLineEdit()
        filter_input.setPlaceholderText(
            "Nombre, categoria, medida, color, fabricante..."
        )
        filter_cat = QComboBox()
        filter_cat.addItem("Todas las categorias")
        cats = sorted(
            {
                (p.get("categoria") or "").strip()
                for p in products
                if (p.get("categoria") or "").strip()
            }
        )
        for c in cats:
            filter_cat.addItem(c)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(filter_input, 1)
        filter_row.addWidget(filter_cat)
        layout.addLayout(filter_row)

        table = QTableWidget()
        table.setStyleSheet(
            """
            QTableWidget {
                background: #0b1220;
                color: #e5e7eb;
                gridline-color: #243447;
                alternate-background-color: #0f172a;
                selection-background-color: #f59e0b;
                selection-color: #111827;
            }
            QTableWidget::item {
                background: #0b1220;
            }
            QTableWidget::item:alternate {
                background: #0f172a;
            }
            QHeaderView::section {
                background: #111827;
                color: #fbbf24;
                border: 1px solid #243447;
                padding: 6px 8px;
                font-weight: 800;
            }
        """
        )
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setShowGrid(True)
        table.setWordWrap(False)
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels(
            [
                "Producto",
                "Categoría",
                "Medida",
                "Color",
                "Fabricante",
                "Estado",
                "Stock",
                "Cantidad en combo",
            ]
        )
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 7):
            table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeToContents
            )
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        table.setRowCount(0)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(26)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setSortingEnabled(False)

        def _apply_combo_filters():
            term = (filter_input.text() or "").strip().lower()
            cat = filter_cat.currentText()
            for row in range(table.rowCount()):
                name = (table.item(row, 0).text() or "").strip().lower()
                categoria = (table.item(row, 1).text() or "").strip().lower()
                medida = (table.item(row, 2).text() or "").strip().lower()
                color = (table.item(row, 3).text() or "").strip().lower()
                fab = (table.item(row, 4).text() or "").strip().lower()
                estado = (table.item(row, 5).text() or "").strip().lower()
                hay = True
                if cat and cat != "Todas las categorias":
                    hay = categoria == cat.lower()
                if hay and term:
                    hay = (
                        term in name
                        or term in categoria
                        or term in medida
                        or term in color
                        or term in fab
                        or term in estado
                    )
                table.setRowHidden(row, not hay)

        def _apply_attr_autofill():
            selected = []
            for r in range(table.rowCount()):
                spin = table.cellWidget(r, 7)
                try:
                    qty = int(spin.value()) if spin else 0
                except Exception:
                    qty = 0
                if qty <= 0:
                    continue
                name_item = table.item(r, 0)
                if not name_item:
                    continue
                attrs = name_item.data(Qt.UserRole + 1) or {}
                selected.append(attrs)
            if not selected:
                return

            def _common(key):
                vals = {str(a.get(key) or "").strip() for a in selected}
                vals.discard("")
                return vals.pop() if len(vals) == 1 else ""

            common_fab = _common("fabricante")
            common_med = _common("medida")
            common_est = _common("estado")
            common_col = _common("color")
            common_mat = _common("material")
            if common_fab and not (fabricante_input.text() or "").strip():
                fabricante_input.setText(common_fab)
            if common_med and (medida_input.currentText() or "").strip() in (
                "",
                "Medida",
            ):
                medida_input.setCurrentText(common_med)
            if common_est and (estado_input.currentText() or "").strip() in (
                "",
                "Nuevo",
            ):
                estado_input.setCurrentText(common_est)
            if common_col and not (color_input.text() or "").strip():
                color_input.setText(common_col)
            if common_mat and not (material_input.text() or "").strip():
                material_input.setText(common_mat)

        for p in products:
            row = table.rowCount()
            table.insertRow(row)
            name_item = QTableWidgetItem(p.get("nombre") or "")
            name_item.setData(Qt.UserRole, int(p.get("id") or 0))
            name_item.setData(
                Qt.UserRole + 1,
                {
                    "categoria": p.get("categoria") or "",
                    "medida": p.get("medida") or "",
                    "color": p.get("color") or "",
                    "fabricante": p.get("fabricante") or "",
                    "estado": p.get("estado") or "",
                    "material": p.get("material") or "",
                },
            )
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(p.get("categoria") or ""))
            table.setItem(row, 2, QTableWidgetItem(p.get("medida") or ""))
            table.setItem(row, 3, QTableWidgetItem(p.get("color") or ""))
            table.setItem(row, 4, QTableWidgetItem(p.get("fabricante") or ""))
            table.setItem(row, 5, QTableWidgetItem(p.get("estado") or ""))
            stock_item = QTableWidgetItem(str(int(p.get("cantidad") or 0)))
            stock_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 6, stock_item)
            qty_spin = QSpinBox()
            qty_spin.setMinimum(0)
            qty_spin.setMaximum(999)
            qty_spin.setFixedHeight(20)
            qty_spin.setFixedWidth(100)
            qty_spin.setAlignment(Qt.AlignCenter)
            qty_spin.setSingleStep(1)
            qty_spin.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
            qty_spin.setAlignment(Qt.AlignCenter)
            qty_spin.lineEdit().setAlignment(Qt.AlignCenter)
            qty_spin.setStyleSheet(
                """
                QSpinBox {
                    background: #111827;
                    color: #e5e7eb;
                    border: 1px solid #374151;
                    border-radius: 6px;
                    padding: 0px 4px;
                    font-weight: 800;
                    min-height: 20px;
                    max-height: 20px;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    subcontrol-origin: border;
                    width: 18px;
                    background: #1f2937;
                    border-left: 1px solid #374151;
                }
                QSpinBox::up-button {
                    subcontrol-position: top right;
                    border-top-right-radius: 6px;
                }
                QSpinBox::down-button {
                    subcontrol-position: bottom right;
                    border-bottom-right-radius: 6px;
                }
                QSpinBox::up-arrow, QSpinBox::down-arrow {
                    width: 10px;
                    height: 10px;
                }
            """
            )
            table.setCellWidget(row, 7, qty_spin)
            qty_spin.valueChanged.connect(lambda _=None: _apply_attr_autofill())
        layout.addWidget(table, 1)
        filter_input.textChanged.connect(_apply_combo_filters)
        filter_cat.currentIndexChanged.connect(_apply_combo_filters)
        _apply_combo_filters()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        create_btn = QPushButton("Crear")
        cancel_btn.setStyleSheet(
            "background:#1f2937; color:#e5e7eb; border-radius:10px; padding:8px 16px; font-weight:800;"
        )
        create_btn.setStyleSheet(
            "background:#22c55e; color:#0b1220; border-radius:10px; padding:8px 16px; font-weight:900;"
        )
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(create_btn)
        layout.addLayout(btn_row)

        def _on_cancel():
            dlg.reject()

        def _on_create():
            nombre = (name_input.text() or "").strip()
            try:
                raw = (price_input.text() or "").strip()
                digits = "".join(ch for ch in raw if ch.isdigit())
                precio = int(digits or 0)
            except Exception:
                precio = 0
            categoria = (cat_input.text() or "").strip()
            material = (material_input.text() or "").strip()
            color = (color_input.text() or "").strip()
            fabricante = (fabricante_input.text() or "").strip()
            medida = (medida_input.currentText() or "").strip()
            estado = (estado_input.currentText() or "").strip()
            codigo = (codigo_input.text() or "").strip()
            items = []
            for row in range(table.rowCount()):
                pid_item = table.item(row, 0)
                if not pid_item:
                    continue
                pid = int(pid_item.data(Qt.UserRole) or 0)
                spin = table.cellWidget(row, 7)
                try:
                    qty = int(spin.value()) if spin else 0
                except Exception:
                    qty = 0
                if pid > 0 and qty > 0:
                    items.append({"producto_id": pid, "cantidad": qty})
            ok, msg, _ = sm.create_combo(
                combo_local,
                nombre,
                precio,
                items,
                usuario=self.username,
                categoria=categoria,
                medida=medida,
                estado=estado,
                color=color,
                fabricante=fabricante,
                material=material,
                codigo=codigo,
            )
            if not ok:
                QMessageBox.warning(self, "Crear combo", msg)
                return
            QMessageBox.information(self, "Crear combo", msg)
            dlg.accept()
            self.load_data()

        cancel_btn.clicked.connect(_on_cancel)
        create_btn.clicked.connect(_on_create)
        dlg.exec_()

    def _open_combo_edit_dialog(self, combo_product: dict):
        if self.role != "admin":
            if not self._can_edit_stock():
                QMessageBox.warning(
                    self,
                    "Editar stock",
                    "Ingresá la contraseña con el botón Editar para editar combos.",
                )
                return
            if self.read_only:
                QMessageBox.warning(
                    self, "Solo lectura", "No puedes editar combos en otro local"
                )
                return
        combo_local = combo_product.get("local") or self.local or self.view_local or ""
        if not combo_local or combo_local in ("Todos", "Todos los locales"):
            try:
                all_locs = sm._get_all_locals_from_db()
                combo_local = all_locs[0] if all_locs else ""
            except Exception:
                combo_local = ""
        combo_id = int(combo_product.get("id") or 0)
        if combo_id <= 0:
            return
        try:
            if self.role == "admin" and self._is_consolidated_view():
                all_locs = sm._get_all_locals_from_db() or [combo_local]
                if combo_local:
                    all_locs = [combo_local] + [
                        loc for loc in all_locs if loc != combo_local
                    ]
                all_products = []
                for loc in all_locs:
                    all_products.extend(
                        sm.get_stock_filtered(loc, "", "", "", apply_reservas=True)
                    )
                seen = set()
                products = []
                for p in all_products:
                    if int(p.get("is_combo") or 0) == 1:
                        continue
                    k = (
                        (p.get("nombre") or "").lower().strip(),
                        (p.get("medida") or "").lower().strip(),
                        (p.get("material") or "").lower().strip(),
                        (p.get("categoria") or "").lower().strip(),
                    )
                    if k not in seen:
                        seen.add(k)
                        products.append(p)
            else:
                products = sm.get_stock_filtered(
                    combo_local, "", "", "", apply_reservas=True
                )
                products = [p for p in products if int(p.get("is_combo") or 0) == 0]
        except Exception:
            products = []

        combo_items_detail = sm.get_combo_items(combo_id)
        existing_items = {
            int(it.get("producto_id") or 0): int(it.get("cantidad") or 0)
            for it in combo_items_detail
        }

        def _combo_attr_key(data: dict) -> tuple:
            return (
                str(data.get("producto_nombre") or data.get("nombre") or "")
                .strip()
                .lower(),
                str(data.get("producto_categoria") or data.get("categoria") or "")
                .strip()
                .lower(),
                str(data.get("producto_medida") or data.get("medida") or "")
                .strip()
                .lower(),
                str(data.get("producto_estado") or data.get("estado") or "")
                .strip()
                .lower(),
                str(data.get("producto_color") or data.get("color") or "")
                .strip()
                .lower(),
                str(data.get("producto_fabricante") or data.get("fabricante") or "")
                .strip()
                .lower(),
            )

        product_attr_keys = set()
        for p in products:
            product_attr_keys.add(
                (
                    str(p.get("nombre") or "").strip().lower(),
                    str(p.get("categoria") or "").strip().lower(),
                    str(p.get("medida") or "").strip().lower(),
                    str(p.get("estado") or "").strip().lower(),
                    str(p.get("color") or "").strip().lower(),
                    str(p.get("fabricante") or "").strip().lower(),
                )
            )

        synthetic_products = []
        for item in combo_items_detail:
            key = _combo_attr_key(item)
            if not any(key) or key in product_attr_keys:
                continue
            synthetic_products.append(
                {
                    "id": int(item.get("producto_id") or 0),
                    "nombre": item.get("producto_nombre") or "",
                    "categoria": item.get("producto_categoria") or "",
                    "medida": item.get("producto_medida") or "",
                    "estado": item.get("producto_estado") or "",
                    "color": item.get("producto_color") or "",
                    "fabricante": item.get("producto_fabricante") or "",
                    "material": "",
                    "cantidad": 0,
                }
            )
            product_attr_keys.add(key)
        if synthetic_products:
            products = synthetic_products + list(products)

        dlg = QDialog(self)
        dlg.setWindowTitle("Editar combo")
        dlg.resize(1200, 700)
        dlg.setMinimumSize(1000, 600)
        dlg.setStyleSheet(
            """
            QDialog { background: #0f172a; }
            QLabel { color: #e5e7eb; font-weight: 700; }
            QLineEdit {
                background: #111827;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px 8px;
                font-weight: 700;
            }
            QSpinBox {
                background: #111827;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 2px 4px;
            }
            QPushButton {
                background: #1f2937;
                color: #e5e7eb;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 800;
            }
            QPushButton:hover { background: #374151; }
        """
        )
        layout = QVBoxLayout(dlg)

        name_label = QLabel("Nombre del combo")
        name_input = QLineEdit()
        name_input.setText(combo_product.get("nombre") or "")
        layout.addWidget(name_label)
        layout.addWidget(name_input)

        price_label = QLabel("Precio de venta")
        price_input = QLineEdit()
        price_input.setPlaceholderText("$0")
        price_input.setText(
            f"${int(combo_product.get('precio_venta') or 0):,}".replace(",", ".")
        )
        layout.addWidget(price_label)
        layout.addWidget(price_input)
        _fmt_lock = {"busy": False}

        def _format_price_text(txt: str) -> str:
            digits = "".join(ch for ch in (txt or "") if ch.isdigit())
            if not digits:
                return ""
            try:
                val = int(digits)
            except Exception:
                val = 0
            formatted = f"{val:,}".replace(",", ".")
            return f"${formatted}"

        def _on_price_changed(txt: str):
            if _fmt_lock["busy"]:
                return
            _fmt_lock["busy"] = True
            try:
                formatted = _format_price_text(txt)
                if txt != formatted:
                    price_input.setText(formatted)
            finally:
                _fmt_lock["busy"] = False

        price_input.textChanged.connect(_on_price_changed)

        # Atributos del combo (como agregar producto)
        attrs_row = QHBoxLayout()
        attrs_row.setSpacing(10)

        cat_input = QLineEdit()
        cat_input.setPlaceholderText("Categoría")
        cat_input.setText(combo_product.get("categoria") or "combo")
        self._enforce_lowercase_input(cat_input)
        attrs_row.addWidget(cat_input)

        material_input = QLineEdit()
        material_input.setPlaceholderText("Material")
        material_input.setText(combo_product.get("material") or "")
        self._enforce_lowercase_input(material_input)
        attrs_row.addWidget(material_input)

        color_input = QLineEdit()
        color_input.setPlaceholderText("Color")
        color_input.setText(combo_product.get("color") or "")
        self._enforce_lowercase_input(color_input)
        attrs_row.addWidget(color_input)

        fabricante_input = QLineEdit()
        fabricante_input.setPlaceholderText("Fabricante")
        fabricante_input.setText(combo_product.get("fabricante") or "")
        self._enforce_lowercase_input(fabricante_input)
        attrs_row.addWidget(fabricante_input)

        medida_input = QComboBox()
        medida_input.setEditable(True)
        medida_input.addItems(["Medida"] + MEDIDAS_ESTANDAR)
        if combo_product.get("medida"):
            medida_input.setCurrentText(combo_product.get("medida") or "")
        _set_combo_popup_width(medida_input)
        if medida_input.lineEdit():
            self._enforce_lowercase_input(medida_input.lineEdit())
        attrs_row.addWidget(medida_input)

        estado_input = QComboBox()
        estado_input.addItems(ESTADOS)
        if combo_product.get("estado"):
            estado_input.setCurrentText(combo_product.get("estado") or "")
        _set_combo_popup_width(estado_input)
        attrs_row.addWidget(estado_input)

        codigo_input = QLineEdit()
        codigo_input.setPlaceholderText("Codigo")
        codigo_input.setText(combo_product.get("codigo") or "")
        self._enforce_lowercase_input(codigo_input)
        attrs_row.addWidget(codigo_input)

        layout.addLayout(attrs_row)

        search_label = QLabel("Buscar producto")
        search_input = QLineEdit()
        search_input.setPlaceholderText(
            "Nombre, categoria, medida, color, fabricante, estado, codigo o material"
        )
        layout.addWidget(search_label)
        layout.addWidget(search_input)

        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels(
            [
                "Producto",
                "Categoría",
                "Medida",
                "Color",
                "Fabricante",
                "Estado",
                "Stock",
                "Cantidad en combo",
            ]
        )
        table.setStyleSheet(
            """
            QTableWidget {
                background: #0b1220;
                color: #e5e7eb;
                gridline-color: #243447;
                alternate-background-color: #0f172a;
                selection-background-color: #f59e0b;
                selection-color: #111827;
            }
            QHeaderView::section {
                background: #111827;
                color: #fbbf24;
                border: 1px solid #243447;
                padding: 6px 8px;
                font-weight: 800;
            }
            """
        )
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 7):
            table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeToContents
            )
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        table.setRowCount(0)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(26)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setSortingEnabled(False)
        qty_state = {int(pid): int(qty) for pid, qty in existing_items.items()}

        def _combo_item_attr_key(data: dict) -> tuple:
            return (
                str(data.get("producto_nombre") or data.get("nombre") or "")
                .strip()
                .lower(),
                str(data.get("producto_categoria") or data.get("categoria") or "")
                .strip()
                .lower(),
                str(data.get("producto_medida") or data.get("medida") or "")
                .strip()
                .lower(),
                str(data.get("producto_estado") or data.get("estado") or "")
                .strip()
                .lower(),
                str(data.get("producto_color") or data.get("color") or "")
                .strip()
                .lower(),
                str(data.get("producto_fabricante") or data.get("fabricante") or "")
                .strip()
                .lower(),
            )

        qty_state_by_attr = {}
        for item in sm.get_combo_items(combo_id):
            try:
                qty = int(item.get("cantidad") or 0)
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            key = _combo_item_attr_key(item)
            if any(key):
                qty_state_by_attr[key] = qty

        def _product_search_text(prod: dict) -> str:
            parts = [
                prod.get("nombre") or "",
                prod.get("categoria") or "",
                prod.get("medida") or "",
                prod.get("color") or "",
                prod.get("fabricante") or "",
                prod.get("estado") or "",
                prod.get("codigo") or "",
                prod.get("material") or "",
            ]
            return " ".join(
                str(p).strip().lower() for p in parts if str(p or "").strip()
            )

        def _product_attr_key(prod: dict) -> tuple:
            return (
                str(prod.get("nombre") or "").strip().lower(),
                str(prod.get("categoria") or "").strip().lower(),
                str(prod.get("medida") or "").strip().lower(),
                str(prod.get("estado") or "").strip().lower(),
                str(prod.get("color") or "").strip().lower(),
                str(prod.get("fabricante") or "").strip().lower(),
            )

        def _product_sort_key(prod: dict):
            try:
                stock = int(prod.get("cantidad") or 0)
            except Exception:
                stock = 0
            has_stock = 1 if stock != 0 else 0
            pid = int(prod.get("id") or 0)
            selected_qty = int(qty_state.get(pid, 0) or 0)
            if selected_qty <= 0:
                selected_qty = int(
                    qty_state_by_attr.get(_product_attr_key(prod), 0) or 0
                )
            selected = 1 if selected_qty > 0 else 0
            return (
                -selected,
                -has_stock,
                -(stock if stock > 0 else 0),
                str(prod.get("nombre") or "").lower(),
            )

        def _apply_attr_autofill():
            selected = []
            for r in range(table.rowCount()):
                spin = table.cellWidget(r, 7)
                try:
                    qty = int(spin.value()) if spin else 0
                except Exception:
                    qty = 0
                if qty <= 0:
                    continue
                name_item = table.item(r, 0)
                if not name_item:
                    continue
                attrs = name_item.data(Qt.UserRole + 1) or {}
                selected.append(attrs)
            if not selected:
                return

            def _common(key):
                vals = {str(a.get(key) or "").strip() for a in selected}
                vals.discard("")
                return vals.pop() if len(vals) == 1 else ""

            common_fab = _common("fabricante")
            common_med = _common("medida")
            common_est = _common("estado")
            common_col = _common("color")
            common_mat = _common("material")
            if common_fab and not (fabricante_input.text() or "").strip():
                fabricante_input.setText(common_fab)
            if common_med and (medida_input.currentText() or "").strip() in (
                "",
                "Medida",
            ):
                medida_input.setCurrentText(common_med)
            if common_est and (estado_input.currentText() or "").strip() in (
                "",
                "Nuevo",
            ):
                estado_input.setCurrentText(common_est)
            if common_col and not (color_input.text() or "").strip():
                color_input.setText(common_col)
            if common_mat and not (material_input.text() or "").strip():
                material_input.setText(common_mat)

        products = sorted(products, key=_product_sort_key)

        def _populate_products_table():
            table.setRowCount(0)
            for p in products:
                row = table.rowCount()
                table.insertRow(row)
                name_item = QTableWidgetItem(p.get("nombre") or "")
                name_item.setData(Qt.UserRole, int(p.get("id") or 0))
                name_item.setData(
                    Qt.UserRole + 1,
                    {
                        "categoria": p.get("categoria") or "",
                        "medida": p.get("medida") or "",
                        "color": p.get("color") or "",
                        "fabricante": p.get("fabricante") or "",
                        "estado": p.get("estado") or "",
                        "material": p.get("material") or "",
                    },
                )
                name_item.setData(Qt.UserRole + 2, _product_search_text(p))
                table.setItem(row, 0, name_item)
                table.setItem(row, 1, QTableWidgetItem(p.get("categoria") or ""))
                table.setItem(row, 2, QTableWidgetItem(p.get("medida") or ""))
                table.setItem(row, 3, QTableWidgetItem(p.get("color") or ""))
                table.setItem(row, 4, QTableWidgetItem(p.get("fabricante") or ""))
                table.setItem(row, 5, QTableWidgetItem(p.get("estado") or ""))
                stock_item = QTableWidgetItem(str(int(p.get("cantidad") or 0)))
                stock_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 6, stock_item)
                qty_spin = QSpinBox()
                qty_spin.setMinimum(0)
                qty_spin.setMaximum(999)
                qty_spin.setFixedHeight(20)
                qty_spin.setFixedWidth(100)
                qty_spin.setAlignment(Qt.AlignCenter)
                qty_spin.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
                pid = int(p.get("id") or 0)
                initial_qty = int(qty_state.get(pid, 0) or 0)
                if initial_qty <= 0:
                    initial_qty = int(
                        qty_state_by_attr.get(_product_attr_key(p), 0) or 0
                    )
                    if initial_qty > 0 and pid > 0:
                        qty_state[pid] = initial_qty
                qty_spin.setValue(initial_qty)
                try:
                    qty_spin.lineEdit().setAlignment(Qt.AlignCenter)
                except Exception:
                    pass
                qty_spin.setStyleSheet(
                    """
                    QSpinBox {
                        background: #111827;
                        color: #e5e7eb;
                        border: 1px solid #374151;
                        border-radius: 6px;
                        padding: 0px 4px;
                        font-weight: 800;
                        min-height: 20px;
                        max-height: 20px;
                    }
                    QSpinBox::up-button, QSpinBox::down-button {
                        subcontrol-origin: border;
                        width: 18px;
                        background: #1f2937;
                        border-left: 1px solid #374151;
                    }
                    QSpinBox::up-button {
                        subcontrol-position: top right;
                        border-top-right-radius: 6px;
                    }
                    QSpinBox::down-button {
                        subcontrol-position: bottom right;
                        border-bottom-right-radius: 6px;
                    }
                    QSpinBox::up-arrow, QSpinBox::down-arrow {
                        width: 10px;
                        height: 10px;
                    }
                """
                )
                qty_spin.valueChanged.connect(
                    lambda value, pid=pid: qty_state.__setitem__(pid, int(value))
                )
                qty_spin.valueChanged.connect(lambda _=None: _apply_attr_autofill())
                table.setCellWidget(row, 7, qty_spin)

        def _apply_product_filters():
            query = (search_input.text() or "").strip().lower()
            for row in range(table.rowCount()):
                name_item = table.item(row, 0)
                haystack = ""
                try:
                    haystack = (
                        str(name_item.data(Qt.UserRole + 2) or "").strip().lower()
                        if name_item
                        else ""
                    )
                except Exception:
                    haystack = ""
                visible = True
                if query:
                    visible = query in haystack
                table.setRowHidden(row, not visible)

        _populate_products_table()
        _apply_product_filters()
        search_input.textChanged.connect(lambda _=None: _apply_product_filters())
        layout.addWidget(table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        save_btn = QPushButton("Guardar cambios")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        def _on_cancel():
            dlg.reject()

        def _on_save():
            nombre = (name_input.text() or "").strip()
            try:
                raw = (price_input.text() or "").strip()
                digits = "".join(ch for ch in raw if ch.isdigit())
                precio = int(digits or 0)
            except Exception:
                precio = 0
            categoria = (cat_input.text() or "").strip()
            material = (material_input.text() or "").strip()
            color = (color_input.text() or "").strip()
            fabricante = (fabricante_input.text() or "").strip()
            medida = (medida_input.currentText() or "").strip()
            estado = (estado_input.currentText() or "").strip()
            codigo = (codigo_input.text() or "").strip()
            items = []
            for pid, qty in qty_state.items():
                if pid > 0 and qty > 0:
                    items.append({"producto_id": pid, "cantidad": qty})
            ok, msg = sm.update_combo(
                combo_id,
                combo_local,
                nombre,
                precio,
                items,
                usuario=self.username,
                categoria=categoria,
                medida=medida,
                estado=estado,
                color=color,
                fabricante=fabricante,
                material=material,
                codigo=codigo,
            )
            if not ok:
                QMessageBox.warning(self, "Editar combo", msg)
                return
            QMessageBox.information(self, "Editar combo", msg)
            dlg.accept()
            self.load_data()

        cancel_btn.clicked.connect(_on_cancel)
        save_btn.clicked.connect(_on_save)
        dlg.exec_()

    def _reset_edit_timeout(self):
        # Desactivado: la edición solo se corta manualmente con el botón.
        return

    def _auto_lock_edit_mode(self):
        try:
            if self.role == "admin":
                return
            if self._edit_unlocked:
                self._edit_unlocked = False
                self._update_edit_button()
                self._apply_edit_lock_state()
        except Exception:
            pass

    def _update_edit_button(self):
        if not hasattr(self, "edit_btn"):
            return
        if self.role == "admin":
            if self._can_add_product():
                self.edit_btn.setText("Ocultar agregar")
                self.edit_btn.setToolTip("Ocultar formulario para agregar productos")
            else:
                self.edit_btn.setText("Agregar producto")
                self.edit_btn.setToolTip("Mostrar formulario para agregar productos")
            return
        if self._can_edit_stock():
            self.edit_btn.setText("Dejar de editar")
        else:
            self.edit_btn.setText("Editar")

    def _trigger_combo_sync(self):
        try:
            if getattr(self, "role", "") == "admin":
                return
            if self._combo_sync_running:
                return
            if self._is_consolidated_view():
                return
            local = (self.view_local or self.local or "").strip()
            if not local or local in ("Todos", "Todos los locales"):
                return
            now = time.time()
            if (
                self._combo_sync_last_local == local
                and (now - float(self._combo_sync_last_ts or 0)) < 60
            ):
                return
            self._combo_sync_running = True
            self._combo_sync_last_local = local
            self._combo_sync_last_ts = now
            self._combo_sync_thread = ComboSyncThread(local, parent=self)
            self._combo_sync_thread.done.connect(self._on_combo_sync_done)
            self._combo_sync_thread.start()
        except Exception:
            self._combo_sync_running = False

    def _on_combo_sync_done(self, local: str, ok: bool):
        try:
            self._combo_sync_running = False
            if ok:
                # refrescar sin bloquear
                self._schedule_reload(50)
        except Exception:
            pass

    def _update_product_field(self, product_id, field, value):
        """Actualiza un campo del producto en el cache local y la tabla"""
        try:
            if product_id in self._products_by_id:
                self._products_by_id[product_id][field] = value
                row = self._row_by_product_id.get(product_id, -1)
                if row >= 0:
                    if self._is_consolidated_view():
                        col_map = {
                            "nombre": 0,
                            "material": 1,
                            "categoria": 2,
                            "fabricante": 3,
                            "color": 4,
                            "medida": 5,
                            "estado": 6,
                            "precio_venta": 7,
                            "codigo": 8,
                        }
                    else:
                        col_map = {
                            "nombre": 0,
                            "material": 1,
                            "categoria": 2,
                            "medida": 3,
                            "fabricante": 4,
                            "color": 5,
                            "estado": 6,
                            "cantidad": 7,
                            "precio_venta": 8,
                        }
                    col = col_map.get(field)
                    if col is not None and col < self.table.columnCount():
                        item = self.table.item(row, col)
                        if item and field in ("precio_venta", "precio_costo"):
                            item.setText(f"${value:,.0f}".replace(",", "."))
                            item.setTextAlignment(
                                Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter
                            )
                        else:
                            item.setText(
                                str(value) if value not in (None, "-") else "-"
                            )
        except Exception as e:
            logger.error(f"Error updating product field: {e}")

    def _merge_exact_duplicates(self, local: str) -> int:
        """Fusiona duplicados exactos en un local y recarga si hay cambios."""
        try:
            local = (local or "").strip()
            if not local or local in ("Todos", "Todos los locales"):
                return 0
            merged = sm.merge_duplicate_products(local)
            if merged:
                try:
                    self._show_toast(f"OK Productos fusionados: {merged}")
                except Exception:
                    pass
                self._schedule_reload(200)
            return merged
        except Exception as e:
            logger.error(f"Error fusionando duplicados exactos: {e}")
            return 0

    def on_cell_double_clicked(self, item):
        """Maneja doble click en celdas para editar campos con popups personalizados"""
        try:
            if item is None:
                return
            if not self._can_edit_stock():
                self._show_toast(
                    "???? Ingresa la contraseOKa con el botOKn Editar para modificar stock.",
                    persistent=True,
                )
                return
            if self.read_only and not self._is_consolidated_view():
                self._show_toast(
                    "???? No puedes editar el stock de otro local", persistent=True
                )
                return

            row = item.row()
            col = item.column()
            if self._is_consolidated_view():
                if col >= getattr(self, "_consolidated_local_col_start", 10):
                    # No permitir editar cantidades con doble click
                    return
                id_item = self.table.item(row, 0)
                if not id_item:
                    return
                product_id = id_item.data(Qt.ItemDataRole.UserRole)
                if not product_id:
                    return
                product = self._products_by_id.get(product_id) or {}
                if not product:
                    return
                if int(product.get("is_combo") or 0) == 1:
                    self._open_combo_edit_dialog(product)
                    return
                # en vista consolidada aplicamos el cambio segun columna
                local_override = ""
                if col == 0:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_name(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 1:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_material(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 2:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_category(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 3:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_fabricante(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 4:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_color(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 5:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_medida(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 6:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    locales_qty = meta.get("locales", {}) or {}
                    local_name = self._pick_consolidated_local(
                        ids_by_local, locales_qty
                    )
                    if not local_name:
                        return
                    pid = ids_by_local.get(local_name)
                    if not pid:
                        return
                    prod_local = self._products_by_id.get(pid) or product
                    self._edit_estado(pid, prod_local, local_override=local_name)
                elif col == 7:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_precio(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                elif col == 8:
                    meta = (
                        self._consolidated_meta.get(row, {})
                        if hasattr(self, "_consolidated_meta")
                        else {}
                    )
                    ids_by_local = meta.get("ids_by_local", {}) or {}
                    self._edit_codigo(
                        product_id,
                        product,
                        local_override=local_override,
                        ids_by_local=ids_by_local,
                    )
                return

            id_item = self.table.item(row, 0)
            if not id_item:
                return
            product_id = id_item.data(Qt.ItemDataRole.UserRole)
            if not product_id:
                return
            product = self._products_by_id.get(product_id) or {}
            if not product:
                return
            if int(product.get("is_combo") or 0) == 1:
                self._open_combo_edit_dialog(product)
                return

            if col == 0:
                self._edit_name(product_id, product)
            elif col == 1:
                self._edit_material(product_id, product)
            elif col == 2:
                self._edit_category(product_id, product)
            elif col == 4:
                self._edit_fabricante(product_id, product)
            elif col == 5:
                self._edit_color(product_id, product)
            elif col == 3:
                self._edit_medida(product_id, product)
            elif col == 6:
                self._edit_estado(product_id, product)
            elif col == 8:
                self._edit_precio(product_id, product)
        except Exception as e:
            logger.error(f"Error en on_cell_double_clicked: {e}")
            self._show_toast(f"OK Error: {str(e)}", persistent=True)

    def _pick_consolidated_local(self, ids_by_local: dict, locales_qty=None) -> str:
        """Selecciona un local cuando la vista está consolidada."""
        try:
            locals_list = [l for l in (ids_by_local or {}).keys() if l]
            if not locals_list:
                return ""
            if len(locals_list) == 1:
                return locals_list[0]
            items = []
            label_to_local = {}
            for loc in sorted(locals_list):
                qty = 0
                try:
                    qty = int((locales_qty or {}).get(loc, 0) or 0)
                except Exception:
                    qty = 0
                label = f"{loc} ({qty})"
                items.append(label)
                label_to_local[label] = loc
            choice, ok = QInputDialog.getItem(
                self, "Seleccionar local", "Local:", items, 0, False
            )
            if not ok or not choice:
                return ""
            return label_to_local.get(choice, "")
        except Exception:
            return ""

    def _edit_consolidated_qty(self, row: int, col: int, local_name: str):
        item = self.table.item(row, col)
        if not item:
            return
        product_id = item.data(Qt.ItemDataRole.UserRole)
        if not product_id:
            return
        product = self._products_by_id.get(product_id) or {}
        if not product:
            return
        if int(product.get("is_combo") or 0) == 1:
            QMessageBox.information(
                self, "Combo", "Los combos no se ajustan manualmente."
            )
            return
        try:
            current = int(item.text())
        except Exception:
            current = 0

        delta, ok = QInputDialog.getInt(
            self,
            "Modificar Cantidad",
            f"Cantidad a sumar/restar en {local_name} (actual: {current}):",
            0,
            -current,
            9999,
        )
        if not ok or delta == 0:
            return

        payload = {
            "producto_id": product_id,
            "delta": delta,
            "usuario": self.username,
            "local": local_name,
            "detalle": "ajuste manual (admin todos los locales)",
            "nombre": product.get("nombre") or "",
            "categoria": product.get("categoria") or "",
            "medida": product.get("medida") or "",
            "estado": product.get("estado") or "Nuevo",
        }
        ok, msg = qa.execute_increment(payload)
        if ok:
            self._show_toast("OK Cantidad actualizada")
            self._schedule_reload(200)
        else:
            self._show_toast(f"Error {msg}", persistent=True)

    def _edit_codigo(self, product_id, product, local_override=None, ids_by_local=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Código")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Nuevo código del producto:"))
        codigo_input = QLineEdit(dialog)
        codigo_input.setText(product.get("codigo", "") or "")
        layout.addWidget(codigo_input)

        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save():
            new_codigo = (codigo_input.text() or "").strip()
            if not new_codigo:
                new_codigo = None
            old_value = product.get("codigo", "")

            if ids_by_local:
                ok_any = False
                errors = []
                for loc, pid in ids_by_local.items():
                    if not pid:
                        continue
                    payload = {
                        "producto_id": pid,
                        "field": "codigo",
                        "value": new_codigo,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    self._show_toast("✅ Código actualizado en todos los locales")
                    self._schedule_reload(200)
                else:
                    self._show_toast(
                        f"❌ Error actualizando código: {errors[0] if errors else 'No se pudo actualizar'}",
                        persistent=True,
                    )
                dialog.accept()
                return

            target_local = (local_override or self.local) or ""
            self._update_product_field(product_id, "codigo", new_codigo)
            payload = {
                "producto_id": product_id,
                "field": "codigo",
                "value": new_codigo,
                "usuario": self.username,
                "local": target_local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("✅ Código actualizado")
                self._merge_exact_duplicates(target_local)
            else:
                self._update_product_field(product_id, "codigo", old_value)
                self._show_toast(f"❌ Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_name(self, product_id, product, local_override=None, ids_by_local=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Nombre")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Nuevo nombre del producto:"))
        name_input = QLineEdit(dialog)
        name_input.setText(product.get("nombre", "") or "")
        self._enforce_lowercase_input(name_input)
        layout.addWidget(name_input)

        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save():
            new_name = (name_input.text() or "").strip()
            if len(new_name) < 2:
                self._show_toast("Error Minimo 2 caracteres", persistent=True)
                return
            new_name = new_name.lower()
            old_value = product.get("nombre", "")

            if ids_by_local:
                ok_any = False
                errors = []
                for loc, pid in ids_by_local.items():
                    if not pid:
                        continue
                    payload = {
                        "producto_id": pid,
                        "field": "nombre",
                        "value": new_name,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    self._show_toast("OK Nombre actualizado en todos los locales")
                    for loc in ids_by_local.keys():
                        self._merge_exact_duplicates(loc)
                    self._schedule_reload(200)
                else:
                    err = errors[0] if errors else "No se pudo actualizar"
                    self._show_toast(f"Error {err}", persistent=True)
                dialog.accept()
                return

            target_local = (local_override or self.local) or ""
            self._update_product_field(product_id, "nombre", new_name)
            payload = {
                "producto_id": product_id,
                "field": "nombre",
                "value": new_name,
                "usuario": self.username,
                "local": target_local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("OK Nombre actualizado")
                self._merge_exact_duplicates(target_local)
            else:
                self._update_product_field(product_id, "nombre", old_value)
                self._show_toast(f"Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_category(
        self, product_id, product, local_override=None, ids_by_local=None
    ):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Categoria")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Selecciona una categoria o agrega una personalizada:"))

        combo = QComboBox(dialog)
        categories = self.categories_cache or sorted(set(sm.get_all_tipos()))
        combo.addItems(categories)
        combo.setEditable(True)
        combo.setCurrentText(product.get("categoria", "") or "")
        if combo.lineEdit():
            self._enforce_lowercase_input(combo.lineEdit())
        layout.addWidget(combo)

        layout.addWidget(QLabel("Categoria personalizada (opcional):"))
        custom_input = QLineEdit(dialog)
        self._enforce_lowercase_input(custom_input)
        layout.addWidget(custom_input)

        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save_cat():
            raw_value = (
                custom_input.text() or ""
            ).strip() or combo.currentText().strip()
            if not raw_value:
                self._show_toast(
                    "Error La categoria no puede estar vacia", persistent=True
                )
                return
            new_category = sm._norm_cat(raw_value)
            old_value = product.get("categoria", "")

            if new_category not in self.categories_cache:
                # Permitir categoría nueva también en vista consolidada.
                # Intentar registrarla en la base y actualizar cache local.
                try:
                    sm.insert_tipo_si_no_existe(new_category)
                except Exception:
                    pass
                try:
                    self.categories_cache.append(new_category)
                    # Normalizar y mantener orden.
                    self.categories_cache = sorted(
                        {
                            (c or "").strip().lower()
                            for c in self.categories_cache
                            if (c or "").strip()
                        }
                    )
                    self.update_categories(self.categories_cache)
                except Exception:
                    pass

            # if we have a set of ids by local it means we are in consolidated
            # view and we should apply the change to every local separately so
            # the category is stored on each row and duplicates can be merged.
            if ids_by_local:
                ok_any = False
                errors = []
                for loc, pid in ids_by_local.items():
                    if not pid:
                        continue
                    payload = {
                        "producto_id": pid,
                        "field": "categoria",
                        "value": new_category,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    self._show_toast("OK Categoria actualizada en todos los locales")
                    for loc in ids_by_local.keys():
                        self._merge_exact_duplicates(loc)
                    self._schedule_reload(200)
                else:
                    err = errors[0] if errors else "No se pudo actualizar"
                    self._show_toast(f"Error {err}", persistent=True)
                dialog.accept()
                return

            # single-local behaviour (or when not in consolidated view)
            self._update_product_field(product_id, "categoria", new_category)
            payload = {
                "producto_id": product_id,
                "field": "categoria",
                "value": new_category,
                "usuario": self.username,
                "local": local_override or self.local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("OK Categoria actualizada")
                self._merge_exact_duplicates(local_override or self.local)
            else:
                self._update_product_field(product_id, "categoria", old_value)
                self._show_toast(f"Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save_cat)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_fabricante(
        self, product_id, product, local_override=None, ids_by_local=None
    ):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Fabricante")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Nuevo fabricante (opcional):"))

        fab_input = QLineEdit(dialog)
        fab_input.setText(
            ""
            if (product.get("fabricante") in (None, "-", ""))
            else str(product.get("fabricante"))
        )
        self._enforce_lowercase_input(fab_input)
        layout.addWidget(fab_input)
        # aclaración para el usuario
        note = QLabel("(Este cambio se aplicará a todos los locales)")
        note.setStyleSheet("font-size:10px; color:#888888;")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save():
            new_fab = (fab_input.text() or "").strip()
            new_fab = new_fab.lower()
            display_value = new_fab or "-"
            old_value = product.get("fabricante", "")
            if ids_by_local:
                ok_any = False
                errors = []
                for loc, pid in ids_by_local.items():
                    if not pid:
                        continue
                    payload = {
                        "producto_id": pid,
                        "field": "fabricante",
                        "value": new_fab,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    self._show_toast("OK Fabricante actualizado en todos los locales")
                    for loc in ids_by_local.keys():
                        self._merge_exact_duplicates(loc)
                    self._schedule_reload(200)
                else:
                    err = errors[0] if errors else "No se pudo actualizar"
                    self._show_toast(f"Error {err}", persistent=True)
                dialog.accept()
                return

            self._update_product_field(product_id, "fabricante", display_value)
            payload = {
                "producto_id": product_id,
                "field": "fabricante",
                "value": new_fab,
                "usuario": self.username,
                "local": local_override or self.local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("OK Fabricante actualizado")
                self._merge_exact_duplicates(local_override or self.local)
            else:
                self._update_product_field(product_id, "fabricante", old_value)
                self._show_toast(f"Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_color(self, product_id, product, local_override=None, ids_by_local=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Color")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Nuevo color (opcional):"))

        color_input = QLineEdit(dialog)
        color_input.setText(
            ""
            if (product.get("color") in (None, "-", ""))
            else str(product.get("color"))
        )
        self._enforce_lowercase_input(color_input)
        layout.addWidget(color_input)
        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save():
            new_color = (color_input.text() or "").strip()
            new_color = new_color.lower()
            display_value = new_color or "-"
            old_value = product.get("color", "")
            if ids_by_local:
                ok_any = False
                errors = []
                for loc, pid in ids_by_local.items():
                    if not pid:
                        continue
                    payload = {
                        "producto_id": pid,
                        "field": "color",
                        "value": new_color,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    self._show_toast("OK Color actualizado en todos los locales")
                    for loc in ids_by_local.keys():
                        self._merge_exact_duplicates(loc)
                    self._schedule_reload(200)
                else:
                    err = errors[0] if errors else "No se pudo actualizar"
                    self._show_toast(f"Error {err}", persistent=True)
                dialog.accept()
                return

            self._update_product_field(product_id, "color", display_value)
            payload = {
                "producto_id": product_id,
                "field": "color",
                "value": new_color,
                "usuario": self.username,
                "local": local_override or self.local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("OK Color actualizado")
                self._merge_exact_duplicates(local_override or self.local)
            else:
                self._update_product_field(product_id, "color", old_value)
                self._show_toast(f"Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_material(
        self, product_id, product, local_override=None, ids_by_local=None
    ):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Material")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Nuevo material (opcional):"))

        material_input = QLineEdit(dialog)
        material_input.setText(
            ""
            if (product.get("material") in (None, "-", ""))
            else str(product.get("material"))
        )
        self._enforce_lowercase_input(material_input)
        layout.addWidget(material_input)
        bulk_checkbox = None
        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)
            if self._is_consolidated_view():
                bulk_checkbox = QCheckBox(
                    "Aplicar a todas las filas con mismo nombre/categoría/fabricante"
                )
                bulk_checkbox.setChecked(True)
                bulk_checkbox.setStyleSheet("color:#e5e7eb;font-weight:700;")
                layout.addWidget(bulk_checkbox)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save():
            new_material = (material_input.text() or "").strip()
            new_material = new_material.lower()
            display_value = new_material or "-"
            old_value = product.get("material", "")
            if ids_by_local:
                target_ids = set()
                if bulk_checkbox is not None and bulk_checkbox.isChecked():

                    def _norm(s):
                        return (s or "").strip().lower()

                    key_nombre = _norm(product.get("nombre"))
                    key_categoria = _norm(product.get("categoria"))
                    key_fabricante = _norm(product.get("fabricante"))
                    for p in (self._products_by_id or {}).values():
                        if _norm(p.get("nombre")) != key_nombre:
                            continue
                        if _norm(p.get("categoria")) != key_categoria:
                            continue
                        if _norm(p.get("fabricante")) != key_fabricante:
                            continue
                        pid = p.get("id")
                        if pid:
                            target_ids.add(int(pid))
                else:
                    for loc, pid in ids_by_local.items():
                        if pid:
                            target_ids.add(int(pid))

                ok_any = False
                errors = []
                touched_locals = set()
                for pid in target_ids:
                    p = self._products_by_id.get(pid) or {}
                    loc = p.get("local") or ""
                    payload = {
                        "producto_id": pid,
                        "field": "material",
                        "value": new_material,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                        touched_locals.add(loc)
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    msg = "OK Material actualizado en todos los locales"
                    if bulk_checkbox is not None and bulk_checkbox.isChecked():
                        msg = f"OK Material actualizado en {len(target_ids)} productos"
                    self._show_toast(msg)
                    for loc in touched_locals:
                        self._merge_exact_duplicates(loc)
                    self._schedule_reload(200)
                else:
                    err = errors[0] if errors else "No se pudo actualizar"
                    self._show_toast(f"Error {err}", persistent=True)
                dialog.accept()
                return

            self._update_product_field(product_id, "material", display_value)
            payload = {
                "producto_id": product_id,
                "field": "material",
                "value": new_material,
                "usuario": self.username,
                "local": local_override or self.local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("OK Material actualizado")
                self._merge_exact_duplicates(local_override or self.local)
            else:
                self._update_product_field(product_id, "material", old_value)
                self._show_toast(f"Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_medida(self, product_id, product, local_override=None, ids_by_local=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Medida")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Selecciona una medida:"))

        nombre_prod = (product.get("nombre") or "").lower()
        current_medida = (product.get("medida") or "").strip()
        if current_medida in PULGADAS:
            medidas_disponibles = PULGADAS
        elif self._is_litros_medida(current_medida):
            medidas_disponibles = self._sort_medidas(list(LITROS) + [current_medida])
        elif any(k in nombre_prod for k in CAMA_KEYWORDS):
            medidas_disponibles = PLAZAS
        elif any(k in nombre_prod for k in BICI_KEYWORDS):
            medidas_disponibles = RODADOS
        else:
            medidas_disponibles = MEDIDAS_ESTANDAR

        combo = QComboBox(dialog)
        combo.addItems(medidas_disponibles)
        combo.setEditable(True)
        if current_medida:
            combo.setCurrentText(current_medida)
        if combo.lineEdit():
            self._enforce_lowercase_input(combo.lineEdit())
        layout.addWidget(combo)
        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save_med():
            new_medida = (combo.currentText() or "").strip()
            new_medida = new_medida.lower()
            if not new_medida:
                self._show_toast(
                    "Error La medida no puede estar vacía", persistent=True
                )
                return
            new_medida_norm = sm._norm_medida(new_medida)
            old_value = product.get("medida", "")

            if ids_by_local:
                ok_any = False
                errors = []
                for loc, pid in ids_by_local.items():
                    if not pid:
                        continue
                    payload = {
                        "producto_id": pid,
                        "field": "medida",
                        "value": new_medida_norm,
                        "usuario": self.username,
                        "local": loc,
                        "motivo": "edicion manual",
                    }
                    ok, msg = qa.execute_update_field(payload)
                    if ok:
                        ok_any = True
                    else:
                        errors.append(msg)
                if ok_any:
                    self._reset_edit_timeout()
                    self._show_toast("OK Medida actualizada en todos los locales")
                    for loc in ids_by_local.keys():
                        self._merge_exact_duplicates(loc)
                    self._schedule_reload(200)
                else:
                    err = errors[0] if errors else "No se pudo actualizar"
                    self._show_toast(f"Error {err}", persistent=True)
                dialog.accept()
                return

            self._update_product_field(product_id, "medida", new_medida_norm)
            payload = {
                "producto_id": product_id,
                "field": "medida",
                "value": new_medida_norm,
                "usuario": self.username,
                "local": local_override or self.local,
                "motivo": "edicion manual",
            }
            ok, msg = qa.execute_update_field(payload)
            if ok:
                self._reset_edit_timeout()
                self._show_toast("OK Medida actualizada")
                self._merge_exact_duplicates(local_override or self.local)
            else:
                self._update_product_field(product_id, "medida", old_value)
                self._show_toast(f"Error {msg}", persistent=True)
            dialog.accept()

        save_btn.clicked.connect(on_save_med)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_precio_costo(
        self, product_id, product, local_override=None, ids_by_local=None
    ):
        current_cost = int(product.get("precio_costo") or 0)
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Precio Costo")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Precio costo actual: ${current_cost:,.0f}"))
        layout.addWidget(QLabel("Nuevo costo (ej: 12000, +10%, -500):"))

        cost_input = QLineEdit(dialog)
        layout.addWidget(cost_input)
        preview_label = QLabel("")
        preview_label.setStyleSheet("color: #10B981; font-weight: bold;")
        layout.addWidget(preview_label)
        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        def _is_allowed(txt: str) -> bool:
            return bool(re.fullmatch(r"[0-9+\\-\\.\\s%]+", txt.strip()))

        def _format_if_numeric(txt: str):
            stripped = txt.strip()
            if not stripped:
                return stripped
            if re.fullmatch(r"[0-9.]+", stripped):
                raw = re.sub(r"[^0-9]", "", stripped)
                if not raw:
                    return stripped
                return f"{int(raw):,}".replace(",", ".")
            return stripped

        def update_preview():
            txt = cost_input.text().strip()
            if not txt:
                preview_label.setText("")
                return
            if not _is_allowed(txt):
                preview_label.setStyleSheet("color: #ef4444; font-weight: bold;")
                preview_label.setText("❌ Solo números, +, - o %")
                return
            try:
                new_cost_val = sm._compute_new_price_from_str(current_cost, txt)
                if new_cost_val < 0:
                    raise ValueError("neg")
                delta = new_cost_val - current_cost
                preview_label.setText(
                    f"Nuevo costo: ${new_cost_val:,.0f} ({delta:+,.0f})"
                )
                preview_label.setStyleSheet("color: #10B981; font-weight: bold;")
            except Exception:
                preview_label.setStyleSheet("color: #ef4444; font-weight: bold;")
                preview_label.setText("❌ Formato inválido")

        def on_cost_changed(text):
            formatted = _format_if_numeric(text)
            if formatted != text:
                cursor = cost_input.cursorPosition()
                cost_input.blockSignals(True)
                cost_input.setText(formatted)
                cost_input.blockSignals(False)
                cost_input.setCursorPosition(
                    min(cursor + (len(formatted) - len(text)), len(formatted))
                )
            update_preview()

        cost_input.textChanged.connect(on_cost_changed)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Aplicar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save_cost():
            txt = cost_input.text().strip()
            if not txt:
                self._show_toast("❌ Ingresa un costo válido", persistent=True)
                return
            if not _is_allowed(txt):
                self._show_toast("❌ Formato inválido", persistent=True)
                return
            try:
                new_cost_val = sm._compute_new_price_from_str(current_cost, txt)
                if new_cost_val < 0:
                    self._show_toast(
                        "❌ El costo no puede ser negativo", persistent=True
                    )
                    return
                old_value = product.get("precio_costo", 0)
                if ids_by_local:
                    ok_any = False
                    errors = []
                    for loc, pid in ids_by_local.items():
                        if not pid:
                            continue
                        self._update_product_field(pid, "precio_costo", new_cost_val)
                        payload = {
                            "producto_id": pid,
                            "field": "precio_costo",
                            "value": new_cost_val,
                            "usuario": self.username,
                            "local": loc,
                            "motivo": "edición manual",
                        }
                        ok, msg = qa.execute_update_field(payload)
                        if ok:
                            ok_any = True
                        else:
                            errors.append(msg)
                    if ok_any:
                        self._reset_edit_timeout()
                        self._show_toast("✅ Costo actualizado en todos los locales")
                        for loc in ids_by_local.keys():
                            self._merge_exact_duplicates(loc)
                        self._schedule_reload(200)
                    else:
                        err = errors[0] if errors else "No se pudo actualizar"
                        self._show_toast(f"❌ {err}", persistent=True)
                    dialog.accept()
                    return

                self._update_product_field(product_id, "precio_costo", new_cost_val)
                payload = {
                    "producto_id": product_id,
                    "field": "precio_costo",
                    "value": new_cost_val,
                    "usuario": self.username,
                    "local": local_override or self.local,
                    "motivo": "edición manual",
                }
                ok, msg = qa.execute_update_field(payload)
                if ok:
                    self._reset_edit_timeout()
                    self._show_toast("✅ Costo actualizado")
                    if self._is_consolidated_view():
                        self._schedule_reload(200)
                else:
                    self._update_product_field(product_id, "precio_costo", old_value)
                    self._show_toast(f"❌ {msg}", persistent=True)
                dialog.accept()
            except Exception as e:
                self._show_toast(f"❌ Error: {str(e)}", persistent=True)

        save_btn.clicked.connect(on_save_cost)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_precio(self, product_id, product, local_override=None, ids_by_local=None):
        current_price = int(product.get("precio_venta") or 0)
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Precio")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Precio actual: ${current_price:,.0f}"))
        layout.addWidget(QLabel("Nuevo precio (ej: 25000, +20%, -500):"))

        price_input = QLineEdit(dialog)
        layout.addWidget(price_input)
        preview_label = QLabel("")
        preview_label.setStyleSheet("color: #10B981; font-weight: bold;")
        layout.addWidget(preview_label)
        if ids_by_local:
            note = QLabel("(Este cambio se aplicará a todos los locales)")
            note.setStyleSheet("font-size:10px; color:#888888;")
            layout.addWidget(note)

        def _is_allowed(txt: str) -> bool:
            return bool(re.fullmatch(r"[0-9+\\-\\.\\s%]+", txt.strip()))

        def _format_if_numeric(txt: str):
            stripped = txt.strip()
            if not stripped:
                return stripped
            if re.fullmatch(r"[0-9.]+", stripped):
                raw = re.sub(r"[^0-9]", "", stripped)
                if not raw:
                    return stripped
                return f"{int(raw):,}".replace(",", ".")
            return stripped

        def update_preview():
            txt = price_input.text().strip()
            if not txt:
                preview_label.setText("")
                return
            if not _is_allowed(txt):
                preview_label.setStyleSheet("color: #ef4444; font-weight: bold;")
                preview_label.setText("❌ Solo números, +, - o %")
                return
            try:
                new_price_val = sm._compute_new_price_from_str(current_price, txt)
                delta = new_price_val - current_price
                preview_label.setText(
                    f"Nuevo precio: ${new_price_val:,.0f} ({delta:+,.0f})"
                )
                preview_label.setStyleSheet("color: #10B981; font-weight: bold;")
            except Exception:
                preview_label.setStyleSheet("color: #ef4444; font-weight: bold;")
                preview_label.setText("❌ Formato inválido")

        def on_price_changed(text):
            formatted = _format_if_numeric(text)
            if formatted != text:
                cursor = price_input.cursorPosition()
                price_input.blockSignals(True)
                price_input.setText(formatted)
                price_input.blockSignals(False)
                price_input.setCursorPosition(
                    min(cursor + (len(formatted) - len(text)), len(formatted))
                )
            update_preview()

        price_input.textChanged.connect(on_price_changed)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Aplicar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_save_price():
            txt = price_input.text().strip()
            if not txt:
                self._show_toast("❌ Ingresa un precio", persistent=True)
                return
            if not _is_allowed(txt):
                self._show_toast(
                    "❌ Solo números, +, - o % en el precio", persistent=True
                )
                return
            try:
                new_price_val = sm._compute_new_price_from_str(current_price, txt)
                if new_price_val <= 0:
                    self._show_toast("❌ Precio debe ser mayor a 0", persistent=True)
                    return
                old_value = product.get("precio_venta")
                if ids_by_local:
                    ok_any = False
                    errors = []
                    for loc, pid in ids_by_local.items():
                        if not pid:
                            continue
                        self._update_product_field(pid, "precio_venta", new_price_val)
                        payload = {
                            "producto_id": pid,
                            "field": "precio_venta",
                            "value": new_price_val,
                            "usuario": self.username,
                            "local": loc,
                            "motivo": "edición manual",
                        }
                        ok, msg = qa.execute_update_field(payload)
                        if ok:
                            ok_any = True
                        else:
                            errors.append(msg)
                    if ok_any:
                        self._reset_edit_timeout()
                        formatted = f"${new_price_val:,.0f}".replace(",", ".")
                        self._show_toast(
                            f"✅ Precio actualizado: {formatted} en todos los locales"
                        )
                        self._merge_exact_duplicates(local_override or self.local)
                        for loc in ids_by_local.keys():
                            self._merge_exact_duplicates(loc)
                        if self._is_consolidated_view():
                            self._schedule_reload(200)
                    else:
                        self._show_toast(
                            f"❌ {errors[0] if errors else 'No se pudo actualizar'}",
                            persistent=True,
                        )
                    dialog.accept()
                    return

                self._update_product_field(product_id, "precio_venta", new_price_val)
                payload = {
                    "producto_id": product_id,
                    "field": "precio_venta",
                    "value": new_price_val,
                    "usuario": self.username,
                    "local": local_override or self.local,
                    "motivo": "edición manual",
                }
                ok, msg = qa.execute_update_field(payload)
                if ok:
                    self._reset_edit_timeout()
                    formatted = f"${new_price_val:,.0f}".replace(",", ".")
                    self._show_toast(f"✅ Precio actualizado: {formatted}")
                    self._reset_edit_timeout()
                    self._merge_exact_duplicates(local_override or self.local)
                    if self._is_consolidated_view():
                        self._schedule_reload(200)
                else:
                    self._update_product_field(product_id, "precio_venta", old_value)
                    self._show_toast(f"❌ {msg}", persistent=True)
                dialog.accept()
            except Exception as e:
                self._show_toast(f"❌ Error: {str(e)}", persistent=True)

        save_btn.clicked.connect(on_save_price)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec_()

    def _edit_estado(self, product_id, product, local_override=None):
        current_estado = product.get("estado") or "Nuevo"
        current_qty = int(product.get("cantidad", 0) or 0)
        current_price = int(product.get("precio_venta", 0) or 0)

        dialog = StateChangeDialog(current_qty, current_estado, current_price, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        qty, nuevo_estado, nuevo_precio, motivo = dialog.values()
        if qty <= 0 or qty > current_qty:
            self._show_toast("❌ Cantidad inválida", persistent=True)
            return
        if (
            nuevo_estado.strip().lower() == current_estado.strip().lower()
            and nuevo_precio is None
        ):
            self._show_toast("Sin cambios a aplicar", persistent=True)
            return
        if nuevo_estado.strip().lower() == "reacondicionado" and not motivo:
            self._show_toast("❌ Motivo requerido para Reacondicionado", persistent=True)
            return

        # Permitir fijar nuevo precio en cualquier cambio de estado (ej. pasar a promoción con otro precio)
        precio_aplicado = nuevo_precio if nuevo_precio is not None else None
        target_local = local_override or self.local
        ok, msg = sm.change_state_quantity(
            producto_id=product_id,
            cantidad=qty,
            nuevo_estado=nuevo_estado,
            nuevo_precio=precio_aplicado,
            usuario=self.username,
            local=target_local,
            motivo=motivo or "",
        )
        if ok:
            self._reset_edit_timeout()
            self._show_toast(f"✅ {qty} unidad(es) movidas a {nuevo_estado}")
            self._merge_exact_duplicates(target_local)
            self._schedule_reload(300)
        else:
            self._show_toast(f"❌ {msg}", persistent=True)

    def create_form(self):
        """Crea el formulario para agregar productos"""
        form_frame = QFrame()
        self.form_frame = form_frame
        form_frame.setStyleSheet(
            f"""
            QFrame {{
                background: #111827;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 20px;
                padding: 25px;
            }}
        """
        )
        form_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(form_frame)
        layout.setSpacing(12)

        title = QLabel("+ Agregar producto")
        title.setStyleSheet("color: #ffc107; font-size: 18px; font-weight: 900;")
        title.setMinimumHeight(scale(30))
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.form_fields = {}

        # Nombre (con detección inteligente)
        self.form_fields["nombre"] = QLineEdit()
        self.form_fields["nombre"].setPlaceholderText("Nombre")
        try:
            self.form_fields["nombre"].setMinimumWidth(scale(140))
        except Exception:
            self.form_fields["nombre"].setMinimumWidth(140)
        self.form_fields["nombre"].setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.form_fields["nombre"].textChanged.connect(self.on_nombre_changed)
        self._enforce_lowercase_input(self.form_fields["nombre"])
        row.addWidget(self.form_fields["nombre"])

        # Material
        self.form_fields["material"] = QLineEdit()
        self.form_fields["material"].setPlaceholderText("Material")
        try:
            self.form_fields["material"].setMaximumWidth(scale(170))
        except Exception:
            self.form_fields["material"].setMaximumWidth(170)
        self.form_fields["material"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self._enforce_lowercase_input(self.form_fields["material"])
        row.addWidget(self.form_fields["material"])

        # Categoria
        self.form_fields["categoria"] = QLineEdit()
        self.form_fields["categoria"].setPlaceholderText("Categoría")
        try:
            self.form_fields["categoria"].setMaximumWidth(scale(180))
        except Exception:
            self.form_fields["categoria"].setMaximumWidth(180)
        self.form_fields["categoria"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self._enforce_lowercase_input(self.form_fields["categoria"])
        row.addWidget(self.form_fields["categoria"])

        # Color (opcional) - agregado entre Categoría y Fabricante
        self.form_fields["color"] = QLineEdit()
        self.form_fields["color"].setPlaceholderText("Color")
        try:
            self.form_fields["color"].setMaximumWidth(scale(120))
        except Exception:
            self.form_fields["color"].setMaximumWidth(120)
        self.form_fields["color"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self._enforce_lowercase_input(self.form_fields["color"])
        row.addWidget(self.form_fields["color"])

        # Fabricante (NUEVO)
        self.form_fields["fabricante"] = QLineEdit()
        self.form_fields["fabricante"].setPlaceholderText("Fabricante")
        try:
            self.form_fields["fabricante"].setMaximumWidth(scale(170))
        except Exception:
            self.form_fields["fabricante"].setMaximumWidth(170)
        self.form_fields["fabricante"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self._enforce_lowercase_input(self.form_fields["fabricante"])
        row.addWidget(self.form_fields["fabricante"])

        # Medida (dinámica según detección)
        self.form_fields["medida"] = QComboBox()
        self.form_fields["medida"].setEditable(True)
        self.form_fields["medida"].addItems(["Medida"] + MEDIDAS_ESTANDAR)
        try:
            self.form_fields["medida"].setMaximumWidth(scale(170))
        except Exception:
            self.form_fields["medida"].setMaximumWidth(170)
        self.form_fields["medida"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self.form_fields["medida"].lineEdit().editingFinished.connect(
            lambda: self._maybe_add_custom_medida(
                self.form_fields["medida"].currentText()
            )
        )
        self.form_fields["medida"].setStyleSheet(
            """
            QComboBox {
                background: #fbbf24;
                color: #1a202c;
                border: 2px solid #d97706;
                border-radius: 10px;
                padding: 10px 12px;
                font-weight: 700;
            }
            QComboBox::drop-down { width: 26px; border: none; }
            QComboBox QAbstractItemView {
                background: #111827;
                color: #f8fafc;
                selection-background-color: #fbbf24;
                selection-color: #1a202c;
            }
        """
        )
        self.form_fields["medida_codigo"] = QLineEdit()
        self.form_fields["medida_codigo"].setPlaceholderText("Codigo")
        try:
            self.form_fields["medida_codigo"].setMaximumWidth(scale(140))
        except Exception:
            self.form_fields["medida_codigo"].setMaximumWidth(140)
        self.form_fields["medida_codigo"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self.form_fields["medida_codigo"].setStyleSheet(
            """
            QLineEdit {
                background: #fbbf24;
                color: #1a202c;
                border: 2px solid #d97706;
                border-radius: 10px;
                padding: 10px 12px;
                font-weight: 700;
            }
        """
        )
        self.form_fields["medida_codigo"].hide()
        row.addWidget(self.form_fields["medida"])
        row.addWidget(self.form_fields["medida_codigo"])

        # Estado
        self.form_fields["estado"] = QComboBox()
        self.form_fields["estado"].addItems(ESTADOS)
        try:
            self.form_fields["estado"].setMaximumWidth(scale(170))
        except Exception:
            self.form_fields["estado"].setMaximumWidth(170)
        self.form_fields["estado"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self.form_fields["estado"].setStyleSheet(
            """
            QComboBox {
                background: #fbbf24;
                color: #1a202c;
                border: 2px solid #d97706;
                border-radius: 10px;
                padding: 10px 12px;
                font-weight: 700;
            }
            QComboBox::drop-down { width: 26px; border: none; }
            QComboBox QAbstractItemView {
                background: #111827;
                color: #f8fafc;
                selection-background-color: #fbbf24;
                selection-color: #1a202c;
            }
        """
        )
        _set_combo_popup_width(self.form_fields["medida"])
        _set_combo_popup_width(self.form_fields["estado"])
        row.addWidget(self.form_fields["estado"])

        # Cantidad
        self.form_fields["cantidad"] = QSpinBox()
        self.form_fields["cantidad"].setRange(1, 9999)
        try:
            self.form_fields["cantidad"].setMaximumWidth(scale(90))
        except Exception:
            self.form_fields["cantidad"].setMaximumWidth(90)
        self.form_fields["cantidad"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        row.addWidget(self.form_fields["cantidad"])

        # Precio (con formato automático)
        self.form_fields["precio"] = QLineEdit()
        self.form_fields["precio"].setPlaceholderText("Precio")
        try:
            self.form_fields["precio"].setMaximumWidth(scale(150))
        except Exception:
            self.form_fields["precio"].setMaximumWidth(150)
        self.form_fields["precio"].setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self.form_fields["precio"].setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"[0-9.]*"), self.form_fields["precio"]
            )
        )
        self.form_fields["precio"].textChanged.connect(self.on_precio_changed)
        row.addWidget(self.form_fields["precio"])

        # Botón agregar
        self.add_btn = QPushButton("+ Agregar")
        self.add_btn.setStyleSheet(
            """
            QPushButton {
                background: #ffc107;
                color: #1A202C;
                border: none;
                border-radius: 16px;
                padding: 14px 22px;
                font-weight: 700;
            }
            QPushButton:hover { background: #ffcc33; }
        """
        )
        self.add_btn.clicked.connect(self.add_product)
        try:
            chain = [
                self.form_fields.get("nombre"),
                self.form_fields.get("material"),
                self.form_fields.get("categoria"),
                self.form_fields.get("color"),
                self.form_fields.get("fabricante"),
                self.form_fields.get("medida"),
                self.form_fields.get("estado"),
                self.form_fields.get("cantidad"),
                self.form_fields.get("precio"),
            ]
            self._setup_enter_chain(chain)
        except Exception:
            pass
        try:
            self.add_btn.setFixedWidth(scale(130))
        except Exception:
            self.add_btn.setFixedWidth(130)
        row.addWidget(self.add_btn)

        height_sources = (
            self.form_fields["nombre"],
            self.form_fields["material"],
            self.form_fields["categoria"],
            self.form_fields["color"],
            self.form_fields["fabricante"],
            self.form_fields["medida"],
            self.form_fields["medida_codigo"],
            self.form_fields["estado"],
            self.form_fields["cantidad"],
            self.form_fields["precio"],
            self.add_btn,
        )
        for widget in height_sources:
            try:
                widget.ensurePolished()
            except Exception:
                pass
        field_height = max(w.sizeHint().height() for w in height_sources)
        for widget in (
            self.form_fields["nombre"],
            self.form_fields["categoria"],
            self.form_fields["color"],
            self.form_fields["fabricante"],
            self.form_fields["medida"],
            self.form_fields["medida_codigo"],
            self.form_fields["estado"],
            self.form_fields["cantidad"],
            self.form_fields["precio"],
            self.add_btn,
        ):
            try:
                widget.setFixedHeight(field_height)
            except Exception:
                pass

        row.addStretch()
        layout.addLayout(row)

        types_frame = QFrame()
        types_frame.setStyleSheet(
            """
            QFrame {
                background: rgba(15, 23, 42, 0.7);
                border: 1px solid #2d3748;
                border-radius: 14px;
                padding: 8px;
            }
        """
        )
        types_row = FlowLayout(types_frame, margin=0, spacing=8)
        # FlowLayout already respects margins set on the frame
        types_frame.layout = types_row
        types_label = QLabel("Tipo de medida/codigo:")
        types_label.setStyleSheet("color:#e5e7eb;font-weight:700;")
        types_row.addWidget(types_label)
        self._measure_type_checks = {}
        self._measure_type_group = QButtonGroup(self)
        self._measure_type_group.setExclusive(True)
        for label in ("Metro", "Plaza", "Rodado", "Pulgada", "Litros", "Codigo"):
            cb = QCheckBox(label)
            cb.setProperty("measure_mode", label.lower())
            cb.setCursor(Qt.PointingHandCursor)
            cb.setStyleSheet(
                """
                QCheckBox {
                    color: #e5e7eb;
                    font-weight: 700;
                    padding: 6px 10px;
                    border: 1px solid #374151;
                    border-radius: 12px;
                    background: #111827;
                }
                QCheckBox::indicator { width: 0; height: 0; }
                QCheckBox:checked {
                    background: #fbbf24;
                    color: #1a202c;
                    border-color: #d97706;
                }
            """
            )
            self._measure_type_group.addButton(cb)
            self._measure_type_checks[label.lower()] = cb
            types_row.addWidget(cb)
        self._measure_type_group.buttonClicked.connect(self._on_measure_type_clicked)
        layout.addWidget(types_frame)

        saved_mode = self._load_measure_mode_pref()
        if saved_mode not in (
            "metro",
            "plaza",
            "rodado",
            "pulgada",
            "litros",
            "codigo",
        ):
            saved_mode = "metro"
        self._set_measure_mode(saved_mode)

        self.main_layout.addWidget(form_frame)

    def update_categories(self, categories=None):
        """Actualiza el combo de categorías con las presentes en productos."""
        try:
            cats_raw = (
                categories
                if categories is not None
                else sorted(set(sm.get_all_tipos()))
            )
            cats = sorted(
                {(c or "").strip().lower() for c in cats_raw if (c or "").strip()}
            )
            self.categories_cache = cats
            self.category_combo.blockSignals(True)
            self.category_combo.clear()
            self.category_combo.addItem("Todas las categorias")
            for cat in cats:
                self.category_combo.addItem(cat)
            self.category_combo.setCurrentIndex(0)
            self.category_combo.blockSignals(False)
        except Exception as e:
            logger.error(f"Error updating categories: {e}")

    def refresh_filter_options(self):
        """Obtiene opciones de filtros (categoría, fabricante, medidas) del local actual."""
        try:
            if (
                getattr(self, "_filter_thread", None)
                and self._filter_thread.isRunning()
            ):
                return
            logger.info("refresh_filter_options: start")
            self._filter_thread = FilterOptionsThread(
                self.view_local,
                self._is_consolidated_view(),
                self._use_reservas_stock(),
                parent=self,
            )
            self._filter_thread.data_ready.connect(self._apply_filter_options)
            self._filter_thread.start()
        except Exception as e:
            logger.error(f"Error iniciando refresh_filter_options: {e}")

    def _apply_filter_options(self, products):
        try:
            if getattr(self, "_filter_apply_running", False):
                logger.info("_apply_filter_options: skip (already running)")
                return
            self._filter_apply_running = True
            t0 = time.perf_counter()
            logger.info("_apply_filter_options: start")
            if isinstance(products, dict):
                categories = products.get("categories") or []
                fabricantes = products.get("fabricantes") or []
                estados_present = products.get("estados") or []
                colores_present = products.get("colores") or []
                plazas_present = products.get("plazas") or []
                rodados_present = products.get("rodados") or []
                pulgadas_present = products.get("pulgadas") or []
                litros_present = products.get("litros") or []
                medidas_std = list(MEDIDAS_ESTANDAR) + list(
                    products.get("medidas_std") or []
                )
            else:
                categories = sorted(
                    {
                        (p.get("categoria") or "").strip().lower()
                        for p in products
                        if (p.get("categoria") or "").strip()
                    }
                )
                fabricantes = sorted(
                    {
                        (p.get("fabricante") or "").strip()
                        for p in products
                        if (p.get("fabricante") or "").strip()
                        and (p.get("fabricante") or "").strip() != "-"
                    }
                )
                estados_present = sorted(
                    {
                        (p.get("estado") or "").strip()
                        for p in products
                        if (p.get("estado") or "").strip()
                    }
                )
                colores_present = sorted(
                    {
                        (p.get("color") or "").strip()
                        for p in products
                        if (p.get("color") or "").strip()
                    }
                )
                medidas_vals = [
                    (p.get("medida") or "").strip()
                    for p in products
                    if (p.get("medida") or "").strip()
                ]
                plazas_present = sorted({m for m in medidas_vals if m in PLAZAS})
                rodados_present = sorted({m for m in medidas_vals if m in RODADOS})
                pulgadas_present = sorted({m for m in medidas_vals if m in PULGADAS})
                litros_present = self._sort_medidas(
                    [m for m in medidas_vals if self._is_litros_medida(m)]
                )
                medidas_std = [m for m in MEDIDAS_ESTANDAR if m in medidas_vals]
                extras = sorted(
                    {
                        m
                        for m in medidas_vals
                        if m not in PLAZAS
                        and m not in RODADOS
                        and m not in PULGADAS
                        and not self._is_litros_medida(m)
                        and m not in MEDIDAS_ESTANDAR
                    }
                )
                medidas_std = list(MEDIDAS_ESTANDAR) + medidas_std + extras
            medidas_std = self._sort_medidas(medidas_std)
            litros_present = self._sort_medidas(litros_present)
            self._measure_options_cache = {
                "metro": list(medidas_std),
                "plaza": list(plazas_present or PLAZAS),
                "rodado": list(rodados_present or RODADOS),
                "pulgada": list(pulgadas_present or PULGADAS),
                "litros": list(litros_present),
            }
            # Limitar opciones para evitar bloqueos en UI
            if len(categories) > 300:
                categories = categories[:300]
            if len(fabricantes) > 300:
                fabricantes = fabricantes[:300]
            if len(medidas_std) > 300:
                medidas_std = medidas_std[:300]
            if len(litros_present) > 300:
                litros_present = litros_present[:300]

            def _set_combo(combo: QComboBox, default_label: str, options: list[str]):
                try:
                    combo.setUpdatesEnabled(False)
                except Exception:
                    pass
                current = combo.currentText() if combo.count() else default_label
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(default_label)
                for opt in options:
                    combo.addItem(opt)
                idx = combo.findText(current)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
                try:
                    combo.setUpdatesEnabled(True)
                except Exception:
                    pass
                _set_combo_popup_width(combo)

            _set_combo(self.category_combo, "Todas las categorias", categories)
            _set_combo(self.fabricante_combo, "Fabricante", fabricantes)
            if hasattr(self, "estado_filter"):
                # mantener selección actual si corresponde
                _set_combo(self.estado_filter, "Estado", estados_present or ESTADOS)
            if hasattr(self, "color_combo"):
                _set_combo(self.color_combo, "Color", colores_present)
            # Actualizar combo dinámico de medida con opciones actuales
            if hasattr(self, "filter_medida_combo"):
                current_mode = getattr(self, "_filter_measure_mode", "metro")
                if current_mode == "plaza":
                    options = plazas_present or PLAZAS
                elif current_mode == "rodado":
                    options = rodados_present or RODADOS
                elif current_mode == "pulgada":
                    options = pulgadas_present or PULGADAS
                elif current_mode == "litros":
                    options = litros_present
                else:
                    options = medidas_std
                _set_combo(self.filter_medida_combo, "Medida", options)

            logger.info(
                f"_apply_filter_options: done in {time.perf_counter() - t0:.3f}s"
            )
            logger.info("refresh_filter_options: done")
        except Exception as e:
            logger.error(f"Error aplicando filtros: {e}")
        finally:
            self._filter_apply_running = False

    def on_nombre_changed(self):
        """Mantiene medidas estandar cuando el modo es Metro."""
        if getattr(self, "_measure_mode", "metro") != "metro":
            return
        self._apply_measure_mode()

    def _on_measure_type_clicked(self, btn):
        mode = btn.property("measure_mode") or "metro"
        try:
            btn.setChecked(True)
        except Exception:
            pass
        self._set_measure_mode(mode)

    def _set_measure_mode(self, mode: str):
        self._measure_mode = mode
        if hasattr(self, "_measure_type_checks"):
            for key, cb in self._measure_type_checks.items():
                cb.blockSignals(True)
                cb.setChecked(key == mode)
                cb.blockSignals(False)
        self._apply_measure_mode()
        self._save_measure_mode_pref(mode)

    def _apply_measure_mode(self):
        mode = getattr(self, "_measure_mode", "metro")
        if not hasattr(self, "form_fields"):
            return
        if mode == "codigo":
            self.form_fields["medida"].hide()
            self.form_fields["medida_codigo"].show()
            return
        self.form_fields["medida"].show()
        self.form_fields["medida_codigo"].hide()
        if mode == "plaza":
            options = PLAZAS
        elif mode == "rodado":
            options = RODADOS
        elif mode == "pulgada":
            options = PULGADAS
        elif mode == "litros":
            options = self._get_measure_options_for_mode("litros")
        else:
            options = self._sort_medidas(MEDIDAS_ESTANDAR)
        current = self.form_fields["medida"].currentText()
        if current and current != "Medida" and current not in options:
            options = self._sort_medidas(list(options) + [current])
        self.form_fields["medida"].blockSignals(True)
        self.form_fields["medida"].clear()
        self.form_fields["medida"].addItems(["Medida"] + options)
        idx = self.form_fields["medida"].findText(current)
        self.form_fields["medida"].setCurrentIndex(idx if idx >= 0 else 0)
        self.form_fields["medida"].blockSignals(False)

    def _on_filter_measure_type_clicked(self, btn):
        """Manejador para clics en botones de tipo de medida en filtros."""
        mode = btn.property("measure_mode") or "metro"
        try:
            btn.setChecked(True)
        except Exception:
            pass
        self._set_filter_measure_mode(mode)

    def _set_filter_measure_mode(self, mode: str):
        """Establece el modo de medida para los filtros."""
        self._filter_measure_mode = mode
        if hasattr(self, "_filter_measure_type_checks"):
            for key, cb in self._filter_measure_type_checks.items():
                cb.blockSignals(True)
                cb.setChecked(key == mode)
                cb.blockSignals(False)
        self._apply_filter_measure_mode()
        self._save_filter_measure_mode_pref(mode)

    def _apply_filter_measure_mode(self):
        """Aplica el modo de medida al combo de filtro dinámico."""
        if not hasattr(self, "filter_medida_combo"):
            return
        mode = getattr(self, "_filter_measure_mode", "metro")

        if mode == "codigo":
            # Ocultar combo de medida y mostrar input de código
            try:
                self.filter_medida_combo.hide()
            except Exception:
                pass
            if hasattr(self, "codigo_input"):
                try:
                    self.codigo_input.show()
                except Exception:
                    pass
            return

        # Modo medida: mostrar combo y ocultar input de código
        try:
            self.filter_medida_combo.show()
        except Exception:
            pass
        if hasattr(self, "codigo_input"):
            try:
                self.codigo_input.hide()
            except Exception:
                pass

        options = self._get_measure_options_for_mode(mode)

        current = self.filter_medida_combo.currentText()
        default_label = "Medida"
        if current and current != default_label and current not in options:
            options = self._sort_medidas(list(options) + [current])

        self.filter_medida_combo.blockSignals(True)
        self.filter_medida_combo.clear()
        self.filter_medida_combo.addItems([default_label] + options)
        idx = self.filter_medida_combo.findText(current)
        self.filter_medida_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.filter_medida_combo.blockSignals(False)

    def _load_measure_mode_pref(self) -> str:
        try:
            if not PREFS_PATH.exists():
                return ""
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
            return data.get("stock_measure_mode", "") or ""
        except Exception:
            return ""

    def _save_measure_mode_pref(self, mode: str) -> None:
        try:
            data = {}
            if PREFS_PATH.exists():
                try:
                    data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
                except Exception:
                    data = {}
            data["stock_measure_mode"] = mode
            _ensure_prefs_dir()
            PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_filter_measure_mode_pref(self) -> str:
        """Carga el modo de medida guardado para filtros."""
        try:
            if not PREFS_PATH.exists():
                return ""
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
            return data.get("stock_filter_measure_mode", "") or ""
        except Exception:
            return ""

    def _save_filter_measure_mode_pref(self, mode: str) -> None:
        """Guarda el modo de medida para filtros."""
        try:
            data = {}
            if PREFS_PATH.exists():
                try:
                    data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
                except Exception:
                    data = {}
            data["stock_filter_measure_mode"] = mode
            _ensure_prefs_dir()
            PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _setup_enter_chain(self, widgets: list):
        try:
            self._enter_chain = [w for w in widgets if w is not None]
            for w in self._enter_chain:
                try:
                    w.installEventFilter(self)
                except Exception:
                    pass
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            if getattr(self, "_enter_chain", None):
                if obj in self._enter_chain and event.type() == QEvent.KeyPress:
                    if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                        self._advance_enter_focus(obj)
                        return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _advance_enter_focus(self, current):
        try:
            chain = getattr(self, "_enter_chain", []) or []
            if not chain:
                return
            if current not in chain:
                return
            idx = chain.index(current)
            if idx < len(chain) - 1:
                nxt = chain[idx + 1]
                try:
                    nxt.setFocus()
                except Exception:
                    pass
            else:
                try:
                    self.add_btn.click()
                except Exception:
                    pass
        except Exception:
            pass

    def _ensure_form_created(self):
        try:
            if hasattr(self, "form_frame"):
                return
            self.create_form()
            self._apply_edit_lock_state()
        except Exception as e:
            logger.error(f"Error creando formulario de alta: {e}")

    def on_precio_changed(self):
        """Formatea precio con separador de miles (22222 → 22.222)"""
        try:
            # Obtener texto sin formatear
            text = self.form_fields["precio"].text().replace(".", "").replace(",", "")
            if not text or not text.isdigit():
                return

            # Formatear con puntos
            num = int(text)
            formatted = f"{num:,}".replace(",", ".")

            # Solo actualizar si cambió (evitar loop infinito)
            if formatted != self.form_fields["precio"].text():
                # Guardar posición del cursor
                cursor_pos = self.form_fields["precio"].cursorPosition()
                self.form_fields["precio"].blockSignals(True)
                self.form_fields["precio"].setText(formatted)
                self.form_fields["precio"].setCursorPosition(
                    min(cursor_pos + 1, len(formatted))
                )
                self.form_fields["precio"].blockSignals(False)
        except Exception:
            pass

    def on_ver_stock_changed(self):
        """Maneja cambio de local para ver stock"""
        t0 = time.perf_counter()
        selected = self.ver_stock_combo.currentText()
        clean = self._clean_local_label(selected)
        if selected == "Todos los locales":
            clean = "Todos los locales"

        # Detener threads activos antes de cambiar de local
        try:
            self._stop_thread_safe(getattr(self, "loading_thread", None), 800)
            self._stop_thread_safe(getattr(self, "_filter_thread", None), 800)
            self._stop_thread_safe(getattr(self, "_combo_sync_thread", None), 800)
            self._stop_thread_safe(getattr(self, "_consolidated_thread", None), 800)
            self._combo_sync_running = False
        except Exception:
            pass

        self.view_local = clean

        if self.role == "admin":
            is_all = clean in ("Todos", "Todos los locales")
            # Admin puede ver todos; edita solo si es un local específico
            if not is_all:
                self.local = self.view_local
                self.read_only = False
                self._ensure_form_created()
            else:
                self.read_only = True
        else:
            # Locales normales: solo lectura al ver otros
            self.read_only = self._normalize_local_name(
                clean
            ) != self._normalize_local_name(self.local)
        self._update_meta_label()
        try:
            title_local = self.view_local if self.role == "admin" else self.local
            self.setWindowTitle(f"Stock - {title_local}")
            if hasattr(self, "header_title"):
                self.header_title.setText(f"Stock de {title_local}")
        except Exception:
            pass
        self._apply_view_mode()
        self._apply_edit_lock_state()
        self.reset_filters(apply=False)
        self._schedule_reload(50)
        logger.info(
            f"on_ver_stock_changed: switched to {self.view_local} in {time.perf_counter() - t0:.3f}s"
        )

    def on_view_mode_changed(self):
        """Maneja cambio de modo de vista (disponibles / señados / envíos)."""
        self.load_data()

    def reset_filters(self, apply: bool = True):
        """Limpia todos los filtros y recarga."""
        if getattr(self, "_reset_in_progress", False):
            logger.info("reset_filters: skip (already running)")
            return
        self._reset_in_progress = True
        try:
            try:
                if hasattr(self, "search_input"):
                    self.search_input.blockSignals(True)
                    self.search_input.clear()
                    self.search_input.blockSignals(False)
                if hasattr(self, "codigo_input"):
                    self.codigo_input.blockSignals(True)
                    self.codigo_input.clear()
                    self.codigo_input.blockSignals(False)
                for combo, default_idx in [
                    (getattr(self, "category_combo", None), 0),
                    (getattr(self, "filter_medida_combo", None), 0),
                    (getattr(self, "fabricante_combo", None), 0),
                    (getattr(self, "estado_filter", None), 0),
                    (getattr(self, "color_combo", None), 0),
                ]:
                    if combo is not None:
                        combo.blockSignals(True)
                        combo.setCurrentIndex(default_idx)
                        combo.blockSignals(False)
                # Resetear botones de medida al modo "metro"
                if hasattr(self, "_filter_measure_type_checks"):
                    for cb in self._filter_measure_type_checks.values():
                        cb.blockSignals(True)
                        cb.setChecked(False)
                        cb.blockSignals(False)
                    # Marcar "metro" como seleccionado
                    if "metro" in self._filter_measure_type_checks:
                        cb = self._filter_measure_type_checks["metro"]
                        cb.blockSignals(True)
                        cb.setChecked(True)
                        cb.blockSignals(False)
                    self._filter_measure_mode = "metro"
                    self._apply_filter_measure_mode()
            except Exception as e:
                logger.error(f"Error reseteando filtros: {e}")
            # Detener/hacer que no afecten cargas en curso: terminar hilo de carga y aumentar contador
            try:
                if (
                    hasattr(self, "loading_thread")
                    and self.loading_thread
                    and self.loading_thread.isRunning()
                ):
                    self._stop_thread_safe(self.loading_thread, 800)
                if not hasattr(self, "_load_counter"):
                    self._load_counter = 0
                self._load_counter += 1
                self._current_load_id = self._load_counter
                # detener timer de b??squeda si est?? en uso
                try:
                    if hasattr(self, "_search_timer"):
                        self._search_timer.stop()
                except Exception:
                    pass
            except Exception:
                pass
            # Eliminar posibles filtros persistidos en prefs (defensivo)
            try:
                if PREFS_PATH.exists():
                    try:
                        data = json.loads(
                            PREFS_PATH.read_text(encoding="utf-8") or "{}"
                        )
                    except Exception:
                        data = {}
                    removed = False
                    for key in (
                        "stock_filters",
                        "stock_table_filters",
                        "stock_saved_filters",
                    ):
                        if key in data:
                            del data[key]
                            removed = True
                    if removed:
                        _ensure_prefs_dir()
                        PREFS_PATH.write_text(
                            json.dumps(data, indent=2), encoding="utf-8"
                        )
            except Exception:
                pass

            if apply:
                # Simplificado: despu??s de limpiar todos los combos, solo llamamos load_data()
                # que leer?? los valores actuales (que ahora son los defaults/vac??os)
                logger.info("reset_filters: combos cleared, scheduling load_data()")
                self._schedule_reload(50)
            self._update_filter_chip()
        finally:
            self._reset_in_progress = False

    def _on_codigo_filter_changed(self, _text=""):
        """Maneja cambios en filtro por código con delay."""
        self.on_search_changed()

    def on_search_changed(self):
        """Maneja cambios en búsqueda con delay"""
        current_time = time.time()
        self.last_search_time = current_time
        # Recargar después de 300ms de inactividad (reutilizando timer)
        self._search_timer.stop()
        self._search_timer.start(300)

    def _delayed_search(self, timestamp):
        """Ejecuta búsqueda si no ha habido cambios recientes"""
        if timestamp == self.last_search_time:
            self.load_data()

    def edit_price(self, product_id, row):
        """Edita precio"""
        if not self._can_edit_stock():
            QMessageBox.warning(
                self,
                "Editar stock",
                "Ingresá la contraseña con el botón Editar para modificar stock.",
            )
            return
        current = (
            self.table.item(row, 8)
            .text()
            .replace("$", "")
            .replace(".", "")
            .replace(",", "")
        )
        try:
            current_price = int(current)
        except:
            current_price = 0

        from PyQt5.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self, "Editar Precio", f"Nuevo precio (actual: ${current_price}):"
        )
        if ok and text:
            try:
                new_price = sm._compute_new_price_from_str(current_price, text)

                # Obtener datos para resolución de ID (Dual Write)
                name = self.table.item(row, 0).text()
                cat = self.table.item(row, 2).text()
                medida = self.table.item(row, 3).text()
                # Estado no está en la tabla, asumimos 'Nuevo' o lo buscamosOK
                # El ID de firestore podría ser suficiente para Firestore, pero para SQL necesitamos match.
                # Si no tenemos estado, podría fallar el match exacto si hay múltiples estados.
                # Pero por ahora enviamos lo que tenemos.

                # Dual Write: Actualizar en ambos sistemas
                payload = {
                    "producto_id": product_id,
                    "field": "precio_venta",
                    "value": int(new_price),
                    "usuario": self.username,
                    "local": self.local,
                    "motivo": "edición manual",
                    # Datos extra para resolución de ID SQL
                    "nombre": name,
                    "categoria": cat,
                    "medida": medida,
                    "estado": "Nuevo",  # Default arriesgado, pero común
                }
                self._reset_edit_timeout()
                qa.execute_update_field(payload)

                # Recargar para reflejar cambios (aunque Firestore podría tardar ms)
                self._schedule_reload(200)

            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error al actualizar precio: {e}")

    def edit_quantity(self, product_id, row):
        """Edita cantidad"""
        current = int(self.table.item(row, 7).text())
        from PyQt5.QtWidgets import QInputDialog

        delta, ok = QInputDialog.getInt(
            self,
            "Modificar Cantidad",
            f"Cantidad a sumar/restar (actual: {current}):",
            0,
            -current,
            9999,
        )
        if ok and delta != 0:
            try:
                # Obtener datos para resolución de ID
                name = self.table.item(row, 0).text()
                cat = self.table.item(row, 2).text()
                medida = self.table.item(row, 3).text()

                # Dual Write para ajuste manual de cantidad
                payload = {
                    "producto_id": product_id,
                    "delta": delta,
                    "usuario": self.username,
                    "local": self.local,
                    "detalle": "ajuste manual",
                    # Datos extra
                    "nombre": name,
                    "categoria": cat,
                    "medida": medida,
                    "estado": "Nuevo",
                }
                qa.execute_increment(payload)
                self._reset_edit_timeout()

                self._schedule_reload(200)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error al modificar cantidad: {e}")

    def add_product(self):
        """Agrega nuevo producto"""
        if self.role == "admin" and not self._can_add_product():
            QMessageBox.warning(
                self,
                "Agregar producto",
                "Habilita el formulario con el boton Editar para agregar productos.",
            )
            return
        if not self._can_edit_stock():
            QMessageBox.warning(
                self,
                "Editar stock",
                "Ingresá la contraseña con el botón Editar para modificar stock.",
            )
            return
        try:
            nombre = self.form_fields["nombre"].text().strip()
            material = self.form_fields["material"].text().strip()
            categoria = self.form_fields["categoria"].text().strip()
            fabricante = self.form_fields["fabricante"].text().strip()
            color = (
                self.form_fields.get("color").text().strip()
                if self.form_fields.get("color") is not None
                else ""
            )
            measure_mode = getattr(self, "_measure_mode", "metro")
            codigo = ""
            if measure_mode == "codigo":
                codigo = self.form_fields["medida_codigo"].text().strip()
                if not codigo:
                    QMessageBox.warning(self, "Error", "Ingrese un codigo")
                    return
                medida = None
            else:
                medida = self.form_fields["medida"].currentText()
                if medida == "Medida":
                    medida = None
            estado = self.form_fields["estado"].currentText()
            cantidad = self.form_fields["cantidad"].value()
            precio_text = (
                self.form_fields["precio"]
                .text()
                .strip()
                .replace(".", "")
                .replace(",", "")
            )
            precio = None

            if not nombre or not categoria:
                QMessageBox.warning(
                    self, "Error", "Complete todos los campos obligatorios"
                )
                return

            if precio_text:
                if not precio_text.isdigit():
                    QMessageBox.warning(self, "Error", "Precio invOKlido")
                    return
                try:
                    precio = int(precio_text)
                except:
                    QMessageBox.warning(self, "Error", "Precio invOKlido")
                    return
                if precio <= 0:
                    raise ValueError("Precio invOKlido")

            # Bloquear categorias fuera de la base
            def _norm(val: str) -> str:
                return (val or "").strip().lower()

            categoria_norm = sm._norm_cat(categoria)
            if not categoria_norm:
                QMessageBox.warning(self, "Error", "La categoria no puede estar vacia")
                return
            try:
                allowed = self.categories_cache or sorted(set(sm.get_all_tipos()))
                allowed_norm = {sm._norm_cat(c) for c in allowed if c}
                if allowed_norm and categoria_norm not in allowed_norm:
                    QMessageBox.warning(
                        self,
                        "Categoria invalida",
                        "La categoria no existe en la base. Selecciona una categoria existente.",
                    )
                    return
            except Exception:
                pass

            # Bloquear duplicados exactos (mismo producto) aunque tenga otro precio
            medida_norm = _norm(medida) if medida else ""
            codigo_norm = _norm(codigo)
            estado_norm = _norm(estado)
            fabricante_norm = _norm(fabricante)
            nombre_norm = _norm(nombre)

            def _same_base(existing: dict) -> bool:
                return (
                    _norm(existing.get("nombre")) == nombre_norm
                    and _norm(existing.get("material")) == _norm(material)
                    and _norm(existing.get("categoria")) == categoria_norm
                    and _norm(existing.get("estado")) == estado_norm
                    and _norm(existing.get("color")) == _norm(color)
                    and _norm(existing.get("fabricante")) == fabricante_norm
                )

            productos_local = []
            try:
                productos_local = list_products_by_local(self.local)
            except Exception:
                productos_local = (
                    list(self._products_by_id.values())
                    if hasattr(self, "_products_by_id")
                    else []
                )

            if codigo_norm:
                for existing in productos_local:
                    if (
                        _same_base(existing)
                        and _norm(existing.get("codigo")) == codigo_norm
                    ):
                        QMessageBox.information(
                            self,
                            "Producto ya existe",
                            "Ese producto ya esta en el stock. Usa los botones + / - para ajustar la cantidad o edita el precio desde la tabla.",
                        )
                        return
            else:
                for existing in productos_local:
                    if (
                        _same_base(existing)
                        and _norm(existing.get("medida")) == medida_norm
                    ):
                        QMessageBox.information(
                            self,
                            "Producto ya existe",
                            "Ese producto ya esta en el stock. Usa los botones + / - para ajustar la cantidad o edita el precio desde la tabla.",
                        )
                        return

            categoria = categoria_norm

            # Verificar precios en otros locales para productos iguales
            match_prices = set()
            try:
                locales_raw = get_all_locals() or []
                productos = []
                if locales_raw:
                    for loc in locales_raw:
                        productos.extend(list_products_by_local(loc))
                else:
                    productos = list_products_by_local(None)
                for existing in productos:
                    if (
                        _norm(existing.get("nombre")) == nombre_norm
                        and _norm(existing.get("material")) == _norm(material)
                        and _norm(existing.get("categoria")) == categoria_norm
                        and _norm(existing.get("medida")) == medida_norm
                        and _norm(existing.get("estado")) == estado_norm
                        and _norm(existing.get("color")) == _norm(color)
                        and _norm(existing.get("fabricante")) == fabricante_norm
                        and (
                            not codigo_norm
                            or _norm(existing.get("codigo")) == codigo_norm
                        )
                    ):
                        try:
                            pprice = int(existing.get("precio_venta") or 0)
                        except Exception:
                            pprice = 0
                        if pprice > 0:
                            match_prices.add(pprice)
            except Exception:
                match_prices = set()

            if len(match_prices) > 1:
                QMessageBox.warning(
                    self,
                    "Precio inconsistente",
                    "Este producto ya existe en otros locales con distintos precios. Unifica el precio antes de agregarlo.",
                )
                return
            if len(match_prices) == 1:
                existing_price = next(iter(match_prices))
                if precio is None:
                    precio = existing_price
                    try:
                        self.form_fields["precio"].setText(str(existing_price))
                    except Exception:
                        pass
                    try:
                        self._show_toast(
                            f"Producto ya existe en locales. Precio auto: ${existing_price:,}".replace(
                                ",", "."
                            )
                        )
                    except Exception:
                        pass
                elif int(precio) != int(existing_price):
                    QMessageBox.warning(
                        self,
                        "Precio distinto",
                        f"El producto ya existe en otros locales con precio ${existing_price:,}.".replace(
                            ",", "."
                        ),
                    )
                    return

            if precio is None:
                QMessageBox.warning(self, "Error", "Complete el precio")
                return

            # Agregar con Dual Write
            # Nota: fabricante no está en add_or_increment actual, se agregará cuando actualicemos stock_model
            payload = {
                "nombre": nombre,
                "material": material,
                "categoria": categoria,
                "medida": medida,
                "codigo": codigo or None,
                "estado": estado,
                "color": color or None,
                "fabricante": fabricante,
                "precio_costo": 0,
                "precio_venta": int(precio),
                "cantidad": cantidad,
                "local": self.local,
                "usuario": self.username,
                "force_update": False,
            }

            success, msg = qa.execute_add_product(payload)

            if success:
                # Limpiar formulario
                self.form_fields["nombre"].clear()
                self.form_fields["material"].clear()
                self.form_fields["categoria"].clear()
                self.form_fields["color"].clear()
                self.form_fields["fabricante"].clear()
                self.form_fields["precio"].clear()
                self.form_fields["cantidad"].setValue(1)
                self.form_fields["medida"].setCurrentIndex(0)
                self.form_fields["medida_codigo"].clear()
                self.form_fields["estado"].setCurrentIndex(0)

                # Recargar datos
                self._schedule_reload(0)
                QMessageBox.information(
                    self, "Éxito", f"Producto '{nombre}' agregado correctamente"
                )
                self._reset_edit_timeout()
            else:
                QMessageBox.warning(self, "Error", "No se pudo agregar el producto")

        except Exception as e:
            logger.error(f"Error adding product: {e}")
            QMessageBox.warning(self, "Error", f"Error al agregar producto: {e}")

    def transfer_product(self, product):
        """Muestra diálogo mejorado para transferir producto a otro local"""
        try:
            if int(product.get("is_combo") or 0) == 1:
                QMessageBox.information(
                    self, "Combo", "No se puede transferir un combo."
                )
                return
            # Seleccionar local destino con popup
            otros_locales = [l for l in self.locales if l != self.local]
            if not otros_locales:
                QMessageBox.information(
                    self, "Sin opciones", "No hay otros locales disponibles"
                )
                return

            # Crear diálogo personalizado para transferencia
            dialog = QDialog(self)
            dialog.setWindowTitle("Transferir Producto")
            layout = QVBoxLayout(dialog)

            # Info del producto
            info_label = QLabel(
                f"Producto: {product.get('nombre')}\nLocal origen: {self.local}"
            )
            layout.addWidget(info_label)

            # Seleccionar local destino
            dest_label = QLabel("Local destino:")
            layout.addWidget(dest_label)
            dest_combo = QComboBox(dialog)
            dest_combo.addItems(otros_locales)
            layout.addWidget(dest_combo)

            # Cantidad disponible y a transferir
            cantidad_actual = int(product.get("cantidad", 0))
            if cantidad_actual <= 0:
                QMessageBox.warning(
                    self, "Sin stock", "No hay unidades para transferir"
                )
                return

            qty_label = QLabel(
                f"Cantidad disponible: {cantidad_actual}\nCantidad a transferir:"
            )
            layout.addWidget(qty_label)
            qty_spin = QSpinBox(dialog)
            qty_spin.setRange(1, cantidad_actual)
            qty_spin.setValue(1)
            layout.addWidget(qty_spin)

            # Botones
            buttons = QHBoxLayout()
            confirm_btn = QPushButton("Confirmar Transferencia")
            cancel_btn = QPushButton("Cancelar")
            buttons.addWidget(confirm_btn)
            buttons.addWidget(cancel_btn)
            layout.addLayout(buttons)

            def on_confirm():
                local_destino = dest_combo.currentText()
                cantidad = qty_spin.value()

                # Confirmación final
                respuesta = QMessageBox.question(
                    self,
                    "Confirmar Transferencia",
                    f"¿Transferir {cantidad} unidades a {local_destino}OK\n\nOrigen: {self.local}\nDestino: {local_destino}",
                    QMessageBox.Yes | QMessageBox.No,
                )

                if respuesta == QMessageBox.Yes:
                    payload = {
                        "producto_id": product.get("id"),
                        "from_local": self.local,
                        "to_local": local_destino,
                        "cantidad": cantidad,
                        "usuario": self.username,
                        "nombre": product.get("nombre"),
                        "categoria": product.get("categoria"),
                        "medida": product.get("medida"),
                        "estado": product.get("estado"),
                        "color": product.get("color"),
                        "precio_venta": product.get("precio_venta"),
                    }

                    try:
                        qa.execute_transfer(payload)
                        self._show_toast(
                            f"✅ {cantidad} unidades transferidas a {local_destino}"
                        )
                        self._schedule_reload(500)
                    except Exception as e:
                        self._show_toast(
                            f"❌ Error en transferencia: {str(e)}", persistent=True
                        )

                dialog.accept()

            confirm_btn.clicked.connect(on_confirm)
            cancel_btn.clicked.connect(lambda: dialog.reject())
            dialog.exec_()

        except Exception as e:
            logger.error(f"Error en transfer_product: {e}")
            self._show_toast(f"❌ Error en transferencia: {str(e)}", persistent=True)

    def delete_product(self, product: dict):
        """Elimina un producto (solo admin y en local específico)."""
        if self.read_only:
            QMessageBox.warning(
                self,
                "Solo lectura",
                "Selecciona un local específico para poder eliminar productos.",
            )
            return
        if self.role != "admin":
            QMessageBox.warning(
                self, "Permisos", "Solo un administrador puede eliminar productos."
            )
            return

        pid = product.get("id")
        is_combo = int(product.get("is_combo") or 0) == 1
        nombre = product.get("nombre") or "Producto"
        if not pid:
            QMessageBox.warning(self, "Error", "No se pudo obtener el ID del producto.")
            return

        confirm = QMessageBox.question(
            self,
            "Eliminar producto",
            f"¿Eliminar '{nombre}' (ID {pid}) del local {self.local}OK\nEsta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        if is_combo:
            ok, msg = sm.delete_combo(int(pid), self.local, usuario=self.username)
        else:
            ok, msg = qa.execute_delete_product(
                {"producto_id": pid, "local": self.local, "usuario": self.username}
            )
        if ok:
            self._show_toast("Producto eliminado")
            self.load_data()
        else:
            QMessageBox.critical(
                self, "Error", msg or "No se pudo eliminar el producto."
            )

    def _on_delete_consolidated(self, ids_by_local: dict, nombre: str):
        """Elimina todos los productos de un conjunto consolidado."""
        if self.role != "admin":
            QMessageBox.warning(
                self, "Permisos", "Solo un administrador puede eliminar productos."
            )
            return
        if not ids_by_local:
            return
        confirm = QMessageBox.question(
            self,
            "Eliminar producto",
            f"¿Eliminar '{nombre}' de todos los locales?\nEsta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        errors = []
        for loc, pid in ids_by_local.items():
            if not pid:
                continue
            prod = self._products_by_id.get(pid) or {}
            is_combo = int(prod.get("is_combo") or 0) == 1
            if is_combo:
                ok, msg = sm.delete_combo(int(pid), loc, usuario=self.username)
            else:
                ok, msg = qa.execute_delete_product(
                    {
                        "producto_id": pid,
                        "local": loc,
                        "usuario": self.username,
                    }
                )
            if not ok:
                errors.append(f"{loc}: {msg}")
        if errors:
            QMessageBox.warning(
                self,
                "Error",
                "No se pudieron eliminar algunos productos:\n" + "\n".join(errors),
            )
        else:
            self._show_toast("Productos eliminados")
        self.load_data()

    def on_item_changed(self, item):
        """Maneja cambios en items de la tabla (inline editing) con feedback visual"""
        if not self._can_edit_stock():
            return
        if not self._editing_enabled:
            return
        if self._is_consolidated_view():
            # Permitir edición de cantidades por admin en la vista consolidada
            try:
                if self.role == "admin":
                    row = item.row()
                    col = item.column()
                    start_col = getattr(self, "_consolidated_local_col_start", 10)
                    if col == 8:  # Código
                        new_value = item.text().strip()
                        if not new_value:
                            new_value = None
                        meta = self._consolidated_meta.get(row, {})
                        ids_by_local = meta.get("ids_by_local", {})
                        if not ids_by_local:
                            return
                        success_count = 0
                        last_error = ""
                        for loc, pid in ids_by_local.items():
                            if not pid:
                                continue
                            try:
                                # Usar el local real del producto (evita mismatch con el header)
                                prod = (
                                    self._products_by_id.get(pid)
                                    if hasattr(self, "_products_by_id")
                                    else None
                                )
                                target_loc = (
                                    prod.get("local")
                                    if isinstance(prod, dict)
                                    else None
                                ) or loc
                                if (
                                    isinstance(target_loc, str)
                                    and target_loc.strip().lower() == "sin local"
                                ):
                                    target_loc = ""
                                payload = {
                                    "producto_id": pid,
                                    "field": "codigo",
                                    "value": new_value,
                                    "usuario": self.username,
                                    "local": target_loc,
                                    "motivo": "edicion manual",
                                }
                                ok, err = qa.execute_update_field(payload)
                                if ok:
                                    success_count += 1
                                else:
                                    last_error = f"{loc}: {err}"
                            except Exception as e:
                                last_error = f"{loc}: {e}"
                        if success_count > 0:
                            self._show_toast(
                                f"✅ Código actualizado en {success_count} locales"
                            )
                            self._reset_edit_timeout()
                        else:
                            msg = last_error or "No se pudo actualizar"
                            self._show_toast(
                                f"❌ Error actualizando código: {msg}", persistent=True
                            )
                            item.setBackground(QColor(239, 68, 68, 30))

                        # Quitar highlight
                        self._queue_highlight_clear(row, col)
                        return

                    # Solo manejar edición de cantidades de locales
                    elif col >= start_col:
                        new_value = item.text().strip()
                        if not new_value or new_value == "-":
                            return
                        try:
                            new_qty = int(new_value)
                        except ValueError:
                            self._show_toast("❌ Cantidad inválida", persistent=True)
                            item.setBackground(QColor(239, 68, 68, 30))
                            return

                        # Identificar local desde el encabezado
                        header_item = self.table.horizontalHeaderItem(col)
                        loc = header_item.text() if header_item else None

                        meta = self._consolidated_meta.get(row, {})
                        old_qty = int(meta.get("locales", {}).get(loc, 0))

                        pid = item.data(Qt.ItemDataRole.UserRole)
                        # Si existe producto en ese local, aplicar incremento
                        if pid:
                            delta = new_qty - old_qty
                            if delta != 0:
                                payload = {
                                    "producto_id": pid,
                                    "delta": delta,
                                    "usuario": self.username,
                                    "local": loc,
                                    "detalle": "Edición consolidada",
                                    "nombre": self.table.item(row, 0).text()
                                    if self.table.item(row, 0)
                                    else "",
                                    "categoria": self.table.item(row, 2).text()
                                    if self.table.item(row, 2)
                                    else "",
                                    "medida": self.table.item(row, 5).text()
                                    if self.table.item(row, 5)
                                    else "",
                                    "estado": self.table.item(row, 6).text()
                                    if self.table.item(row, 6)
                                    else "",
                                }
                                qa.execute_increment(payload)
                                # Actualizar cache consolidada
                                try:
                                    self._consolidated_meta[row]["locales"][
                                        loc
                                    ] = new_qty
                                except Exception:
                                    pass
                                self._log_audit(pid, "cantidad", old_qty, new_qty)
                                self._show_toast(f"✅ Cantidad actualizada: {new_qty}")
                                self._reset_edit_timeout()
                        else:
                            # No hay producto en ese local: crear si cantidad>0
                            if new_qty > 0:
                                sample_id = meta.get("sample_id")
                                sample = (
                                    self._products_by_id.get(sample_id, {})
                                    if sample_id is not None
                                    else {}
                                )
                                payload = {
                                    "nombre": sample.get("nombre")
                                    or self.table.item(row, 0).text(),
                                    "material": sample.get("material"),
                                    "categoria": sample.get("categoria"),
                                    "medida": sample.get("medida"),
                                    "codigo": sample.get("codigo") or None,
                                    "estado": sample.get("estado"),
                                    "color": sample.get("color") or None,
                                    "fabricante": sample.get("fabricante"),
                                    "precio_costo": sample.get("precio_costo") or 0,
                                    "precio_venta": sample.get("precio_venta") or 0,
                                    "cantidad": new_qty,
                                    "local": loc,
                                    "usuario": self.username,
                                    "force_update": False,
                                }
                                success, msg = qa.execute_add_product(payload)
                                if success:
                                    self._show_toast(
                                        f"✅ Producto agregado en {loc} con cantidad {new_qty}"
                                    )
                                    # Recargar para sincronizar ids y vistas
                                    self._schedule_reload(0)
                                else:
                                    self._show_toast(
                                        f"❌ No se pudo crear producto: {msg}",
                                        persistent=True,
                                    )
                        # Quitar highlight de forma segura
                        row_idx, col_idx = item.row(), item.column()
                        self._queue_highlight_clear(row_idx, col_idx)
                        return
            except Exception as e:
                logger.error(f"Error handling consolidated edit: {e}")
                return
            return

        try:
            row = item.row()
            col = item.column()

            # Obtener el ID del producto
            name_item = self.table.item(row, 0)
            if not name_item:
                return
            product_id = name_item.data(Qt.ItemDataRole.UserRole)
            if not product_id:
                return

            # Obtener el nuevo valor
            new_value = item.text().strip()
            if not new_value or new_value == "-":
                return

            # Mapeo de columnas habilitadas para edición inline
            field_map = {7: "cantidad"}
            if col not in field_map:
                return

            field = field_map[col]

            # Obtener producto completo
            product = self._products_by_id.get(product_id, {})

            # Guardar valor anterior para auditoría y revertir si falla
            old_value = product.get(field)

            # ========== PRODUCCIÓN: VALIDACIONES CRÍTICAS ==========

            # 1. VERIFICAR PERMISOS
            has_permission, permission_msg = self._check_edit_permission(col, field)
            if not has_permission:
                self._show_toast(f"🔒 {permission_msg}", persistent=True)
                item.setBackground(QColor(239, 68, 68, 50))  # Rojo
                # Revertir valor
                self._editing_enabled = False
                item.setText(str(old_value) if old_value is not None else "-")
                self._editing_enabled = True
                return

            # Conflictos deshabilitados en modo Supabase

            # 3. VALIDAR REGLAS DE NEGOCIO
            is_valid, validation_msg = self._validate_business_rules(
                field, new_value, product
            )
            if not is_valid:
                self._show_toast(validation_msg, persistent=True)
                item.setBackground(QColor(239, 68, 68, 50))  # Rojo
                # Revertir valor
                self._editing_enabled = False
                item.setText(str(old_value) if old_value is not None else "-")
                self._editing_enabled = True
                return

            # Feedback inmediato (procesando)
            item.setBackground(QColor(255, 193, 7, 30))  # Amarillo suave

            # Procesar según tipo de campo
            if col == 7:  # Cantidad - usar increment
                try:
                    new_qty = int(new_value)
                    old_qty = int(
                        self._products_by_id.get(product_id, {}).get("cantidad", 0)
                    )
                    # Confirmación desactivada: permitir dejar en 0 sin mensaje
                    delta = new_qty - old_qty

                    if delta != 0:
                        payload = {
                            "producto_id": product_id,
                            "delta": delta,
                            "usuario": self.username,
                            "local": self.local,
                            "detalle": "Edición inline",
                            "nombre": self.table.item(row, 0).text(),
                            "categoria": self.table.item(row, 2).text(),
                            "medida": self.table.item(row, 3).text(),
                            "estado": self.table.item(row, 6).text(),
                        }
                        qa.execute_increment(payload)

                        # AUDITORÍA
                        self._log_audit(product_id, "cantidad", old_qty, new_qty)

                        self._show_toast(f"✅ Cantidad actualizada: {new_qty}")
                        self._reset_edit_timeout()

                        # Actualizar cache local
                        if product_id in self._products_by_id:
                            self._products_by_id[product_id]["cantidad"] = new_qty
                except ValueError:
                    self._show_toast("❌ Cantidad inválida", persistent=True)
                    item.setBackground(QColor(239, 68, 68, 30))  # Rojo suave
                    return

            elif col == 8:  # Precio - limpiar formato (CORREGIDO índice tras Color)
                try:
                    # Remover $ y puntos
                    clean_price = (
                        new_value.replace("$", "").replace(".", "").replace(",", "")
                    )
                    new_price = int(clean_price)

                    if new_price <= 0:
                        raise ValueError("Precio debe ser mayor a 0")

                    payload = {
                        "producto_id": product_id,
                        "field": "precio_venta",
                        "value": new_price,
                        "usuario": self.username,
                        "local": self.local,
                        "motivo": "edición inline",
                        "nombre": self.table.item(row, 0).text(),
                        "categoria": self.table.item(row, 2).text(),
                        "medida": self.table.item(row, 3).text(),
                        "estado": self.table.item(row, 6).text(),
                    }
                    qa.execute_update_field(payload)

                    # AUDITORÍA
                    self._log_audit(product_id, "precio_venta", old_value, new_price)

                    # Reformatear el precio
                    formatted = f"${new_price:,.0f}".replace(",", ".")
                    self._editing_enabled = False
                    item.setText(formatted)
                    item.setBackground(QColor(16, 185, 129, 30))  # Verde suave
                    self._editing_enabled = True

                    self._show_toast(f"✅ Precio actualizado: {formatted}")
                    self._reset_edit_timeout()

                    # Actualizar cache local
                    if product_id in self._products_by_id:
                        self._products_by_id[product_id]["precio_venta"] = new_price

                except ValueError as e:
                    self._show_toast(f"❌ Precio inválido: {str(e)}", persistent=True)
                    item.setBackground(QColor(239, 68, 68, 30))  # Rojo suave
                    return

            else:  # medida / estado
                payload = {
                    "producto_id": product_id,
                    "field": field,
                    "value": new_value,
                    "usuario": self.username,
                    "local": self.local,
                    "motivo": "ediciOKn inline",
                    "nombre": self.table.item(row, 0).text(),
                    "categoria": self.table.item(row, 2).text(),
                    "medida": new_value
                    if field == "medida"
                    else self.table.item(row, 3).text(),
                    "estado": new_value
                    if field == "estado"
                    else self.table.item(row, 6).text(),
                }
                qa.execute_update_field(payload)

                # AUDITOROKA
                self._log_audit(product_id, field, old_value, new_value)

                item.setBackground(QColor(16, 185, 129, 30))  # Verde suave
                self._show_toast(f"OK {field.capitalize()} actualizado")
                self._reset_edit_timeout()

                # Actualizar cache local
                if product_id in self._products_by_id:
                    self._products_by_id[product_id][field] = new_value

            # Quitar highlight de forma segura (el item puede ser reemplazado)
            row_idx, col_idx = item.row(), item.column()
            self._queue_highlight_clear(row_idx, col_idx)

        except Exception as e:
            logger.error(f"Error in on_item_changed: {e}")
            self._show_toast(f"❌ Error: {str(e)}", persistent=True)
            if item:
                item.setBackground(QColor(239, 68, 68, 30))

    def load_data(self):
        """Inicia la carga de datos en background"""
        try:
            t0 = time.perf_counter()
            self._apply_view_mode()
            if self._get_stock_view_mode() != "Disponibles":
                self._load_reservas_sections()
                return
            # Si admin selecciona "Todos los locales", mostrar inventario consolidado (background)
            if self._is_consolidated_view():
                if (
                    getattr(self, "_consolidated_thread", None)
                    and self._consolidated_thread.isRunning()
                ):
                    self._pending_reload = True
                    return
                if not hasattr(self, "_load_counter"):
                    self._load_counter = 0
                self._load_counter += 1
                load_id = self._load_counter
                self._current_load_id = load_id
                self.loading_bar.setVisible(True)
                self._consolidated_thread = ConsolidatedInventoryThread(self, load_id)
                self._consolidated_thread.data_ready.connect(
                    self._on_consolidated_loaded
                )
                self._consolidated_thread.start()
                return
            self._apply_local_table_schema()

            # Obtener filtros
            force_filters = getattr(self, "_force_next_filters", None)
            if force_filters:
                search = force_filters.get("search", "")
                categoria = force_filters.get("categoria", "")
                fabricante = force_filters.get("fabricante", "")
                color = force_filters.get("color", "")
                estado = force_filters.get("estado", "")
                medidas = force_filters.get("medidas", []) or []
                codigo = force_filters.get("codigo", "") or ""
                try:
                    delattr(self, "_force_next_filters")
                except Exception:
                    pass
            else:
                search = ""
                if hasattr(self, "search_input"):
                    search = self.search_input.text()

                categoria = ""
                if hasattr(self, "category_combo"):
                    categoria = self.category_combo.currentText()
                    if categoria in ("Todas las categorias", "Todas las categorías"):
                        categoria = ""

                fabricante = ""
                if hasattr(self, "fabricante_combo"):
                    fabricante = self.fabricante_combo.currentText()
                    if fabricante == "Fabricante":
                        fabricante = ""

                color = ""

                estado = ""
                if hasattr(self, "estado_filter"):
                    estado = self.estado_filter.currentText()
                    if estado == "Estado":
                        estado = ""

                color = ""
                if hasattr(self, "color_combo"):
                    color = self.color_combo.currentText()
                    if color == "Color":
                        color = ""

                # Leer medida del combo dinámico o código según el modo seleccionado
                medidas = []
                codigo = ""
                if getattr(self, "_filter_measure_mode", "metro") == "codigo":
                    if hasattr(self, "codigo_input"):
                        codigo = (self.codigo_input.text() or "").strip()
                else:
                    if (
                        hasattr(self, "filter_medida_combo")
                        and self.filter_medida_combo.currentIndex() > 0
                    ):
                        medida_text = self.filter_medida_combo.currentText()
                        if medida_text and medida_text != "Medida":
                            medidas.append(medida_text)

            # Si se pidió un reset forzado, ignorar los controles y usar filtros vacíos
            if getattr(self, "_force_clear_filters", False):
                try:
                    search = ""
                    categoria = ""
                    fabricante = ""
                    color = ""
                    estado = ""
                    medidas = []
                    codigo = ""
                finally:
                    try:
                        delattr(self, "_force_clear_filters")
                    except Exception:
                        try:
                            del self._force_clear_filters
                        except Exception:
                            pass

            sig = None
            # Firmas para evitar recargas redundantes
            try:
                sig = (
                    self.view_local,
                    self._get_stock_view_mode(),
                    search,
                    categoria,
                    fabricante,
                    estado,
                    tuple(medidas),
                    codigo,
                    color,
                )
                self._last_filter_signature = sig
                self._last_requested_signature = sig
            except Exception:
                pass

            self._update_filter_chip()

            # Refrescar opciones de filtros con datos del local actual
            if getattr(self, "_skip_refresh_filters_once", False):
                try:
                    delattr(self, "_skip_refresh_filters_once")
                except Exception:
                    pass
            else:
                if getattr(self, "role", "") != "admin":
                    self.refresh_filter_options()

            # Evitar recargas duplicadas si ya hay una carga en curso
            if (
                hasattr(self, "loading_thread")
                and self.loading_thread
                and self.loading_thread.isRunning()
            ):
                if sig is not None and sig == getattr(
                    self, "_last_active_signature", None
                ):
                    return
                self._pending_reload = True
                return

            # Iniciar thread con identificador para descartar resultados obsoletos
            if not hasattr(self, "_load_counter"):
                self._load_counter = 0
            self._load_counter += 1
            load_id = self._load_counter
            self._current_load_id = load_id
            if sig is not None:
                self._last_active_signature = sig
            logger.info(
                f"load_data: starting thread with search='{search}' cat='{categoria}' fab='{fabricante}' medidas={medidas}"
            )
            logger.info(
                f"load_data: color='{color}' codigo='{codigo}' estado='{estado}'"
            )

            # Mostrar cache inmediato para reducir tiempo de espera percibido
            try:
                cached = offline_store.get_cached_stock(
                    self.view_local,
                    search,
                    categoria,
                    estado,
                    medidas,
                    fabricante,
                    color,
                    codigo,
                )
                if cached:
                    self.on_data_loaded(load_id, cached)
            except Exception:
                pass

            self.loading_thread = LoadingThread(
                self.view_local,
                search,
                categoria,
                medidas,
                fabricante,
                estado,
                color,
                codigo,
                load_id=load_id,
                apply_reservas=self._use_reservas_stock(),
                parent=self,
            )
            self.loading_thread.data_loaded.connect(self.on_data_loaded)
            try:
                self.loading_thread.error_occurred.connect(self._on_loading_error)
            except Exception:
                pass
            self.loading_thread.start()
            # sincronizar combos en background (no bloquea UI)
            self._trigger_combo_sync()
            logger.info(
                f"load_data: setup done in {time.perf_counter() - t0:.3f}s (local={self.view_local})"
            )

        except Exception as e:
            logger.error(f"Error starting load_data: {e}")

    def _build_consolidated_inventory(self):
        """Construye lista consolidada (una fila por producto con columnas por local)."""
        try:
            locales_raw = get_all_locals() or []
            products = []
            if locales_raw:
                for loc in locales_raw:
                    products.extend(sm.get_stock_filtered(loc, "", "", ""))
            else:
                products.extend(sm.get_stock_filtered("", "", "", ""))
        except Exception as e:
            logger.error(f"Error cargando inventario consolidado: {e}")
            locales_raw = []
            products = []

        locales = list(
            dict.fromkeys([(loc or "").strip() or "Sin local" for loc in locales_raw])
        )
        # Columnas fijas: Producto, Material, Categoría, Fabricante, Color, Medida, Estado, Precio venta, Código, Total
        headers = [
            "Producto",
            "Material",
            "Categoría",
            "Fabricante",
            "Color",
            "Medida",
            "Estado",
            "Precio venta",
            "Código",
            "Total",
        ] + locales
        # añadir columna de acción al final cuando estamos en vista consolidada
        headers.append("Eliminar")

        merged = {}
        products_by_id = {}
        for p in products:
            pid = p.get("id")
            if pid is not None:
                products_by_id[pid] = p
            codigo = p.get("codigo") or ""
            precio_venta = int(p.get("precio_venta") or 0)
            is_combo_val = int(p.get("is_combo") or 0)
            key = (
                p.get("nombre") or "",
                p.get("material") or "",
                p.get("categoria") or "",
                p.get("fabricante") or "",
                p.get("color") or "",
                p.get("medida") or "",
                p.get("estado") or "",
                precio_venta,
                codigo,
                is_combo_val,
            )
            entry = merged.setdefault(
                key,
                {
                    "locales": {},
                    "ids_by_local": {},
                    "sample_id": None,
                    "precio_costo": p.get("precio_costo") or 0,
                    "precio_venta": p.get("precio_venta") or 0,
                    "codigo": p.get("codigo") or "",
                    "updated_at": p.get("updated_at") or "",
                    "is_combo": is_combo_val,
                },
            )
            loc = (p.get("local") or "").strip() or "Sin local"
            qty = int(p.get("cantidad") or 0)
            entry["locales"][loc] = entry["locales"].get(loc, 0) + qty
            if pid is not None and loc not in entry["ids_by_local"]:
                entry["ids_by_local"][loc] = pid
            if entry["sample_id"] is None and pid is not None:
                entry["sample_id"] = pid
            if not entry["precio_costo"]:
                entry["precio_costo"] = p.get("precio_costo") or 0
            if not entry["precio_venta"]:
                entry["precio_venta"] = p.get("precio_venta") or 0
            if not entry["codigo"]:
                entry["codigo"] = p.get("codigo") or ""
            upd = str(p.get("updated_at") or "")
            if upd > entry["updated_at"]:
                entry["updated_at"] = upd

        rows = list(merged.items())
        skip_client = getattr(self, "_skip_client_filters_once", False)
        if skip_client:
            try:
                delattr(self, "_skip_client_filters_once")
            except Exception:
                pass
        else:
            # gather all filter values from UI controls (same as load_data)
            search = ""
            if hasattr(self, "search_input"):
                search = (self.search_input.text() or "").strip().lower()
            search_terms = _split_search_terms(search)

            categoria_filter = ""
            if hasattr(self, "category_combo"):
                raw_categoria = (self.category_combo.currentText() or "").strip()
                if raw_categoria and not raw_categoria.lower().startswith("todas"):
                    categoria_filter = raw_categoria

            fabricante_filter = ""
            if hasattr(self, "fabricante_combo"):
                fab = self.fabricante_combo.currentText()
                if fab and fab != "Fabricante":
                    fabricante_filter = fab

            estado_filter = ""
            if hasattr(self, "estado_filter"):
                ef = self.estado_filter.currentText()
                if ef and ef != "Estado":
                    estado_filter = ef

            color_filter = ""
            if hasattr(self, "color_combo"):
                cf = self.color_combo.currentText()
                if cf and cf != "Color":
                    color_filter = cf

            # Leer medida del combo dinámico o código según el modo seleccionado
            medidas = []
            codigo_filter = ""
            if getattr(self, "_filter_measure_mode", "metro") == "codigo":
                if getattr(self, "codigo_input", None):
                    codigo_filter = (self.codigo_input.text() or "").strip().lower()
            else:
                if (
                    hasattr(self, "filter_medida_combo")
                    and self.filter_medida_combo.currentIndex() > 0
                ):
                    medida_text = self.filter_medida_combo.currentText()
                    if medida_text and medida_text != "Medida":
                        medidas.append(medida_text)

            # Buscar por prefijo de palabra en nombre del producto
            if search_terms:
                rows = [
                    (key, data)
                    for key, data in rows
                    if _match_terms_prefix((key[0] or ""), search_terms)
                ]

            if categoria_filter:
                cat_norm = categoria_filter.strip().lower()
                rows = [
                    (key, data)
                    for key, data in rows
                    if str(key[2] or "").strip().lower() == cat_norm
                ]

            if fabricante_filter:
                fab_norm = fabricante_filter.strip().lower()
                rows = [
                    (key, data)
                    for key, data in rows
                    if str(key[3] or "").strip().lower() == fab_norm
                ]

            if estado_filter:
                est_norm = estado_filter.strip().lower()
                rows = [
                    (key, data)
                    for key, data in rows
                    if str(key[6] or "").strip().lower() == est_norm
                ]

            if color_filter:
                col_norm = color_filter.strip().lower()
                rows = [
                    (key, data)
                    for key, data in rows
                    if str(key[4] or "").strip().lower() == col_norm
                ]

            if medidas:
                # match any medida exactly
                rows = [
                    (key, data)
                    for key, data in rows
                    if any(m == (key[5] or "") for m in medidas)
                ]

            if codigo_filter:
                rows = [
                    (key, data)
                    for key, data in rows
                    if codigo_filter in (data.get("codigo") or "").lower()
                ]
        return headers, rows, products_by_id

    def _load_consolidated_inventory(self):
        """Pone en la tabla el inventario consolidado (solo lectura)."""
        headers, rows, products_by_id = self._build_consolidated_inventory()
        self._apply_consolidated_inventory(headers, rows, products_by_id)

    def _on_consolidated_loaded(self, headers, rows, products_by_id, load_id: int):
        if int(load_id or 0) != int(getattr(self, "_current_load_id", 0) or 0):
            return
        self._apply_consolidated_inventory(headers, rows, products_by_id)
        # Refrescar opciones de filtros después de cargar datos consolidados
        try:
            self._apply_filter_options(list((products_by_id or {}).values()))
        except Exception:
            self.refresh_filter_options()
        if getattr(self, "_pending_reload", False):
            self._pending_reload = False
            self.load_data()

    def _on_loading_error(self, msg: str):
        """Handler para errores de carga provenientes del LoadingThread."""
        try:
            logger.error(f"LoadingThread reported error: {msg}")
        except Exception:
            pass
        try:
            # Mostrar mensaje persistente para que el usuario sepa que hubo un error
            self._show_toast(f"Error cargando stock: {msg}", persistent=True)
        except Exception:
            pass

    def _apply_consolidated_inventory(self, headers, rows, products_by_id):
        self.loading_bar.setVisible(False)
        self.table.setRowCount(0)
        self.table.clearContents()
        self._editing_enabled = False

        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        self._products_by_id = products_by_id or {}
        self._row_by_product_id = {}
        self._consolidated_meta = {}

        # Las primeras 10 columnas (0..9) son fijas; las columnas por local empiezan en el índice 10
        # pero el último header puede ser el botón "Eliminar" y no debe contarse como local.
        locales = headers[10:]
        delete_col = None
        if locales and locales[-1] == "Eliminar":
            delete_col = len(headers) - 1
            locales = locales[:-1]
        self._consolidated_local_col_start = (
            len(headers) - len(locales) - (1 if delete_col is not None else 0)
        )

        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setStretchLastSection(False)
        # ajustar anchos para columnas base
        base_widths = {
            0: 240,  # Producto
            1: 140,  # Material
            2: 160,  # Categoria
            3: 140,  # Fabricante
            4: 120,  # Color
            5: 140,  # Medida
            6: 130,  # Estado
            7: 140,  # Precio venta
            8: 110,  # Codigo
            9: 110,  # Total
        }
        header.blockSignals(True)
        for col, w in base_widths.items():
            if col < self.table.columnCount():
                header.resizeSection(col, w)
        for col in range(self._consolidated_local_col_start, self.table.columnCount()):
            header.resizeSection(col, 90)
        header.blockSignals(False)
        self._load_table_prefs()

        for idx, (key, data) in enumerate(rows):
            (
                nombre,
                material,
                categoria,
                fabricante,
                color,
                medida,
                estado,
                precio_key,
                codigo_key,
                is_combo_val,
            ) = key
            precio_costo = float(data.get("precio_costo") or 0)
            precio_venta = float(
                precio_key if precio_key is not None else data.get("precio_venta") or 0
            )
            codigo = data.get("codigo") or codigo_key or ""
            updated_at = data.get("updated_at") or ""
            total_qty = sum(data["locales"].values())
            margen = precio_venta - precio_costo
            ganancia_total = margen * total_qty
            pct_ganancia = (margen / precio_costo * 100) if precio_costo else 0
            sample_id = data.get("sample_id")
            ids_by_local = data.get("ids_by_local", {})
            if sample_id is not None:
                self._row_by_product_id[sample_id] = idx
            self._consolidated_meta[idx] = {
                "sample_id": sample_id,
                "ids_by_local": ids_by_local,
                "locales": dict(data.get("locales", {})),
            }

            def _set(col, text, editable=False, user_role=None):
                item = QTableWidgetItem(str(text))
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if user_role is not None:
                    item.setData(Qt.ItemDataRole.UserRole, user_role)
                self.table.setItem(idx, col, item)

            _set(0, nombre, user_role=sample_id)
            _set(1, material)
            _set(2, categoria)
            _set(3, fabricante or "")
            _set(4, color)
            _set(5, medida)
            _set(6, estado)
            _set(7, f"${precio_venta:,.0f}".replace(",", "."))
            _set(8, codigo, editable=self.role == "admin")
            _set(9, total_qty)
            for col_idx, loc in enumerate(
                locales, start=self._consolidated_local_col_start
            ):
                qty = data["locales"].get(loc, 0)
                editable = self.role == "admin"
                pid = ids_by_local.get(loc)
                _set(col_idx, qty, editable=editable, user_role=pid)
            # botón de eliminación al final
            if headers and headers[-1] == "Eliminar":
                btn = QPushButton("Eliminar")
                btn.setStyleSheet("background: #DC2626; color: white;")
                # Solo admin puede pulsarlo
                btn.setEnabled(self.role == "admin")

                # guardar copia de ids_by_local y nombre para el callback
                def make_handler(ids, name):
                    return lambda checked=False: self._on_delete_consolidated(ids, name)

                btn.clicked.connect(make_handler(dict(ids_by_local), nombre))
                self.table.setCellWidget(idx, len(headers) - 1, btn)

        self._load_reservas_sections()

    def on_data_loaded(self, load_id, products):
        """Puebla la tabla con los productos cargados."""
        try:
            t0 = time.perf_counter()
            if getattr(self, "_ignore_next_load_result", False):
                try:
                    delattr(self, "_ignore_next_load_result")
                except Exception:
                    pass
                return
            # Ignorar resultados de cargas antiguas
            if hasattr(self, "_current_load_id") and load_id != getattr(
                self, "_current_load_id"
            ):
                return
            self.loading_bar.setVisible(False)
            table = self.table
            prev_sorting = False
            try:
                prev_sorting = table.isSortingEnabled()
            except Exception:
                prev_sorting = False
            try:
                table.setSortingEnabled(False)
            except Exception:
                pass
            table.blockSignals(True)
            table.setRowCount(0)
            self._editing_enabled = False
            self._products_by_id = {}
            self._row_by_product_id = {}
            self._duplicate_keys: dict = {}

            products = list(products or [])
            logger.info(
                f"on_data_loaded: received {len(products)} rows in {time.perf_counter() - t0:.3f}s"
            )
            # Protecciones: evitar pintar demasiadas filas de golpe
            try:
                max_rows = int(os.environ.get("MANAREY_MAX_ROWS", "2000"))
            except Exception:
                max_rows = 2000
            if len(products) > max_rows:
                try:
                    logger.warning(
                        f"on_data_loaded: truncating products list from {len(products)} to {max_rows} rows to avoid UI freeze"
                    )
                except Exception:
                    pass
                try:
                    self._show_toast(
                        f"Mostrando primeros {max_rows} productos (usar filtros o paginación)",
                        persistent=True,
                    )
                except Exception:
                    pass
                products = products[:max_rows]

            # Desactivar updates visuales mientras llenamos la tabla por chunks
            try:
                self.table.setUpdatesEnabled(False)
            except Exception:
                pass
            # Aviso de conexión (solo si no hay datos)
            try:
                if not products and not offline_store.is_online():
                    now_ts = time.time()
                    last_ts = getattr(self, "_last_offline_warn_ts", 0) or 0
                    if now_ts - last_ts > 30:
                        self._last_offline_warn_ts = now_ts
                        self._show_toast(
                            "⚠️ Sin conexión a la base de datos", persistent=True
                        )
            except Exception:
                pass
            skip_client = getattr(self, "_skip_client_filters_once", False)
            if skip_client:
                try:
                    delattr(self, "_skip_client_filters_once")
                except Exception:
                    pass

            if not skip_client:
                search_terms = _split_search_terms(
                    (self.search_input.text() if hasattr(self, "search_input") else "")
                )
                if search_terms:
                    products = [
                        p
                        for p in products
                        if _match_terms_prefix((p.get("nombre") or ""), search_terms)
                    ]
            total = len(products)

            self._pending_products = list(products)
            self._pending_fill_index = 0
            self._actions_lazy = True
            # Ajustar tamaño de chunk según volumen para evitar bloqueos
            self._fill_chunk_size = 20 if total >= 2000 else 60
            self._skip_duplicate_highlight = total >= 1200

            row_h = int(self.table.verticalHeader().defaultSectionSize() or 40)
            try:
                visible_rows = max(
                    10, int(self.table.viewport().height() / max(1, row_h))
                )
            except Exception:
                visible_rows = 30
            first_chunk = max(80, visible_rows + 20)
            self._fill_table_chunk(first_chunk)

            self._editing_enabled = True
            self._actions_lazy = True
            self._update_filter_chip()
            if getattr(self, "role", "") != "admin":
                self._load_reservas_sections()
            if getattr(self, "_pending_reload", False):
                self._pending_reload = False
                self.load_data()
            try:
                table.setSortingEnabled(prev_sorting)
            except Exception:
                pass
            table.blockSignals(False)
            try:
                logger.info(
                    f"on_data_loaded: done rows={total} active_threads={QThread.activeThreadCount()}"
                )
            except Exception:
                pass
            try:
                table.viewport().setUpdatesEnabled(True)
            except Exception:
                pass
            table.setUpdatesEnabled(True)
        except Exception as e:
            logger.error(f"Error en on_data_loaded: {e}")
            self._show_toast(f"❌ Error cargando datos: {e}", persistent=True)

    def _schedule_reload(self, ms: int = 0):
        """Agenda una recarga de datos sin bloquear la UI."""
        try:
            if ms and ms > 0:
                if hasattr(self, "_reload_timer"):
                    self._reload_timer.start(int(ms))
                else:
                    self.load_data()
            else:
                self.load_data()
        except Exception:
            self.load_data()

    def _stop_thread_safe(self, thread, wait_ms: int = 800):
        try:
            if thread and thread.isRunning():
                try:
                    thread.requestInterruption()
                except Exception:
                    pass
                try:
                    thread.wait(int(wait_ms))
                except Exception:
                    pass
        except Exception:
            pass

    def _highlight_duplicates(self):
        """Resalta filas duplicadas detectadas por clave exacta."""
        try:
            t0 = time.perf_counter()
            if getattr(self, "_skip_duplicate_highlight", False):
                logger.info("_highlight_duplicates: skipped")
                return
            dup_color = QColor(251, 191, 36, 40)
            for rows in self._duplicate_keys.values():
                if len(rows) <= 1:
                    continue
                for r in rows:
                    for c in range(
                        min(self.table.columnCount(), 10)
                    ):  # datos principales
                        item = self.table.item(r, c)
                        if item:
                            item.setBackground(dup_color)
        except Exception:
            pass

    def _update_filter_chip(self):
        """Muestra filtros activos como chip compacto."""
        try:
            active = []
            if getattr(self, "search_input", None) and self.search_input.text().strip():
                active.append(f"busca='{self.search_input.text().strip()}'")
            if (
                getattr(self, "category_combo", None)
                and self.category_combo.currentIndex() > 0
            ):
                active.append(f"cat={self.category_combo.currentText()}")
            if (
                getattr(self, "estado_filter", None)
                and self.estado_filter.currentIndex() > 0
            ):
                active.append(f"estado={self.estado_filter.currentText()}")
            if (
                getattr(self, "color_combo", None)
                and self.color_combo.currentIndex() > 0
            ):
                active.append(f"color={self.color_combo.currentText()}")
            if (
                getattr(self, "fabricante_combo", None)
                and self.fabricante_combo.currentIndex() > 0
            ):
                active.append(f"fab={self.fabricante_combo.currentText()}")
            if getattr(self, "_filter_measure_mode", "metro") == "codigo":
                if (
                    getattr(self, "codigo_input", None)
                    and self.codigo_input.text().strip()
                ):
                    active.append(f"codigo={self.codigo_input.text().strip()}")
            else:
                if (
                    getattr(self, "filter_medida_combo", None)
                    and self.filter_medida_combo.currentIndex() > 0
                ):
                    active.append(self.filter_medida_combo.currentText())
            for combo in (
                getattr(self, "plaza_combo", None),
                getattr(self, "rodado_combo", None),
                getattr(self, "pulgada_combo", None),
                getattr(self, "medida_combo", None),
            ):
                if combo and combo.currentIndex() > 0:
                    active.append(combo.currentText())
            text = " | ".join(active) if active else "Sin filtros"
            if hasattr(self, "filter_chip_label"):
                self.filter_chip_label.setText(text)
        except Exception:
            pass

    # Paginación eliminada

    def _load_table_prefs(self):
        """Carga orden y anchos de columnas desde prefs."""
        return

    def _save_table_prefs(self, *args):
        """Guarda orden y anchos de columnas en prefs."""
        return

        """Guarda orden y anchos de columnas en prefs."""
        # Columnas fijas: no persistimos anchos/orden.
        return
        try:
            header = self.table.horizontalHeader()
            order = [header.logicalIndex(i) for i in range(header.count())]
            widths = {i: header.sectionSize(i) for i in range(header.count())}
            data = {}
            if PREFS_PATH.exists():
                try:
                    data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
                except Exception:
                    data = {}
            key = "stock_table_all" if self._is_consolidated_view() else "stock_table"
            data[key] = {"order": order, "widths": widths}
            _ensure_prefs_dir()
            PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_window_state(self):
        """Restaura geometría/tamaño recordado."""
        try:
            if not PREFS_PATH.exists():
                return
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
            prefs = data.get("stock_window", {})
            geo = prefs.get("geometry")
            if geo:
                arr = QByteArray.fromBase64(geo.encode())
                if not arr.isEmpty():
                    self.restoreGeometry(arr)
            if prefs.get("maximized"):
                self.showMaximized()
        except Exception as e:
            logger.debug(f"No se pudo restaurar ventana: {e}")

    def _persist_window_state(self):
        """Guarda geometría/tamaño."""
        try:
            data = {}
            if PREFS_PATH.exists():
                try:
                    data = json.loads(PREFS_PATH.read_text(encoding="utf-8") or "{}")
                except Exception:
                    data = {}
            data["stock_window"] = {
                "geometry": bytes(self.saveGeometry().toBase64()).decode(),
                "maximized": bool(self.isMaximized()),
            }
            _ensure_prefs_dir()
            PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"No se pudo guardar ventana: {e}")

    def add_action_buttons_new(self, row, product):
        """Agrega botones +, -, y Transferir - TAMAÑO OPTIMIZADO"""
        pid = product.get("id")
        is_combo = int(product.get("is_combo") or 0) == 1

        def _center_widget(btn: QPushButton) -> QWidget:
            container = QWidget()
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout = QGridLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(btn, 0, 0, Qt.AlignmentFlag.AlignCenter)
            return container

        # ===== BOTÓN + (col 12) =====
        plus_btn = QPushButton("+")
        try:
            plus_btn.setFixedSize(scale(26), scale(26))
        except Exception:
            plus_btn.setFixedSize(26, 26)
        plus_btn.setCursor(Qt.PointingHandCursor)
        plus_btn.setEnabled(
            self._can_edit_stock() and not self.read_only and not is_combo
        )
        if is_combo:
            plus_btn.setToolTip("Los combos no se ajustan manualmente")
        plus_btn.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #22C55E, stop:1 #16A34A);
                color: white;
                border: 2px solid #15803D;
                border-radius: 5px;
                font-weight: 900;
                font-size: 12px;
                padding: 0px;
                margin: 0px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #16A34A, stop:1 #15803D);
                border-color: #166534;
            }
            QPushButton:pressed { 
                background: #15803D;
                border-color: #166534;
            }
        """
        )
        plus_btn.clicked.connect(
            lambda checked=False, p=product: self.increment_product(p)
        )
        container_8 = _center_widget(plus_btn)
        container_8.setProperty("action_btn", plus_btn)
        self.table.setCellWidget(row, 9, container_8)

        # ===== BOTÓN - (col 12) =====
        minus_btn = QPushButton("-")
        try:
            minus_btn.setFixedSize(scale(26), scale(26))
        except Exception:
            minus_btn.setFixedSize(26, 26)
        minus_btn.setCursor(Qt.PointingHandCursor)
        minus_btn.setEnabled(
            self._can_edit_stock() and not self.read_only and not is_combo
        )
        if is_combo:
            minus_btn.setToolTip("Los combos no se ajustan manualmente")
        minus_btn.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #EF4444, stop:1 #DC2626);
                color: white;
                border: 2px solid #B91C1C;
                border-radius: 5px;
                font-weight: 900;
                font-size: 12px;
                padding: 0px;
                margin: 0px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #DC2626, stop:1 #B91C1C);
                border-color: #991B1B;
            }
            QPushButton:pressed { 
                background: #B91C1C;
                border-color: #991B1B;
            }
        """
        )
        minus_btn.clicked.connect(
            lambda checked=False, p=product: self.decrement_product(p)
        )
        container_9 = _center_widget(minus_btn)
        container_9.setProperty("action_btn", minus_btn)
        self.table.setCellWidget(row, 10, container_9)

        # Botón Transferir (col 12) - MÁS GRANDE Y VISIBLE
        transfer_btn = QPushButton("Transferir")
        try:
            transfer_btn.setFixedSize(scale(82), scale(28))
        except Exception:
            transfer_btn.setFixedSize(82, 28)
        transfer_btn.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FBBF24, stop:1 #F59E0B);
                color: #111827;
                border: 1px solid #D97706;
                border-radius: 8px;
                font-weight: 800;
                font-size: 11px;
                padding: 0px 6px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #F59E0B, stop:1 #D97706);
                border-color: #B45309;
            }
            QPushButton:pressed { background: #B45309; }
        """
        )
        transfer_btn.setEnabled((not self.read_only) and not is_combo)
        if is_combo:
            transfer_btn.setToolTip("No se puede transferir combos")
        transfer_btn.clicked.connect(
            lambda checked=False, p=product: self.transfer_product(p)
        )
        container_10 = _center_widget(transfer_btn)
        container_10.setProperty("action_btn", transfer_btn)
        self.table.setCellWidget(row, 11, container_10)

        # Botón Borrar (col 12) - solo admins
        if self.role == "admin":
            delete_btn = QPushButton("Eliminar")
            try:
                delete_btn.setFixedSize(scale(100), scale(40))
            except Exception:
                delete_btn.setFixedSize(100, 40)
            delete_btn.setCursor(Qt.PointingHandCursor)
            delete_btn.setStyleSheet(
                """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #f87171, stop:1 #ef4444);
                    color: white;
                    border: 2px solid #b91c1c;
                    border-radius: 8px;
                    font-weight: 700;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #ef4444, stop:1 #dc2626);
                    border-color: #991b1b;
                }
                QPushButton:pressed { background: #b91c1c; }
            """
            )
            delete_btn.setEnabled(self._can_edit_stock() and not self.read_only)
            delete_btn.clicked.connect(
                lambda checked=False, p=product: self.delete_product(p)
            )
            container_11 = _center_widget(delete_btn)
            container_11.setProperty("action_btn", delete_btn)
            self.table.setCellWidget(row, 12, container_11)

    def increment_product(self, product):
        """Incrementa cantidad de producto sin bloquear la UI."""
        if not self._can_edit_stock():
            QMessageBox.warning(
                self,
                "Editar stock",
                "IngresOK la contraseOKa con el botOKn Editar para modificar stock.",
            )
            return
        if self.read_only:
            QMessageBox.warning(
                self, "Solo lectura", "No puedes modificar el stock de otro local"
            )
            return
        if int(product.get("is_combo") or 0) == 1:
            QMessageBox.information(
                self, "Combo", "Los combos no se ajustan manualmente."
            )
            return
        if int(product.get("is_combo") or 0) == 1:
            QMessageBox.information(
                self, "Combo", "Los combos no se ajustan manualmente."
            )
            return

        try:
            pid = product.get("id")
            if not pid:
                logger.error(f"Producto sin ID vOKlido: {product}")
                QMessageBox.critical(
                    self,
                    "Error",
                    "El producto no tiene un ID vOKlido. No se puede incrementar.",
                )
                return

            base_product = self._products_by_id.get(pid) or product or {}
            dialog = StockAdjustDialog(base_product, mode="increment", parent=self)
            if dialog.exec_() != QDialog.Accepted:
                return
            qty = dialog.get_quantity()
            if not qty or qty <= 0:
                return

            # Throttling mOKnimo
            now = time.time()
            last = self._last_op_time_by_pid.get(pid, 0)
            if now - last < 0.02:
                return
            self._last_op_time_by_pid[pid] = now

            payload = {
                "producto_id": pid,
                "delta": int(qty),
                "usuario": self.username,
                "local": self.local,
                "detalle": f"Incremento manual de {qty} unidades",
                "nombre": base_product.get("nombre"),
                "categoria": base_product.get("categoria"),
                "medida": base_product.get("medida"),
                "estado": base_product.get("estado", "Nuevo"),
                "color": base_product.get("color"),
            }
            try:
                enqueued = self.operation_queue.enqueue(payload)
                if enqueued:
                    self._show_toast(f"OK +{qty} unidades")
                    self._reset_edit_timeout()
                else:
                    self._show_toast("OKOK OperaciOKn encolada para reintento")
            except Exception as e:
                logger.error(f"Error encolando incremento: {e}")
                self._show_toast(f"OK Error: {str(e)}", persistent=True)

        except Exception as e:
            logger.error(f"Error en increment_product: {e}")

    def decrement_product(self, product):
        """Decrementa cantidad de producto - solicita motivo mediante popup"""
        if not self._can_edit_stock():
            QMessageBox.warning(
                self,
                "Editar stock",
                "IngresOK la contraseOKa con el botOKn Editar para modificar stock.",
            )
            return
        # Verificar que no estemos en modo solo lectura
        if self.read_only:
            QMessageBox.warning(
                self, "Solo lectura", "No puedes modificar el stock de otro local"
            )
            return

        try:
            pid = product.get("id")
            # VALIDACIÓN CRÍTICA: verificar que el producto tenga ID
            if not pid:
                logger.error(f"Producto sin ID válido: {product}")
                QMessageBox.critical(
                    self,
                    "Error",
                    "El producto no tiene un ID válido. No se puede decrementar.",
                )
                return

            base_product = self._products_by_id.get(pid) or product or {}
            current = int(base_product.get("cantidad", 0) or 0)
            if current <= 0:
                QMessageBox.information(
                    self, "Sin stock", "No hay unidades para decrementar"
                )
                return

            dialog = StockAdjustDialog(base_product, mode="decrement", parent=self)
            if dialog.exec_() != QDialog.Accepted:
                return
            qty_to_decrement = dialog.get_quantity()
            motivo = dialog.get_motivo()
            if qty_to_decrement <= 0:
                return
            # Confirmación desactivada: permitir dejar en 0 sin mensaje
            # Throttling
            now = time.time()
            last = self._last_op_time_by_pid.get(pid, 0)
            if now - last < 0.15:
                return
            self._last_op_time_by_pid[pid] = now
            payload = {
                "producto_id": pid,
                "delta": -qty_to_decrement,
                "usuario": self.username,
                "local": self.local,
                "detalle": f"Decremento: {motivo}",
                "motivo": motivo,
                "nombre": base_product.get("nombre"),
                "categoria": base_product.get("categoria"),
                "medida": base_product.get("medida"),
                "estado": base_product.get("estado", "Nuevo"),
                "color": base_product.get("color"),
            }
            # Encolar operacion
            try:
                enqueued = self.operation_queue.enqueue(payload)
                if enqueued:
                    self._show_toast(f"OK. Decrementando {qty_to_decrement} unidades")
                    self._reset_edit_timeout()
                else:
                    self._show_toast("Error al encolar la operacion", persistent=True)
            except Exception as e:
                logger.error(f"Error enqueuing decrement payload: {e}")
                self._show_toast(f"Error: {str(e)}", persistent=True)

        except Exception as e:
            logger.error(f"Error en decrement_product: {e}")

    def _on_stock_operation_finished(self, ok, payload, response, product):
        """Callback cuando la operaciÃ³n de stock termina - EJECUTADO EN HILO PRINCIPAL"""
        try:
            pid = payload.get("producto_id")

            if ok:
                # ✅ ACTUALIZAR TABLA DIRECTAMENTE
                delta = payload.get("delta", 0)
                base_product = product or self._products_by_id.get(pid, {})
                current_qty = int(base_product.get("cantidad", 0))
                new_qty = current_qty + delta

                # Actualizar fila en O(1) usando cach� de filas
                row = self._row_by_product_id.get(pid, -1)
                if row >= 0:
                    qty_item = self.table.item(row, 7)
                    if qty_item:
                        qty_item.setText(str(new_qty))
                if pid in self._products_by_id:
                    self._products_by_id[pid]["cantidad"] = new_qty

                # Toast de éxito
                op_name = "incrementado" if delta > 0 else "decrementado"
                self._show_toast(f"✅ Stock {op_name}: {delta:+d}")
            else:
                logger.error(
                    f"Stock operation failed: {response.get('msg', 'Unknown error')}"
                )
                self._show_toast(
                    f"❌ Error: {response.get('msg', 'Error desconocido')}",
                    persistent=True,
                )

        except Exception as e:
            logger.error(f"Error en _on_stock_operation_finished: {e}")

    def _on_stock_operation_error(self, info):
        """Maneja errores en operación de stock.

        `info` puede ser un dict con keys 'error' y 'payload'.
        """
        try:
            if isinstance(info, dict):
                err = info.get("error") or info.get("msg") or str(info)
            else:
                err = str(info)
            logger.error(f"Stock operation error: {err}")
            self._show_toast(f"❌ Error: {err}", persistent=True)
        except Exception as e:
            logger.error(f"Error manejando error de operación: {e}")

    def _on_queue_finished(self, payload, response):
        """Handler ultra-ligero: solo acumula y agenda el flush."""
        try:
            self._pending_updates.append((payload, response))
            if not self._update_timer.isActive():
                self._update_timer.start(50)  # flush rápido para menor latencia
        except Exception as e:
            logger.error(f"Error en _on_queue_finished: {e}")

    def _on_queue_error(self, payload, error_msg):
        """Handler cuando `OperationQueue` reporta un error."""
        try:
            info = {"error": error_msg, "payload": payload}
            self._on_stock_operation_error(info)
        except Exception as e:
            logger.error(f"Error en _on_queue_error: {e}")

    def _on_queue_count_changed(self, count):
        """Handler cuando cambia el tamaño de la cola."""
        try:
            if count > 0:
                self.queue_banner.setText(f"⏳ Cola: {count} operaciones")
                self.queue_banner.setVisible(True)
            else:
                self.queue_banner.setVisible(False)
        except Exception as e:
            logger.error(f"Error actualizando queue_banner: {e}")

    def _cleanup_thread(self, thread):
        """Limpia referencias a threads completados"""
        try:
            if thread in self.stock_operation_threads:
                self.stock_operation_threads.remove(thread)
        except Exception as e:
            logger.error(f"Error cleaning up thread: {e}")

    def _on_field_updated_confirmed(self, product_id: int, field: str, value):
        # Maneja confirmación de actualización de campo desde la cola.
        try:
            col_map = {
                "nombre": 0,
                "categoria": 1,
                "medida": 2,
                "fabricante": 3,
                "color": 4,
                "estado": 5,
                "precio_venta": 7,
            }
            col = col_map.get(field)
            if col is not None:
                row = self._row_by_product_id.get(product_id, -1)
                if row >= 0:
                    cell_item = self.table.item(row, col)
                    if cell_item:
                        cell_item.setText(str(value or ""))
                if product_id in self._products_by_id:
                    self._products_by_id[product_id][field] = value
            # Si la ventana de historial está abierta, recargar para que muestre el nuevo movimiento
            try:
                from PyQt5.QtWidgets import QApplication

                app = QApplication.instance()
                if app:
                    try:
                        from views.history_view import HistoryWindow
                    except Exception:
                        HistoryWindow = None
                    for w in app.topLevelWidgets():
                        try:
                            if HistoryWindow is not None and isinstance(
                                w, HistoryWindow
                            ):
                                try:
                                    w.load()
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception as e:
            logger.error(
                f"Error en _on_field_updated_confirmed({product_id}, {field}, {value}): {e}"
            )

    def _on_execute_callback(self, callback, success: bool, message: str):
        """Ejecuta un callback en el hilo principal (recibido via señal del worker)."""
        if callback:
            try:
                callback(success, message)
            except Exception as e:
                logger.error(f"Error ejecutando callback en UI: {e}")

    def _get_operation_message(self, op_type: str, payload: dict, stage: str) -> str:
        # Genera mensaje para toast según operación.
        try:
            if op_type == "increment":
                product = self._products_by_id.get(payload.get("producto_id"))
                if stage == "start":
                    return f"🔄 Incrementando {payload['delta']} unidades..."
                elif stage == "success":
                    return f"✅ Stock incrementado: +{payload['delta']}"
                else:
                    return f"❌ Error al incrementar stock"

            elif op_type == "decrement":
                product = self._products_by_id.get(payload.get("producto_id"))
                if stage == "start":
                    return f"🔄 Decrementando {payload['delta']} unidades..."
                elif stage == "success":
                    return f"✅ Stock decrementado: -{payload['delta']}"
                else:
                    return f"❌ Error al decrementar stock"

            elif op_type == "update_field":
                field = payload.get("field")
                value = payload.get("value")
                if stage == "start":
                    return f"🔄 Actualizando {field}..."
                elif stage == "success":
                    return f"✅ {field.title()} actualizado"
                else:
                    return f"❌ Error al actualizar {field}"

            elif op_type == "transfer":
                qty = payload.get("cantidad", 0)
                to_local = payload.get("to_local", "")
                if stage == "start":
                    return f"🔄 Transfiriendo {qty} unidades a {to_local}..."
                elif stage == "success":
                    return f"✅ {qty} unidades transferidas a {to_local}"
                else:
                    return f"❌ Error en transferencia"

            else:
                if stage == "start":
                    return "🔄 Procesando operación..."
                elif stage == "success":
                    return "✅ Operación completada"
                else:
                    return "❌ Error en operación"

        except Exception as e:
            logger.error(f"Error generando mensaje: {e}")
            return "⚠️ Error inesperado"

    # ==================== PRODUCCIÓN: MÉTODOS CRÍTICOS ====================

    def _check_edit_permission(self, col: int, field: str) -> tuple[bool, str]:
        """Verifica permisos para editar un campo específico"""
        # Modo solo lectura
        if self.read_only and not self._is_consolidated_view():
            return False, "No puedes modificar el stock de otro local"

        # Permisos por campo según rol
        if col == 7:  # Precio
            if self.role not in ["admin", "gerente"]:
                return False, "Solo administradores y gerentes pueden cambiar precios"

        if col == 6:  # Cantidad
            if self.role == "vendedor":
                return False, "Los vendedores no pueden modificar stock directamente"

        return True, ""

    def _log_audit(self, product_id: str, field: str, old_value, new_value):
        """Registra cambio en log de auditoría"""
        try:
            import socket
            from datetime import datetime

            # Obtener IP local
            try:
                hostname = socket.gethostname()
                ip_address = socket.gethostbyname(hostname)
            except:
                ip_address = "unknown"

            audit_log = {
                "timestamp": datetime.now().isoformat(),
                "user": self.username,
                "role": self.role,
                "local": self.local,
                "product_id": product_id,
                "field": field,
                "old_value": str(old_value),
                "new_value": str(new_value),
                "ip_address": ip_address,
                "action": "stock_edit",
            }

            # Guardar en Firestore
            logger.info(
                f"Audit log (disabled) {self.username} cambio {field} de {old_value} a {new_value}"
            )

        except Exception as e:
            logger.error(f"Error logging audit: {e}")

    def _check_version_conflict(self, product_id: str) -> tuple[bool, dict]:
        """Verifica si hubo cambio concurrente (optimistic locking)"""
        # Firestore removido: asumimos sin conflicto.
        return True, {}

    def _validate_business_rules(
        self, field: str, new_value, product: dict
    ) -> tuple[bool, str]:
        """Validaciones de reglas de negocio"""
        try:
            # 1. Stock no puede ser negativo
            if field == "cantidad":
                qty = int(new_value)
                if qty < 0:
                    return False, "❌ Stock no puede ser negativo"

                # 2. Alert stock bajo
                if qty < 5 and self._should_alert_low_stock(product):
                    QMessageBox.warning(
                        self,
                        "⚠️ Stock Bajo",
                        f"Stock crítico: {qty} unidades\n"
                        f"Producto: {product.get('nombre', 'N/A')}\n\n"
                        "Considera hacer un pedido.",
                        QMessageBox.Ok,
                    )

                # 3. Límite máximo
                if qty > 9999:
                    return False, "❌ Stock máximo: 9999 unidades"

            # 4. Precio debe ser mayor a 0
            if field == "precio_venta":
                precio = int(new_value)
                if precio <= 0:
                    return False, "❌ Precio debe ser mayor a $0"

                # 5. Validar margen mínimo
                precio_costo = product.get("precio_costo", 0)
                if precio_costo > 0 and precio < precio_costo:
                    respuesta = QMessageBox.question(
                        self,
                        "⚠️ Precio Bajo Costo",
                        f"Precio de venta: ${precio:,.0f}\n"
                        f"Precio de costo: ${precio_costo:,.0f}\n\n"
                        f"¡Estás vendiendo con pérdida!\n"
                        f"¿Confirmar este precioOK",
                        QMessageBox.Yes | QMessageBox.No,
                    )

                    if respuesta == QMessageBox.No:
                        return False, "Operación cancelada por el usuario"

                # 6. Alertar cambios de precio drásticos
                old_price = product.get("precio_venta", 0)
                if old_price > 0:
                    change_pct = abs((precio - old_price) / old_price * 100)
                    if change_pct > 50:  # Cambio > 50%
                        respuesta = QMessageBox.question(
                            self,
                            "⚠️ Cambio de Precio Grande",
                            f"Cambio del {change_pct:.1f}%:\n"
                            f"Anterior: ${old_price:,.0f}\n"
                            f"Nuevo: ${precio:,.0f}\n\n"
                            f"¿ConfirmarOK",
                            QMessageBox.Yes | QMessageBox.No,
                        )

                        if respuesta == QMessageBox.No:
                            return False, "Operación cancelada"

            # 7. Nombre no puede estar vacío
            if field == "nombre":
                if len(new_value.strip()) < 2:
                    return False, "❌ Nombre debe tener al menos 2 caracteres"

            return True, ""

        except ValueError as e:
            return False, f"❌ Valor inválido: {str(e)}"
        except Exception as e:
            logger.error(f"Error en validación: {e}")
            return False, f"❌ Error de validación: {str(e)}"

    def _should_alert_low_stock(
        self, product: dict, cooldown_seconds: int = 300
    ) -> bool:
        """Evita mostrar muchas alertas de stock bajo por el mismo producto."""
        # Desactivado: no mostrar alertas de stock bajo ni para locales ni admin
        return False

    def _show_toast(self, message: str, persistent: bool = False):
        # Muestra un toast con el mensaje dado.
        try:
            from utils.toast import Toast

            if not hasattr(self, "_toast"):
                self._toast = Toast(self)
            duration = 2000
            self._toast.show_message(message, duration=duration)
        except Exception as e:
            logger.error(f"Error mostrando toast: {e}")
            sb = (
                self.statusBar()
                if hasattr(self, "statusBar") and callable(self.statusBar)
                else None
            )
            if sb:
                sb.showMessage(message, 2000)
            else:
                print(message)

    def _clear_item_highlight(self, row: int, col: int):
        try:
            # Evitar errores si la fila/col ya no existen
            if row < 0 or col < 0:
                return
            if row >= self.table.rowCount() or col >= self.table.columnCount():
                return
            it = self.table.item(row, col)
            if it:
                # Evitar que este cambio dispare señales de itemChanged
                try:
                    prev = None
                    try:
                        prev = self.table.signalsBlocked()
                    except Exception:
                        prev = False
                    try:
                        self.table.blockSignals(True)
                    except Exception:
                        pass
                    it.setBackground(QColor(0, 0, 0, 0))
                finally:
                    try:
                        if not prev:
                            self.table.blockSignals(False)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"No se pudo limpiar highlight: {e}")

    def _queue_highlight_clear(self, row: int, col: int):
        """Encola limpieza de highlight usando un único timer reutilizable."""
        try:
            self._highlight_clear_queue.append((row, col))
            if not self._highlight_timer.isActive():
                self._highlight_timer.start(1000)
        except Exception as e:
            logger.debug(f"No se pudo encolar limpieza de highlight: {e}")

    def _flush_highlight_queue(self):
        """Limpia todos los resaltados encolados."""
        try:
            # Procesar la cola en pequeños lotes para evitar bloquear el hilo UI por mucho tiempo.
            max_per_tick = int(os.environ.get("MANAREY_HIGHLIGHT_BATCH", "50"))
            processed = 0
            while self._highlight_clear_queue and processed < max_per_tick:
                r, c = self._highlight_clear_queue.pop(0)
                self._clear_item_highlight(r, c)
                processed += 1
            # Si quedan elementos, re-agendar el flush pronto
            if self._highlight_clear_queue:
                QTimer.singleShot(50, self._flush_highlight_queue)
        except Exception as e:
            logger.debug(f"No se pudo procesar cola de highlight: {e}")

    def _flush_pending_updates(self):
        """Procesa en lote las actualizaciones de la cola sin bloquear la UI."""
        try:
            if not self._pending_updates:
                return

            updates = self._pending_updates[:]
            self._pending_updates.clear()

            # Agrupar deltas por producto
            by_pid = {}
            for payload, response in updates:
                if not response.get("ok"):
                    continue
                pid = payload.get("producto_id")
                if not pid:
                    continue
                by_pid[pid] = by_pid.get(pid, 0) + payload.get("delta", 0)

            # Actualizar cache y tabla sin disparar señales
            self.table.blockSignals(True)
            for pid, delta in by_pid.items():
                if pid in self._products_by_id:
                    self._products_by_id[pid]["cantidad"] = (
                        int(self._products_by_id[pid].get("cantidad", 0)) + delta
                    )
                row = self._row_by_product_id.get(pid, -1)
                if 0 <= row < self.table.rowCount():
                    item = self.table.item(row, 7)
                    if item and pid in self._products_by_id:
                        item.setText(str(self._products_by_id[pid]["cantidad"]))
            self.table.blockSignals(False)
            if by_pid:
                # Recalcular combos (cantidad virtual) tras cambios de stock
                self._trigger_combo_sync()
                self._schedule_reload(200)
        except Exception as e:
            try:
                self.table.blockSignals(False)
            except Exception:
                pass
            logger.error(f"Error en _flush_pending_updates: {e}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._apply_table_density()
            self._fit_main_table_columns()
        except Exception:
            pass

    def closeEvent(self, event):
        # Maneja el cierre de la ventana
        self._persist_window_state()
        # Detener threads de carga
        self._stop_thread_safe(getattr(self, "loading_thread", None), 800)
        # Detener thread de filtros
        self._stop_thread_safe(getattr(self, "_filter_thread", None), 800)
        # Detener reservas interlocales/envios
        self._stop_thread_safe(getattr(self, "_reservas_thread", None), 800)
        # Detener sync de combos
        self._stop_thread_safe(getattr(self, "_combo_sync_thread", None), 800)
        # Detener carga consolidada
        self._stop_thread_safe(getattr(self, "_consolidated_thread", None), 800)

        # Detener todos los stock operation threads
        for thread in self.stock_operation_threads:
            if thread and thread.isRunning():
                try:
                    thread.requestInterruption()
                    thread.wait(800)
                except Exception:
                    pass
        self.stock_operation_threads.clear()

        # Detener workers
        if hasattr(self, "queue_worker"):
            self.queue_worker.stop()
        if hasattr(self, "stock_worker"):
            self.stock_worker.stop()
        if hasattr(self, "operation_queue") and self.operation_queue:
            try:
                self.operation_queue.stop()
                self.operation_queue.wait(1000)
            except Exception:
                pass

        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            app = QApplication.instance()
            if app and app.property("manarey_kiosk"):
                if not self.isFullScreen():
                    self.showFullScreen()
        except Exception:
            pass

    # DISABLED: showEvent - references missing fields
    # def showEvent(self, event):
    #     super().showEvent(event)
    #     if not self.categories_cache:
    #         try:
    #             self.categories_cache = sorted(set(sm.get_all_tipos()))
    #             self.setup_category_completer(self.form_fields['categoria'])
    #         except Exception as e:
    #             logger.error(f"Error inicializando cache: {e}")

    def edit_product_category(self, product_id, current_category):
        # Edita la categoría del producto de forma asíncrona.

        # Crear un diálogo personalizado no bloqueante
        from PyQt5.QtWidgets import (
            QComboBox,
            QDialog,
            QHBoxLayout,
            QPushButton,
            QVBoxLayout,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Editar Categoría")
        layout = QVBoxLayout(dialog)

        # ComboBox con autocompletado
        combo = QComboBox(dialog)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.InsertAlphabetically)

        # Cargar categorías
        categories = (
            self.categories_cache
            if self.categories_cache
            else sorted(set(sm.get_all_tipos()))
        )
        combo.addItems(categories)

        # Seleccionar categoría actual
        if current_category in categories:
            combo.setCurrentText(current_category)

        layout.addWidget(combo)

        # Botones
        buttons = QHBoxLayout()
        ok_btn = QPushButton("Aceptar")
        cancel_btn = QPushButton("Cancelar")
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        def on_accept():
            new_category = combo.currentText().strip()
            if new_category:
                old_value = self._products_by_id.get(product_id, {}).get("categoria")
                normalized = sm._norm_cat(new_category)

                # Actualización optimista UI inmediata
                self._update_product_field(product_id, "categoria", normalized)

                # Actualizar cache de categorías localmente
                if normalized not in self.categories_cache:
                    try:
                        sm.insert_tipo_si_no_existe(normalized)
                    except Exception:
                        pass
                    self.categories_cache.append(normalized)
                    self.categories_cache.sort()
                    self.update_categories(self.categories_cache)

                # Encolar operación
                payload = {
                    "producto_id": product_id,
                    "field": "categoria",
                    "value": normalized,
                    "usuario": self.username,
                    "local": self.local,
                    "motivo": "edición manual",
                }

                ok, message = qa.execute_update_field(payload)
                if ok:
                    self._show_toast("✅ Categoría actualizada exitosamente.")
                else:
                    self._update_product_field(product_id, "categoria", old_value)
                    self._show_toast(
                        f"❌ Error al actualizar categoría: {message}", persistent=True
                    )
            else:
                self._show_toast(
                    "❌ No se seleccionó ninguna categoría.", persistent=True
                )

            dialog.accept()

        def on_cancel():
            dialog.reject()

        ok_btn.clicked.connect(on_accept)
        cancel_btn.clicked.connect(on_cancel)

        # Mostrar diálogo de forma no modal
        dialog.setModal(False)
        dialog.show()
