import json
import os
import re

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QKeySequence, QPalette
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from models import stock_model as sm

try:
    from models.db import is_postgres as _is_postgres
except Exception:

    def _is_postgres():
        return False


# (Reusa tus constantes definidas arriba)
# DORADO, DARK, CARD, BORDER, TEXT, MUTED, POS_BG, NEG_BG, NEU_BG
# Paleta
DORADO = "#C9A040"
DARK = "#1f1f22"
CARD = "#232327"
BORDER = "#34343a"
TEXT = "#ECECF1"
MUTED = "#c9c9cf"
POS_BG = "#2d3e2e"
NEG_BG = "#452a2a"
NEU_BG = "#2a2a2e"
CHIP_BG = "#2a2f3a"
CHIP_TXT = "#cfd3dc"
MAX_HISTORY_ROWS = int(os.environ.get("MANAREY_HISTORY_MAX", "800"))


def _fmt_money(v):
    try:
        return "{:,}".format(int(v)).replace(",", ".")
    except Exception:
        return str(v)


def _disp(v):
    """Representación amigable: devuelve '—' si v es None o cadena vacía, sino str(v)."""
    try:
        if v is None:
            return "—"
        s = str(v).strip()
        if not s:
            return "—"
        if s.lower() in ("none", "null", "nan", "-", "—"):
            return "—"
        return s
    except Exception:
        return "—"


def _icon_and_colors(accion: str, delta: int):
    """
    Devuelve (icono, colorBadgeBG, colorBadgeFG) en función del tipo de acción.
    """
    a = (accion or "").lower()
    if a in ("ingreso",) or (a == "ajuste" and (delta or 0) > 0):
        return "➕", POS_BG, "#bde5bd"
    if a in ("baja",) or (a == "ajuste" and (delta or 0) < 0):
        return "➖", NEG_BG, "#f2c7c7"
    if a in ("transferencia", "transfer_out", "transfer_in"):
        return "📦", "#213a56", "#b9d8ff"
    if a == "cambio_estado":
        return "🔄", "#453a21", "#ffe3a1"
    # ajuste neutro / cambios de campo
    return "🛠", NEU_BG, "#d6d6db"


def _map_action(accion: str, delta):
    a = (accion or "").lower()
    if a == "ajuste":
        if delta is None or delta == 0:
            return "ajuste"
        return "suma" if delta > 0 else "resta"
    if a == "ingreso":
        return "suma"
    if a == "baja":
        return "resta"
    if a == "cambio_estado":
        return "cambio de estado"
    if a in ("transferencia", "transfer_out", "transfer_in"):
        return "transferencia"
    return accion or ""


def _nice_detail(accion, detalle, delta, meta):
    det = (detalle or "").strip()
    a = (accion or "").lower()

    if a == "ajuste":
        if delta is None:
            f = (meta or {}).get("field")
            old = (meta or {}).get("old")
            new = (meta or {}).get("new")
            if f:
                label = f.capitalize()
                extra = ""
                # Usar representación amigable para evitar mostrar 'None'
                if old is not None or new is not None:
                    extra = f" · {label}: {_disp(old)} ➜ {_disp(new)}"
                return f"Cambio de {label.lower()}{extra}"
            return "ajuste"
        if "botón" in det.lower() or "boton" in det.lower():
            return "suma por botón +" if (delta or 0) > 0 else "resta por botón −"
        return "ajuste manual"

    if a == "ingreso":
        return "alta / ingreso"

    if a == "baja":
        return det or "baja"

    if a == "cambio_estado":
        from_s = (meta or {}).get("from")
        to_s = (meta or {}).get("to")
        moved = (meta or {}).get("moved")
        msg = "cambio de estado"
        if from_s or to_s:
            msg += f" · {from_s} ➜ {to_s}"
        if moved:
            msg += f" · movidos: {moved}"
        return msg

    if a in ("transferencia", "transfer_out", "transfer_in"):
        from_l = (meta or {}).get("from_local")
        to_l = (meta or {}).get("to_local")
        moved = (meta or {}).get("moved")
        if delta is not None and delta < 0:
            return f"salida a {to_l} · movidos: {moved}"
        if delta is not None and delta > 0:
            return f"entrada desde {from_l} · movidos: {moved}"
        return det or "transferencia"

    return det or ""


class MovementCard(QWidget):
    """Tarjeta legible: frase humana, sin datos tecnicos."""

    def __init__(self, parent, row_tuple, role, username, undo_cb):
        super().__init__(parent)
        self.role = role
        self.username = username
        self.undo_cb = undo_cb

        (
            hid,
            pid,
            accion,
            detalle,
            cant,
            usuario,
            local,
            fecha,
            undone,
            undone_by,
            undone_at,
            nombre,
            categoria,
            medida,
            precio,
            motivo,
            meta_raw,
        ) = row_tuple

        try:
            meta = json.loads(meta_raw or "{}")
        except Exception:
            meta = {}

        try:
            delta = int(cant) if cant is not None else None
        except Exception:
            delta = None

        before = meta.get("old_qty")
        after = meta.get("new_qty")
        field = meta.get("field")
        old_val = meta.get("old")
        new_val = meta.get("new") if "new" in meta else meta.get("value")
        accion_norm = (
            "transferencia"
            if (accion or "").lower() in ("transfer_out", "transfer_in")
            else accion
        )
        from_local, to_local = self._infer_transfer_locals(detalle, local, delta, meta)

        self.setStyleSheet(
            f"QWidget{{background:{CARD}; border:1px solid {BORDER}; border-radius:12px;}}"
            f"QLabel{{color:{TEXT};}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)

        icon, headline = self._headline(accion_norm, delta, field, from_local, to_local)
        header = QHBoxLayout()
        title = QLabel(f"{icon} {headline}")
        title.setStyleSheet(f"color:{DORADO}; font-weight:800;")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        prod_name = QLabel(self._fmt_value(nombre, "Producto"))
        prod_name.setStyleSheet("font-weight:800; font-size:16px;")
        root.addWidget(prod_name)

        secondary = []
        if medida:
            secondary.append(self._fmt_value(medida, "-"))
        elif categoria:
            secondary.append(self._fmt_value(categoria, "-"))
        if secondary:
            sub = QLabel((" \u00b7 ").join(secondary))
            sub.setStyleSheet("color:#b9bdc7; font-size:12px;")
            root.addWidget(sub)

        details_full, details_preview = self._details_text(
            accion_norm,
            delta,
            before,
            after,
            field,
            old_val,
            new_val,
            meta,
            detalle,
            local,
        )
        if details_full:
            details_lbl = QLabel(details_preview or details_full)
            details_lbl.setWordWrap(True)
            details_lbl.setStyleSheet(f"color:{MUTED};")
            root.addWidget(details_lbl)

            if details_preview:
                btn = QPushButton("Ver cambios")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton{{background:#34343a;color:{DORADO};border:1px solid {BORDER};"
                    "border-radius:10px;padding:6px 12px;font-weight:700;}}"
                )
                btn.setFixedWidth(140)

                state = {"open": False}

                def _toggle_details():
                    state["open"] = not state["open"]
                    details_lbl.setText(
                        details_full if state["open"] else details_preview
                    )
                    btn.setText("Ocultar cambios" if state["open"] else "Ver cambios")

                btn.clicked.connect(_toggle_details)
                root.addWidget(btn, alignment=Qt.AlignLeft)

        motive_text = self._clean_reason(motivo, detalle, accion, delta, field)
        if motive_text:
            mot_lbl = QLabel(f"\U0001F4DD Motivo: {motive_text}")
            mot_lbl.setStyleSheet("color:#d6d6de;")
            mot_lbl.setWordWrap(True)
            root.addWidget(mot_lbl)

        info = QLabel(
            f"\U0001F464 {self._fmt_value(usuario, '-')}  \u00b7  \U0001F3EC {self._fmt_value(local, '-')}  \u00b7  \U0001F552 {self._fmt_date(fecha)}"
        )
        info.setStyleSheet("color:#9a9aa3; font-size:12px;")
        root.addWidget(info)

        if int(undone or 0) == 1:
            for lab in self.findChildren(QLabel):
                lab.setStyleSheet(lab.styleSheet() + " color:#8a8a93;")

    def _infer_transfer_locals(self, detalle, local, delta, meta):
        from_local = (meta or {}).get("from_local")
        to_local = (meta or {}).get("to_local")
        if from_local or to_local:
            return from_local, to_local

        det = (detalle or "").strip()
        if det:
            m = re.search(
                r"(transferencia|salida)\s+a\s+(.+)$", det, flags=re.IGNORECASE
            )
            if m:
                to_local = (m.group(2) or "").strip()
            m = re.search(
                r"(transferencia|entrada)\s+desde\s+(.+)$", det, flags=re.IGNORECASE
            )
            if m:
                from_local = (m.group(2) or "").strip()

        if delta is not None:
            if delta < 0 and not from_local:
                from_local = local
            elif delta > 0 and not to_local:
                to_local = local

        return from_local, to_local

    def _fmt_value(self, value, fallback="sin dato"):
        if value is None:
            return fallback
        text = str(value).strip()
        if not text or text.lower() in ("none", "null", "nan", "-"):
            return fallback
        return text

    def _fmt_date(self, value):
        from datetime import datetime, timezone

        if not value:
            return ""
        dt = None
        if isinstance(value, datetime):
            dt = value
        else:
            s = str(value).strip()
            if not s:
                return ""
            try:
                if s.endswith("Z"):
                    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(s.replace("Z", ""))
            except Exception:
                try:
                    dt = datetime.strptime(s.split(".")[0], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return s
        try:
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            elif _is_postgres():
                dt = dt.replace(tzinfo=timezone.utc).astimezone()
        except Exception:
            pass
        return dt.strftime("%d/%m/%Y \u00b7 %H:%M")

    def _compute_stock(self, before, after, delta):
        prev = before
        post = after
        if prev is None and post is not None and delta is not None:
            try:
                prev = int(post) - int(delta)
            except Exception:
                prev = None
        if post is None and prev is not None and delta is not None:
            try:
                post = int(prev) + int(delta)
            except Exception:
                post = None
        if prev is None and post is None and delta is not None:
            prev = 0
            post = delta
        return prev, post

    def _fmt_field_value(self, field_name, value):
        if value is None:
            return "sin dato"
        if field_name in ("precio", "precio_venta", "precio_costo"):
            try:
                return "$" + _fmt_money(value)
            except Exception:
                return str(value)
        return self._fmt_value(value)

    def _details_text(
        self,
        accion,
        delta,
        before,
        after,
        field,
        old_val,
        new_val,
        meta,
        detalle=None,
        local=None,
    ):
        lines_out = []

        if (accion or "").lower() in ("transferencia", "transfer_out", "transfer_in"):
            from_local, to_local = self._infer_transfer_locals(
                detalle, local, delta, meta
            )
            if from_local and to_local:
                lines_out.append(f"Transferencia: {from_local} -> {to_local}")
            elif from_local:
                lines_out.append(f"Transferencia desde {from_local}")
            elif to_local:
                lines_out.append(f"Transferencia hacia {to_local}")

        # Stock (si aplica)
        has_stock = (
            (before is not None)
            or (after is not None)
            or (delta is not None and int(delta) != 0)
        )
        prev, post = (
            self._compute_stock(before, after, delta) if has_stock else (None, None)
        )
        if has_stock:
            if delta:
                delta_part = f" (+{abs(delta)})" if delta > 0 else f" (-{abs(delta)})"
            else:
                delta_part = ""
            prev_disp = self._fmt_value(prev, "0") if prev is not None else "0"
            post_disp = self._fmt_value(post, "0") if post is not None else prev_disp
            lines_out.append(
                f"Stock: {prev_disp} \u2192 {post_disp} unidades{delta_part}"
            )

        # Edición de campos (si aplica)
        changes = []
        raw_changes = meta.get("changes")
        if isinstance(raw_changes, list) and raw_changes:
            for ch in raw_changes:
                if isinstance(ch, dict) and ch.get("field"):
                    changes.append(ch)
        elif field:
            changes.append({"field": field, "old": old_val, "new": new_val})

        preview_text = None

        if changes:
            bullet_lines = []
            for ch in changes:
                f = str(ch.get("field") or "").strip()
                if not f:
                    continue
                label = f.replace("_", " ").capitalize()
                oldv = self._fmt_field_value(f, ch.get("old"))
                newv = self._fmt_field_value(f, ch.get("new"))
                if oldv == "sin dato" and newv == "sin dato":
                    bullet_lines.append(f"\u2022 {label}")
                else:
                    bullet_lines.append(f"\u2022 {label}: {oldv} \u2192 {newv}")

            if bullet_lines:
                lines_out.append("Cambios realizados:")
                if len(bullet_lines) > 3:
                    preview_lines = (
                        lines_out
                        + bullet_lines[:3]
                        + [f"... (+{len(bullet_lines) - 3} mas)"]
                    )
                    preview_text = "\n".join(preview_lines).strip()
                lines_out.extend(bullet_lines)

        return "\n".join(lines_out).strip(), preview_text

    def _clean_reason(self, motivo, detalle, accion, delta, field):
        def looks_tech(val: str) -> bool:
            v = val.lower()
            return any(
                key in v
                for key in [
                    "update_field",
                    "old_qty",
                    "new_qty",
                    "meta",
                    "null",
                    "none",
                    "field",
                    "value",
                ]
            )

        mot = (motivo or "").strip()
        det = (detalle or "").strip()
        a = (accion or "").lower()

        # Detalles de edicion suelen ser internos (por ejemplo: update_field:* o colapsados)
        if a == "cambio_campo" and det:
            dl = det.lower()
            if dl.startswith(("update_field:", "cambio de", "edicion")):
                det = ""

        # Nunca mostrar JSON/objetos
        if mot.startswith("{") and mot.endswith("}"):
            mot = ""

        if mot and not looks_tech(mot):
            return self._humanize_reason(mot)

        if det and not looks_tech(det):
            return self._humanize_detail(det, accion, delta, field)

        # Fallbacks humanos
        if field or a == "cambio_campo":
            return "Corrección manual realizada por el usuario"
        if a in ("ingreso",) or (a == "ajuste" and (delta or 0) > 0):
            return "Ingreso de mercadería"
        if a in ("baja",) or (a == "ajuste" and (delta or 0) < 0):
            return "Salida de mercadería"
        if a in ("transferencia", "transfer_out", "transfer_in"):
            return "Transferencia entre locales"
        return ""

    def _humanize_reason(self, motivo: str) -> str:
        m = (motivo or "").strip()
        mp = {
            "sincronizado_automatico": "Sincronización automática entre locales",
            "edicion_masiva": "Edición masiva",
            "edicion_manual": "Corrección manual realizada por el usuario",
        }
        return mp.get(m, m)

    def _humanize_detail(self, detalle: str, accion, delta, field) -> str:
        d = (detalle or "").strip()
        dl = d.lower()
        if "alta" in dl or "ingreso" in dl or "add_product" in dl:
            return "Ingreso de mercadería"
        if "baja" in dl:
            return "Salida de mercadería"
        if "ajuste" in dl or "boton" in dl:
            a = (accion or "").lower()
            if a in ("ingreso",) or (a == "ajuste" and (delta or 0) > 0):
                return "Ingreso de mercadería"
            if a in ("baja",) or (a == "ajuste" and (delta or 0) < 0):
                return "Salida de mercadería"
            return "Corrección manual realizada por el usuario"
        if "transfer" in dl:
            return "Transferencia entre locales"
        return d

    def _headline(self, accion, delta, field, from_local, to_local):
        a = (accion or "").lower()
        if a in ("ingreso",) or (a == "ajuste" and (delta or 0) > 0):
            qty = abs(delta) if delta is not None else None
            if qty:
                return "\u2795", f"Ingreso de stock (+{qty} unidades)"
            return "\u2795", "Ingreso de stock"
        if a in ("baja",) or (a == "ajuste" and (delta or 0) < 0):
            qty = abs(delta) if delta is not None else None
            if qty:
                return "\u2796", f"Salida de stock (-{qty} unidades)"
            return "\u2796", "Salida de stock"
        if a == "transferencia":
            if from_local and to_local:
                return "\U0001F501", f"Transferencia {from_local} -> {to_local}"
            if delta is not None and delta < 0:
                destino = self._fmt_value(to_local, "otro local")
                return "\U0001F501", f"Transferencia enviada a {destino}"
            if delta is not None and delta > 0:
                origen = self._fmt_value(from_local, "otro local")
                return "\U0001F501", f"Transferencia recibida desde {origen}"
            return "\U0001F501", "Transferencia de stock"
        if field or a in ("cambio_campo", "cambio_estado"):
            return "\u270f\ufe0f", "Edición de producto"
        return "\u2139\ufe0f", "Actualización"

    def _confirm_undo(self, hid, action_label, text):
        dlg = ConfirmDialog(
            "Deshacer movimiento",
            f"Deshacer esta acción?  ({action_label})",
            "Se revertirá el cambio:\n" + text,
            self,
        )
        if dlg.exec_() == QDialog.Accepted:
            ok, msg = sm.undo_historial_entry(int(hid), self.username)
            if not ok:
                QMessageBox.warning(self, "Deshacer", msg)
            else:
                QMessageBox.information(self, "Deshacer", "Movimiento deshecho.")
                if self.undo_cb:
                    self.undo_cb()


class ConfirmDialog(QDialog):
    def __init__(self, titulo, texto, detalle=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setModal(True)
        self.setFixedWidth(520)
        self.setStyleSheet(
            f"QDialog{{background:{CARD}; color:{TEXT}; border:1px solid {BORDER}; border-radius:14px;}}"
            f"QLabel{{color:{TEXT};}}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        head = QLabel(texto)
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{TEXT}; font-weight:700;")
        lay.addWidget(head)

        if detalle:
            det = QLabel(detalle)
            det.setWordWrap(True)
            det.setStyleSheet("color:#d7d7de;")
            lay.addWidget(det)

        row = QHBoxLayout()
        row.addStretch()

        btn_ok = QPushButton("Deshacer")
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.setStyleSheet(
            f"QPushButton{{background:{DORADO}; color:#171717; border:none; border-radius:10px; "
            "padding:8px 16px; font-weight:800;}}"
        )
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_ok)

        btn_no = QPushButton("Cancelar")
        btn_no.setCursor(Qt.PointingHandCursor)
        btn_no.setStyleSheet(
            f"QPushButton{{background:#34343a; color:{DORADO}; border:1px solid {BORDER}; "
            "border-radius:10px; padding:8px 16px; font-weight:700;}}"
        )
        btn_no.clicked.connect(self.reject)
        row.addWidget(btn_no)

        lay.addLayout(row)


# ------------------------------
# Helpers de texto/acciones
# ------------------------------


def _is_sum_row(r):
    """Devuelve True si la fila representa una suma simple de stock (ingreso o ajuste positivo)."""
    accion = r[2]  # accion
    delta = r[4] or 0  # cantidad
    if accion in ("ingreso",):
        return True
    if accion == "ajuste" and (delta or 0) > 0:
        return True
    return False


def _is_breaker_row(r):
    """Acciones que cortan una racha de sumas (cambio de estado, transferencia, bajas/restas, cambios de campo)."""
    accion = r[2]
    delta = r[4] or 0
    if accion in ("cambio_estado", "transferencia", "transfer_out", "transfer_in"):
        return True
    if accion in ("baja",):
        return True
    if accion == "ajuste" and (delta or 0) < 0:
        return True
    # Ajustes de campos (ajuste con cantidad=None) también cortan
    if accion == "ajuste" and (r[4] is None):
        return True
    return False


def _collapse_continuous_sums(rows):
    """
    Colapsa sumas consecutivas del mismo producto (sin breakers intermedios)
    en una sola fila sintética que muestra Stock old_qty → new_qty acumulado.
    Mantiene el orden (más nuevo arriba).
    """
    if not rows:
        return rows

    # Trabajamos de más viejo a más nuevo para armar bien los tramos
    rows_chrono = list(reversed(rows))
    collapsed = []
    run = None  # {'pid':..., 'first': row, 'last': row, 'sum': int, 'old_qty':int, 'new_qty':int}

    META_IDX = 16  # h.meta (JSON) en el SELECT actual

    def flush_run():
        nonlocal run, collapsed
        if not run:
            return
        f = list(run["first"])
        # Tomar timestamp/id del último movimiento para reflejar el "cuando" real
        try:
            f[0] = run["last"][0]
            f[7] = run["last"][7]
            f[5] = run["last"][5]
            f[6] = run["last"][6]
        except Exception:
            pass
        # sintetizamos un movimiento "ingreso" con cantidad sumada total
        # y meta con old_qty -> new_qty del tramo
        # Campos del tuple:
        # (hid,pid,accion,detalle,cant,usuario,local,fecha,undone,undone_by,undone_at,
        #  nombre,categoria,medida, motivo, meta_raw)
        f[2] = "ingreso"
        f[3] = "alta / ingreso (colapsado)"
        f[4] = run["sum"]  # cantidad total
        # armamos meta_raw nuevo
        meta = {"old_qty": run["old_qty"], "new_qty": run["new_qty"]}
        f[META_IDX] = json.dumps(meta, ensure_ascii=False)
        collapsed.append(tuple(f))
        run = None

    for r in rows_chrono:
        pid = r[1]
        if _is_breaker_row(r):
            # corta cualquier racha abierta y agrega el breaker tal cual
            flush_run()
            collapsed.append(r)
            continue

        if _is_sum_row(r):
            # leer meta para old/new qty
            try:
                meta = json.loads(r[META_IDX] or "{}")
            except Exception:
                meta = {}
            oldq = meta.get("old_qty")
            newq = meta.get("new_qty")

            if run is None:
                run = {
                    "pid": pid,
                    "first": list(r),
                    "last": r,
                    "sum": int(r[4] or 0),
                    "old_qty": oldq if oldq is not None else None,
                    "new_qty": newq if newq is not None else None,
                }
            else:
                # misma racha si es el MISMO producto
                if pid == run["pid"]:
                    run["last"] = r
                    run["sum"] += int(r[4] or 0)
                    # old_qty del primer movimiento, new_qty del último
                    if run["old_qty"] is None and oldq is not None:
                        run["old_qty"] = oldq
                    if newq is not None:
                        run["new_qty"] = newq
                else:
                    # producto distinto => cerrar racha y empezar otra
                    flush_run()
                    run = {
                        "pid": pid,
                        "first": list(r),
                        "last": r,
                        "sum": int(r[4] or 0),
                        "old_qty": oldq if oldq is not None else None,
                        "new_qty": newq if newq is not None else None,
                    }
        else:
            # no es suma y no es breaker (poco probable), lo dejamos pasar tal cual
            flush_run()
            collapsed.append(r)

    flush_run()

    # volvemos a ordenar a "más nuevo primero"
    return list(reversed(collapsed))


def _collapse_continuous_edits(rows, window_seconds: int = 3):
    """
    Colapsa ediciones consecutivas del mismo producto (cambios de campo) en una sola fila
    para que el historial sea más legible.
    """
    if not rows:
        return rows

    from datetime import datetime, timedelta

    META_IDX = 16  # h.meta (JSON) en el SELECT actual
    MOTIVO_IDX = 15
    CREATED_AT_IDX = 7

    def _parse_dt(v):
        if isinstance(v, datetime):
            return v.replace(tzinfo=None)
        s = str(v or "").strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)
        except Exception:
            try:
                return datetime.strptime(s.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    def _get_meta(r):
        try:
            return json.loads(r[META_IDX] or "{}")
        except Exception:
            return {}

    def _is_edit_row(r):
        accion = (r[2] or "").lower()
        cant = r[4]
        meta = _get_meta(r)
        # Postgres worker: accion='cambio_campo' cantidad NULL
        if accion == "cambio_campo":
            return True
        # SQLite/local: a veces entra como 'ajuste' con cantidad 0/NULL, pero con meta.field
        if meta.get("field") and (cant is None or int(cant or 0) == 0):
            return True
        # Si meta trae 'changes' ya es una edicion compuesta
        if isinstance(meta.get("changes"), list) and meta.get("changes"):
            return True
        return False

    def _add_change(changes, field, old, new):
        field = (field or "").strip()
        if not field:
            return
        # si ya existe el campo, conservar el primer 'old' y actualizar el ultimo 'new'
        for ch in changes:
            if ch.get("field") == field:
                if ch.get("old") in (None, "", "sin dato") and old not in (None, ""):
                    ch["old"] = old
                ch["new"] = new
                return
        changes.append({"field": field, "old": old, "new": new})

    rows_chrono = list(reversed(rows))  # viejo -> nuevo
    collapsed = []
    run = None  # {key, last_dt, last_row, rows, changes, motivo}

    def flush_run():
        nonlocal run, collapsed
        if not run:
            return
        if len(run["changes"]) <= 1:
            collapsed.extend(run.get("rows") or [])
            run = None
            return
        base = list(run["last_row"])
        base[2] = "cambio_campo"
        base[3] = "edicion de producto (colapsado)"
        base[4] = None  # evita mostrar linea de stock en ediciones
        if run.get("motivo"):
            base[MOTIVO_IDX] = run["motivo"]
        meta = {"changes": run["changes"]}
        base[META_IDX] = json.dumps(meta, ensure_ascii=False)
        collapsed.append(tuple(base))
        run = None

    max_gap = timedelta(seconds=int(window_seconds or 0))

    for r in rows_chrono:
        if not _is_edit_row(r):
            flush_run()
            collapsed.append(r)
            continue

        meta = _get_meta(r)
        dt = _parse_dt(r[CREATED_AT_IDX])
        key = (r[1], r[5], r[6])  # producto_id, usuario, local

        if run is None:
            run = {
                "key": key,
                "last_dt": dt,
                "last_row": r,
                "rows": [r],
                "changes": [],
                "motivo": (r[MOTIVO_IDX] or "").strip(),
            }
        else:
            same_key = key == run["key"]
            close_enough = (
                dt is not None
                and run["last_dt"] is not None
                and (dt - run["last_dt"]) <= max_gap
            )
            if not (same_key and close_enough):
                flush_run()
                run = {
                    "key": key,
                    "last_dt": dt,
                    "last_row": r,
                    "rows": [r],
                    "changes": [],
                    "motivo": (r[MOTIVO_IDX] or "").strip(),
                }
            else:
                run["rows"].append(r)

        # Acumular cambios (si ya viene compuesto, agregarlos todos)
        if isinstance(meta.get("changes"), list) and meta.get("changes"):
            for ch in meta.get("changes"):
                if isinstance(ch, dict):
                    _add_change(
                        run["changes"], ch.get("field"), ch.get("old"), ch.get("new")
                    )
        else:
            field = meta.get("field")
            if not field:
                det = r[3] or ""
                if isinstance(det, str) and det.startswith("update_field:"):
                    field = det.split("update_field:", 1)[1].strip()
            new_val = meta.get("new") if "new" in meta else meta.get("value")
            _add_change(run["changes"], field, meta.get("old"), new_val)

        # Motivo: conservar el primero no vacio
        if not run.get("motivo") and (r[MOTIVO_IDX] or "").strip():
            run["motivo"] = (r[MOTIVO_IDX] or "").strip()

        run["last_row"] = r
        run["last_dt"] = dt

    flush_run()
    return list(reversed(collapsed))  # nuevo -> viejo


# Ventana de historial
# ------------------------------


class HistoryLoadWorker(QThread):
    data_loaded = pyqtSignal(int, list)

    def __init__(
        self,
        role: str,
        user_local: str,
        search: str,
        action_query: str,
        local_f: str,
        range_key: str,
        load_id: int,
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self.user_local = user_local
        self.search = search
        self.action_query = action_query
        self.local_f = local_f
        self.range_key = range_key
        self.load_id = load_id

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            rows = sm.get_historial(
                self.role,
                self.user_local,
                self.search,
                self.action_query,
                self.local_f,
                self.range_key,
            )
        except Exception:
            rows = []
        try:
            if self.isInterruptionRequested():
                return
            self.data_loaded.emit(self.load_id, rows)
        except Exception:
            pass


class HistoryWindow(QMainWindow):
    # Topbar
    def __init__(self, username: str, role: str, user_local: str, back_command=None):
        super().__init__()
        self.username = username
        self.role = role
        self.user_local = user_local
        self.back_command = back_command
        self.max_rows = MAX_HISTORY_ROWS
        self._load_counter = 0
        self._current_load_id = 0
        self._load_thread = None

        self.setWindowTitle("Historial de movimientos")
        self.resize(1100, 720)
        self.setStyleSheet(f"QMainWindow{{background:{DARK};}} QLabel{{color:{TEXT};}}")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Topbar
        # Topbar
        top = QHBoxLayout()
        if self.back_command:
            btn_back = QPushButton("← Volver")
            btn_back.setCursor(Qt.PointingHandCursor)
            btn_back.setStyleSheet(
                f"QPushButton{{background:#34343a;color:{DORADO};border:1px solid #3e3e44;"
                "border-radius:10px;padding:8px 14px;font-weight:700;}}"
                "QPushButton:hover{background:#3e3e44;}"
            )
            btn_back.clicked.connect(self.back_command)
            top.addWidget(btn_back)

        title = QLabel("Historial de movimientos")
        title.setFont(QFont("Segoe UI", 26, QFont.Black))
        title.setStyleSheet(f"color:{DORADO}; margin-left:8px;")
        top.addWidget(title)
        top.addStretch()
        root.addLayout(top)

        # Filtros
        filt = QHBoxLayout()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por producto o detalle…")
        self.search.setFixedWidth(280)
        self.search.setStyleSheet(
            f"QLineEdit{{background:{DORADO};color:#171717;border:none;border-radius:12px;padding:8px 12px;}}"
        )
        filt.addWidget(self.search)

        self.action_cb = QComboBox()
        self.action_cb.addItems(
            [
                "Todos",
                "suma",
                "resta",
                "ajuste",
                "ingreso",
                "baja",
                "cambio_campo",
                "cambio_estado",
                "transferencia",
            ]
        )
        self.action_cb.setStyleSheet(
            f"QComboBox{{background:{DORADO};color:#171717;border:none;border-radius:12px;padding:8px 12px;}}"
        )
        self.action_cb.setFixedWidth(200)
        filt.addWidget(self.action_cb)

        self.range_cb = QComboBox()
        self.range_cb.addItems(["30d", "7d", "hoy", "todo"])
        self.range_cb.setStyleSheet(
            f"QComboBox{{background:{DORADO};color:#171717;border:none;border-radius:12px;padding:8px 12px;}}"
        )
        self.range_cb.setFixedWidth(120)
        # Seleccionar 'hoy' por defecto al abrir la vista
        try:
            self.range_cb.setCurrentText("hoy")
        except Exception:
            try:
                self.range_cb.setCurrentIndex(2)
            except Exception:
                pass
        filt.addWidget(self.range_cb)

        if self.role == "admin":
            self.local_cb = QComboBox()
            self.local_cb.addItems(["Todos", "Cane", "Vidriera", "Longchamps", "Glew"])
            self.local_cb.setStyleSheet(
                f"QComboBox{{background:{DORADO};color:#171717;border:none;border-radius:12px;padding:8px 12px;}}"
            )
            self.local_cb.setFixedWidth(160)
            filt.addWidget(self.local_cb)
            try:
                self.local_cb.currentIndexChanged.connect(self.load)
            except Exception:
                pass
        else:
            self.local_cb = None

        btn = QPushButton("🔎 Buscar")
        btn.setStyleSheet(
            f"QPushButton{{background:{DORADO};color:#171717;border:none;border-radius:12px;padding:9px 16px;font-weight:700;}}"
        )
        btn.clicked.connect(self.load)
        filt.addWidget(btn)
        filt.addStretch()
        root.addLayout(filt)

        # Contenedor de cards
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea{border:0;} QWidget{background:transparent;}"
        )
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(2, 2, 2, 2)
        self.vbox.setSpacing(10)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll)

        self.load()
        self._refresh_timer = None  # refresco manual; evita fuga de timers

    def _fetch(self):
        search = self.search.text().strip()
        action = self.action_cb.currentText()
        action_query = action if action not in ("suma", "resta") else "Todos"
        range_key = self.range_cb.currentText()
        local_f = self.local_cb.currentText() if self.local_cb else "Todos"
        rows = sm.get_historial(
            self.role, self.user_local, search, action_query, local_f, range_key
        )
        if action == "suma":
            rows = [r for r in rows if (r[4] or 0) > 0 or r[2] in ("ingreso",)]
        elif action == "resta":
            rows = [r for r in rows if (r[4] or 0) < 0 or r[2] in ("baja",)]
        return rows

    def load(self):
        # limpiar
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

        loading = QLabel("Cargando movimientos...")
        loading.setStyleSheet("color:#9a9aa3; padding:12px;")
        self.vbox.addWidget(loading)
        self.vbox.addStretch()

        search = self.search.text().strip()
        action = self.action_cb.currentText()
        action_query = action if action not in ("suma", "resta") else "Todos"
        range_key = self.range_cb.currentText()
        local_f = self.local_cb.currentText() if self.local_cb else "Todos"

        self._load_counter += 1
        self._current_load_id = self._load_counter
        if self._load_thread and self._load_thread.isRunning():
            try:
                self._load_thread.requestInterruption()
            except Exception:
                pass

        self._load_thread = HistoryLoadWorker(
            self.role,
            self.user_local,
            search,
            action_query,
            local_f,
            range_key,
            self._current_load_id,
            self,
        )
        self._load_thread.data_loaded.connect(self._on_history_loaded)
        self._load_thread.start()

    def _on_history_loaded(self, load_id, rows):
        if load_id != self._current_load_id:
            return
        # limpiar
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

        action = self.action_cb.currentText()
        if action == "suma":
            rows = [r for r in rows if (r[4] or 0) > 0 or r[2] in ("ingreso",)]
        elif action == "resta":
            rows = [r for r in rows if (r[4] or 0) < 0 or r[2] in ("baja",)]

        rows = _collapse_continuous_sums(rows)
        rows = _collapse_continuous_edits(rows)

        total_rows = len(rows)
        truncated = None
        if self.max_rows and total_rows > self.max_rows:
            truncated = (total_rows, self.max_rows)
            rows = rows[: self.max_rows]

        if not rows:
            empty = QLabel("Sin movimientos para los filtros seleccionados.")
            empty.setStyleSheet("color:#9a9aa3; padding:12px;")
            self.vbox.addWidget(empty)
            self.vbox.addStretch()
            return

        if truncated:
            note = QLabel(
                f"Mostrando {truncated[1]} de {truncated[0]} movimientos. Ajusta filtros para ver menos."
            )
            note.setStyleSheet(
                "color:#fbbf24; padding:10px; background:#3a2e14; border:1px solid #4b3a1a; border-radius:8px;"
            )
            self.vbox.addWidget(note)

        for r in rows:
            self.vbox.addWidget(
                MovementCard(
                    self.container, r, self.role, self.username, undo_cb=self.load
                )
            )
        self.vbox.addStretch()

    def _current_hist_local(self):
        try:
            if self.role == "admin" and self.local_cb is not None:
                loc = self.local_cb.currentText()
                return None if loc == "Todos" else loc
            return self.user_local
        except Exception:
            return self.user_local

    def closeEvent(self, event):
        try:
            if self._load_thread and self._load_thread.isRunning():
                self._load_thread.requestInterruption()
                self._load_thread.wait(200)
        except Exception:
            pass
        super().closeEvent(event)
