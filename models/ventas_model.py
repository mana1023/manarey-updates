import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from models import offline_store
from models import stock_model as sm
from models import stock_queue_api as qa
from models.db import get_connection as get_conn
from models.db import is_postgres
from models.db import put_connection as put_conn

logger = logging.getLogger(__name__)

_HAS_ENVIO_PAGO_COL = None
_HAS_REMITO_IMPRESO_COL = None
_HAS_ENTREGA_ENTREGADO_COL = None
_HAS_ENTREGA_MOTIVO_COL = None
_HAS_ENTREGA_FECHA_COL = None
_HAS_ENTREGA_PROGRAMADA_COL = None
_HAS_ENTREGA_LOCAL_COL = None
_HAS_DETALLE_STOCK_LOCAL_COL = None
_HAS_DETALLE_ENTREGA_LOCAL_ENTREGADO_COL = None
_HAS_DETALLE_ENTREGA_LOCAL_FECHA_COL = None
_HAS_DETALLE_ENTREGA_LOCAL_COL = None
_ENVIO_SCHEMA_OK = None
_CASH_SCHEMA_OK = None
_DOMICILIO_SCHEMA_OK = None
_APP_SETTINGS_SCHEMA_OK = None


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_numero_venta() -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"{ts}{uuid4().hex[:6].upper()}"


def _has_envio_pago_column(cur) -> bool:
    global _HAS_ENVIO_PAGO_COL
    if _HAS_ENVIO_PAGO_COL is not None:
        return _HAS_ENVIO_PAGO_COL
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ventas'
                  AND column_name = 'forma_pago_envio'
                """
            )
            _HAS_ENVIO_PAGO_COL = cur.fetchone() is not None
        else:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_ENVIO_PAGO_COL = any(
                r[1] == "forma_pago_envio" for r in cur.fetchall()
            )
    except Exception:
        _HAS_ENVIO_PAGO_COL = False
    return _HAS_ENVIO_PAGO_COL


def _has_remito_impreso_column(cur) -> bool:
    global _HAS_REMITO_IMPRESO_COL
    if _HAS_REMITO_IMPRESO_COL is not None:
        return _HAS_REMITO_IMPRESO_COL
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ventas'
                  AND column_name = 'remito_impreso'
                """
            )
            _HAS_REMITO_IMPRESO_COL = cur.fetchone() is not None
        else:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_REMITO_IMPRESO_COL = any(
                r[1] == "remito_impreso" for r in cur.fetchall()
            )
    except Exception:
        _HAS_REMITO_IMPRESO_COL = False
    return _HAS_REMITO_IMPRESO_COL


def _has_entrega_entregado_column(cur) -> bool:
    global _HAS_ENTREGA_ENTREGADO_COL
    if _HAS_ENTREGA_ENTREGADO_COL is not None:
        return _HAS_ENTREGA_ENTREGADO_COL
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ventas'
                  AND column_name = 'entrega_entregado'
                """
            )
            _HAS_ENTREGA_ENTREGADO_COL = cur.fetchone() is not None
        else:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_ENTREGA_ENTREGADO_COL = any(
                r[1] == "entrega_entregado" for r in cur.fetchall()
            )
    except Exception:
        _HAS_ENTREGA_ENTREGADO_COL = False
    return _HAS_ENTREGA_ENTREGADO_COL


def _has_entrega_motivo_column(cur) -> bool:
    global _HAS_ENTREGA_MOTIVO_COL
    if _HAS_ENTREGA_MOTIVO_COL is not None:
        return _HAS_ENTREGA_MOTIVO_COL
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ventas'
                  AND column_name = 'entrega_motivo'
                """
            )
            _HAS_ENTREGA_MOTIVO_COL = cur.fetchone() is not None
        else:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_ENTREGA_MOTIVO_COL = any(
                r[1] == "entrega_motivo" for r in cur.fetchall()
            )
    except Exception:
        _HAS_ENTREGA_MOTIVO_COL = False
    return _HAS_ENTREGA_MOTIVO_COL


def _has_entrega_fecha_column(cur) -> bool:
    global _HAS_ENTREGA_FECHA_COL
    if _HAS_ENTREGA_FECHA_COL is not None:
        return _HAS_ENTREGA_FECHA_COL
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ventas'
                  AND column_name = 'entrega_fecha'
                """
            )
            _HAS_ENTREGA_FECHA_COL = cur.fetchone() is not None
        else:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_ENTREGA_FECHA_COL = any(
                r[1] == "entrega_fecha" for r in cur.fetchall()
            )
    except Exception:
        _HAS_ENTREGA_FECHA_COL = False
    return _HAS_ENTREGA_FECHA_COL


def _has_entrega_programada_column(cur) -> bool:
    global _HAS_ENTREGA_PROGRAMADA_COL
    if _HAS_ENTREGA_PROGRAMADA_COL is not None:
        return _HAS_ENTREGA_PROGRAMADA_COL
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ventas'
                  AND column_name = 'entrega_programada'
                """
            )
            _HAS_ENTREGA_PROGRAMADA_COL = cur.fetchone() is not None
        else:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_ENTREGA_PROGRAMADA_COL = any(
                r[1] == "entrega_programada" for r in cur.fetchall()
            )
    except Exception:
        _HAS_ENTREGA_PROGRAMADA_COL = False
    return _HAS_ENTREGA_PROGRAMADA_COL


def _has_entrega_local_column(cur) -> bool:
    global _HAS_ENTREGA_LOCAL_COL
    if _HAS_ENTREGA_LOCAL_COL is not None:
        return _HAS_ENTREGA_LOCAL_COL
    try:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ventas' AND column_name = 'entrega_local'
            """
        )
        _HAS_ENTREGA_LOCAL_COL = cur.fetchone() is not None
    except Exception:
        try:
            cur.execute("PRAGMA table_info(ventas)")
            _HAS_ENTREGA_LOCAL_COL = any(
                r[1] == "entrega_local" for r in cur.fetchall()
            )
        except Exception:
            _HAS_ENTREGA_LOCAL_COL = False
    return _HAS_ENTREGA_LOCAL_COL


def _has_detalle_col(cur, col_name: str) -> bool:
    try:
        if is_postgres():
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'detalle_ventas'
                  AND column_name = %s
                """,
                (col_name,),
            )
            return cur.fetchone() is not None
        cur.execute("PRAGMA table_info(detalle_ventas)")
        return any(r[1] == col_name for r in cur.fetchall())
    except Exception:
        return False


def ensure_envios_schema() -> Tuple[bool, str]:
    global _ENVIO_SCHEMA_OK
    global _HAS_ENVIO_PAGO_COL, _HAS_REMITO_IMPRESO_COL, _HAS_ENTREGA_ENTREGADO_COL, _HAS_ENTREGA_MOTIVO_COL
    global _HAS_ENTREGA_FECHA_COL, _HAS_ENTREGA_PROGRAMADA_COL, _HAS_ENTREGA_LOCAL_COL
    global _HAS_DETALLE_STOCK_LOCAL_COL, _HAS_DETALLE_ENTREGA_LOCAL_ENTREGADO_COL, _HAS_DETALLE_ENTREGA_LOCAL_FECHA_COL
    global _HAS_DETALLE_ENTREGA_LOCAL_COL
    if _ENVIO_SCHEMA_OK is True:
        return True, "OK"
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS forma_pago_envio TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS remito_impreso INTEGER DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS entrega_entregado INTEGER DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS entrega_motivo TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS entrega_fecha TIMESTAMP"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS entrega_programada TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS entrega_local TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS fecha_venta_original TIMESTAMP"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS tipo_pago_origen TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS forma_pago_origen TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS pago_completado_fecha TIMESTAMP"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS pago_completado_monto REAL DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS pago_completado_forma TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS pago_completado_tipo TEXT"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS tarjeta_cuotas INTEGER DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS tarjeta_interes_pct REAL DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS tarjeta_interes_monto REAL DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS producto_fabricante TEXT"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS producto_material TEXT"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS stock_local TEXT"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS entrega_local_entregado INTEGER DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS entrega_local_fecha TIMESTAMP"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS entrega_local TEXT"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS is_combo INTEGER DEFAULT 0"
            )
            cur.execute(
                "ALTER TABLE detalle_ventas ADD COLUMN IF NOT EXISTS combo_id INTEGER"
            )
            # Permitir nuevos tipos de pago (ej: credito_personal)
            try:
                cur.execute(
                    "ALTER TABLE ventas DROP CONSTRAINT IF EXISTS ventas_tipo_pago_check"
                )
            except Exception:
                pass
            cur.execute(
                "ALTER TABLE ventas ADD CONSTRAINT ventas_tipo_pago_check "
                "CHECK (tipo_pago IN ('completo','sena','domicilio','credito_personal'))"
            )
        else:
            cur.execute("PRAGMA table_info(ventas)")
            ventas_cols = {r[1] for r in cur.fetchall()}
            if "forma_pago_envio" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN forma_pago_envio TEXT")
            if "remito_impreso" not in ventas_cols:
                cur.execute(
                    "ALTER TABLE ventas ADD COLUMN remito_impreso INTEGER DEFAULT 0"
                )
            if "entrega_entregado" not in ventas_cols:
                cur.execute(
                    "ALTER TABLE ventas ADD COLUMN entrega_entregado INTEGER DEFAULT 0"
                )
            if "entrega_motivo" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN entrega_motivo TEXT")
            if "entrega_fecha" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN entrega_fecha TEXT")
            if "entrega_programada" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN entrega_programada TEXT")
            if "entrega_local" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN entrega_local TEXT")
            if "fecha_venta_original" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN fecha_venta_original TEXT")
            if "tipo_pago_origen" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN tipo_pago_origen TEXT")
            if "forma_pago_origen" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN forma_pago_origen TEXT")
            if "pago_completado_fecha" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN pago_completado_fecha TEXT")
            if "pago_completado_monto" not in ventas_cols:
                cur.execute(
                    "ALTER TABLE ventas ADD COLUMN pago_completado_monto REAL DEFAULT 0"
                )
            if "pago_completado_forma" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN pago_completado_forma TEXT")
            if "pago_completado_tipo" not in ventas_cols:
                cur.execute("ALTER TABLE ventas ADD COLUMN pago_completado_tipo TEXT")
            if "tarjeta_cuotas" not in ventas_cols:
                cur.execute(
                    "ALTER TABLE ventas ADD COLUMN tarjeta_cuotas INTEGER DEFAULT 0"
                )
            if "tarjeta_interes_pct" not in ventas_cols:
                cur.execute(
                    "ALTER TABLE ventas ADD COLUMN tarjeta_interes_pct REAL DEFAULT 0"
                )
            if "tarjeta_interes_monto" not in ventas_cols:
                cur.execute(
                    "ALTER TABLE ventas ADD COLUMN tarjeta_interes_monto REAL DEFAULT 0"
                )
            cur.execute("PRAGMA table_info(detalle_ventas)")
            detalle_cols = {r[1] for r in cur.fetchall()}
            if "producto_fabricante" not in detalle_cols:
                cur.execute(
                    "ALTER TABLE detalle_ventas ADD COLUMN producto_fabricante TEXT"
                )
            if "producto_material" not in detalle_cols:
                cur.execute(
                    "ALTER TABLE detalle_ventas ADD COLUMN producto_material TEXT"
                )
            if "stock_local" not in detalle_cols:
                cur.execute("ALTER TABLE detalle_ventas ADD COLUMN stock_local TEXT")
            if "entrega_local_entregado" not in detalle_cols:
                cur.execute(
                    "ALTER TABLE detalle_ventas ADD COLUMN entrega_local_entregado INTEGER DEFAULT 0"
                )
            if "entrega_local_fecha" not in detalle_cols:
                cur.execute(
                    "ALTER TABLE detalle_ventas ADD COLUMN entrega_local_fecha TEXT"
                )
            if "entrega_local" not in detalle_cols:
                cur.execute("ALTER TABLE detalle_ventas ADD COLUMN entrega_local TEXT")
            if "is_combo" not in detalle_cols:
                cur.execute(
                    "ALTER TABLE detalle_ventas ADD COLUMN is_combo INTEGER DEFAULT 0"
                )
            if "combo_id" not in detalle_cols:
                cur.execute("ALTER TABLE detalle_ventas ADD COLUMN combo_id INTEGER")

        try:
            cur.execute(
                """
                UPDATE ventas
                SET fecha_venta_original = COALESCE(fecha_venta_original, fecha),
                    tipo_pago_origen = COALESCE(tipo_pago_origen, tipo_pago),
                    forma_pago_origen = COALESCE(forma_pago_origen, forma_pago)
                """
            )
        except Exception:
            pass

        conn.commit()
        _ENVIO_SCHEMA_OK = True
        _HAS_ENVIO_PAGO_COL = True
        _HAS_REMITO_IMPRESO_COL = True
        _HAS_ENTREGA_ENTREGADO_COL = True
        _HAS_ENTREGA_MOTIVO_COL = True
        _HAS_ENTREGA_FECHA_COL = True
        _HAS_ENTREGA_LOCAL_COL = True
        _HAS_DETALLE_STOCK_LOCAL_COL = True
        _HAS_DETALLE_ENTREGA_LOCAL_ENTREGADO_COL = True
        _HAS_DETALLE_ENTREGA_LOCAL_FECHA_COL = True
        _HAS_DETALLE_ENTREGA_LOCAL_COL = True
        return True, "OK"
    except Exception as e:
        logger.exception("Error asegurando schema de envios")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        _ENVIO_SCHEMA_OK = False
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def _is_combo_item(item: Dict) -> bool:
    try:
        return int(item.get("is_combo") or 0) == 1
    except Exception:
        return False


def _combo_components_for_item(item: Dict) -> List[Dict[str, int]]:
    try:
        combo_id = int(item.get("combo_id") or item.get("producto_id") or 0)
    except Exception:
        combo_id = 0
    if combo_id <= 0:
        return []
    combo_items = item.get("combo_items")
    if not isinstance(combo_items, list) or not combo_items:
        try:
            combo_items = sm.get_combo_items(combo_id)
        except Exception:
            combo_items = []
    out: List[Dict[str, int]] = []
    for it in combo_items:
        try:
            pid = int(it.get("producto_id") or 0)
            qty = int(it.get("cantidad") or 0)
        except Exception:
            pid = 0
            qty = 0
        if pid > 0 and qty > 0:
            out.append({"producto_id": pid, "cantidad": qty})
    return out


def _combo_signature(items: List[Dict]) -> Tuple[Tuple]:
    try:
        return sm._combo_signature(items)  # type: ignore[attr-defined]
    except Exception:
        # Fallback simple si no existe helper
        sig = []
        for it in items or []:
            sig.append(
                (
                    str(it.get("producto_nombre") or "").strip().lower(),
                    str(it.get("producto_categoria") or "").strip().lower(),
                    str(it.get("producto_medida") or "").strip().lower(),
                    str(it.get("producto_estado") or "").strip().lower(),
                    str(it.get("producto_color") or "").strip().lower(),
                    str(it.get("producto_fabricante") or "").strip().lower(),
                    int(it.get("cantidad") or 0),
                )
            )
        return tuple(sorted(sig))


def _map_combo_components_to_local(
    components: List[Dict], local: str
) -> List[Dict[str, int]]:
    conn = None
    out: List[Dict[str, int]] = []
    try:
        local = (local or "").strip()
        if not local:
            return []
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        for comp in components or []:
            try:
                qty = int(comp.get("cantidad") or 0)
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            nombre = str(comp.get("producto_nombre") or "").strip()
            categoria = str(comp.get("producto_categoria") or "").strip()
            medida = str(comp.get("producto_medida") or "").strip()
            estado = str(comp.get("producto_estado") or "").strip()
            color = str(comp.get("producto_color") or "").strip()
            fabricante = str(comp.get("producto_fabricante") or "").strip()
            # Si faltan atributos, completarlos desde el producto origen por ID
            if not nombre or not categoria or not medida:
                try:
                    pid_src = int(comp.get("producto_id") or 0)
                except Exception:
                    pid_src = 0
                if pid_src > 0:
                    try:
                        cur.execute(
                            f"""
                            SELECT nombre, categoria, medida, estado, color, fabricante
                            FROM productos
                            WHERE id={ph}
                            """,
                            (pid_src,),
                        )
                        row = cur.fetchone()
                        if row:
                            nombre = nombre or (row[0] or "")
                            categoria = categoria or (row[1] or "")
                            medida = medida or (row[2] or "")
                            estado = estado or (row[3] or "")
                            color = color or (row[4] or "")
                            fabricante = fabricante or (row[5] or "")
                    except Exception:
                        pass
            if not nombre or not categoria:
                return []
            cur.execute(
                f"""
                SELECT id FROM productos
                WHERE local={ph} AND COALESCE(is_combo,0)=0
                  AND LOWER(nombre)=LOWER({ph}) AND LOWER(categoria)=LOWER({ph})
                  AND COALESCE(medida,'')=COALESCE({ph},'')
                  AND COALESCE(estado,'')=COALESCE({ph},'')
                  AND COALESCE(color,'')=COALESCE({ph},'')
                  AND COALESCE(fabricante,'')=COALESCE({ph},'')
                LIMIT 1
                """,
                (local, nombre, categoria, medida, estado, color, fabricante),
            )
            row = cur.fetchone()
            if not row:
                return []
            out.append({"producto_id": int(row[0]), "cantidad": qty})
        return out
    except Exception:
        return []
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def _get_combo_row(combo_id: int) -> Optional[Dict[str, Any]]:
    conn = None
    try:
        if combo_id <= 0:
            return None
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        cur.execute(
            f"""
            SELECT id, nombre, categoria, medida, estado, color, precio_costo, precio_venta,
                   local, codigo, descripcion, fabricante, material, is_combo
            FROM productos
            WHERE id={ph} AND COALESCE(is_combo,0)=1
            """,
            (int(combo_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    except Exception:
        return None
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def _find_combo_id_by_full_match(
    local: str, combo_row: Dict[str, Any], signature: Tuple[Tuple]
) -> int:
    conn = None
    try:
        local = (local or "").strip()
        if not local or not combo_row or not signature:
            return 0
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        where = [
            f"local={ph}",
            "COALESCE(is_combo,0)=1",
            f"LOWER(nombre)=LOWER({ph})",
            f"COALESCE(categoria,'')=COALESCE({ph},'')",
            f"COALESCE(medida,'')=COALESCE({ph},'')",
            f"COALESCE(estado,'')=COALESCE({ph},'')",
            f"COALESCE(color,'')=COALESCE({ph},'')",
            f"COALESCE(fabricante,'')=COALESCE({ph},'')",
            f"COALESCE(material,'')=COALESCE({ph},'')",
            f"COALESCE(codigo,'')=COALESCE({ph},'')",
            f"COALESCE(descripcion,'')=COALESCE({ph},'')",
            f"COALESCE(precio_venta,0)=COALESCE({ph},0)",
            f"COALESCE(precio_costo,0)=COALESCE({ph},0)",
        ]
        params = [
            local,
            combo_row.get("nombre") or "",
            combo_row.get("categoria") or "",
            combo_row.get("medida") or "",
            combo_row.get("estado") or "",
            combo_row.get("color") or "",
            combo_row.get("fabricante") or "",
            combo_row.get("material") or "",
            combo_row.get("codigo") or "",
            combo_row.get("descripcion") or "",
            combo_row.get("precio_venta") or 0,
            combo_row.get("precio_costo") or 0,
        ]
        cur.execute(
            f"""
            SELECT id FROM productos
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        )
        candidates = [int(r[0]) for r in cur.fetchall() or []]
        for cid in candidates:
            try:
                items = sm.get_combo_items(int(cid))
            except Exception:
                items = []
            if _combo_signature(items) == signature:
                return int(cid)
        return 0
    except Exception:
        return 0
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def _find_combo_id_by_attrs(
    local: str, nombre: str, medida: str = "", fabricante: str = ""
) -> int:
    conn = None
    try:
        local = (local or "").strip()
        nombre = (nombre or "").strip()
        if not local or not nombre:
            return 0
        medida = (medida or "").strip()
        fabricante = (fabricante or "").strip()
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        where = [
            f"local={ph}",
            "COALESCE(is_combo,0)=1",
            f"LOWER(nombre)=LOWER({ph})",
        ]
        params = [local, nombre]
        if medida:
            where.append(f"COALESCE(medida,'')={ph}")
            params.append(medida)
        if fabricante:
            where.append(f"COALESCE(fabricante,'')={ph}")
            params.append(fabricante)
        cur.execute(
            f"""
            SELECT id FROM productos
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(params),
        )
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def _combo_components_for_item_local(item: Dict, local: str) -> List[Dict[str, int]]:
    # Prioridad: combo_items explícitos -> combo_id -> combo por nombre en local
    combo_items = item.get("combo_items")
    if isinstance(combo_items, list) and combo_items:
        mapped = _map_combo_components_to_local(combo_items, local)
        if mapped:
            return mapped
        return _combo_components_for_item(
            {
                "combo_id": item.get("combo_id"),
                "producto_id": item.get("producto_id"),
                "combo_items": combo_items,
            }
        )
    comps = _combo_components_for_item(item)
    if comps:
        # Intentar mapear usando atributos del combo en el local objetivo
        try:
            src_combo_id = int(item.get("combo_id") or item.get("producto_id") or 0)
        except Exception:
            src_combo_id = 0
        if src_combo_id > 0:
            try:
                src_items = sm.get_combo_items(int(src_combo_id))
            except Exception:
                src_items = []
            mapped = _map_combo_components_to_local(src_items, local)
            if mapped:
                return mapped
        return comps
    try:
        src_combo_id = int(item.get("combo_id") or item.get("producto_id") or 0)
    except Exception:
        src_combo_id = 0
    if src_combo_id > 0:
        src_row = _get_combo_row(src_combo_id)
        if src_row:
            try:
                src_items = sm.get_combo_items(int(src_combo_id))
            except Exception:
                src_items = []
            sig = _combo_signature(src_items)
            target_combo_id = _find_combo_id_by_full_match(local, src_row, sig)
            if target_combo_id > 0:
                try:
                    return _combo_components_for_item(
                        {
                            "combo_id": target_combo_id,
                            "combo_items": sm.get_combo_items(int(target_combo_id)),
                        }
                    )
                except Exception:
                    pass
    nombre = item.get("producto_nombre") or item.get("nombre") or ""
    medida = item.get("producto_medida") or item.get("medida") or ""
    fabricante = item.get("producto_fabricante") or item.get("fabricante") or ""
    combo_id = _find_combo_id_by_attrs(local, nombre, medida, fabricante)
    if combo_id <= 0:
        return []
    try:
        return _combo_components_for_item(
            {"combo_id": combo_id, "combo_items": sm.get_combo_items(combo_id)}
        )
    except Exception:
        return []


def _expand_items_for_stock(
    items: List[Dict],
    default_local: str,
    *,
    skip_delivered: bool = False,
) -> List[Dict[str, Any]]:
    """Convierte items (incluyendo combos) a productos reales para operar stock."""
    expanded: List[Dict[str, Any]] = []
    for it in items or []:
        if skip_delivered:
            try:
                if int(it.get("entrega_local_entregado") or 0) == 1:
                    continue
            except Exception:
                pass
        try:
            qty = int(it.get("cantidad") or 0)
        except Exception:
            qty = 0
        if qty <= 0:
            continue
        local_det = (
            it.get("stock_local") or it.get("entrega_local") or ""
        ).strip() or default_local
        if _is_combo_item(it):
            components = _combo_components_for_item_local(it, local_det)
            for comp in components:
                comp_pid = int(comp.get("producto_id") or 0)
                comp_qty = int(comp.get("cantidad") or 0) * qty
                if comp_pid > 0 and comp_qty > 0:
                    expanded.append(
                        {
                            "producto_id": comp_pid,
                            "cantidad": comp_qty,
                            "nombre": it.get("producto_nombre")
                            or it.get("nombre")
                            or "",
                            "local": local_det,
                            "detalle_origen": f"Combo {it.get('producto_nombre') or it.get('nombre') or ''}".strip(),
                        }
                    )
        else:
            try:
                pid = int(it.get("producto_id") or 0)
            except Exception:
                pid = 0
            try:
                entrega_pid = int(it.get("entrega_producto_id") or 0)
            except Exception:
                entrega_pid = 0
            if entrega_pid > 0:
                pid = entrega_pid
            if pid <= 0:
                continue
            expanded.append(
                {
                    "producto_id": pid,
                    "cantidad": qty,
                    "nombre": it.get("producto_nombre") or it.get("nombre") or "",
                    "local": local_det,
                    "detalle_origen": "",
                }
            )
    return expanded


def _get_stock_qty(cur, producto_id: int, local: str) -> int:
    try:
        ph = "%s" if is_postgres() else "?"
        cur.execute(
            f"SELECT COALESCE(cantidad,0) FROM productos "
            f"WHERE id={ph} AND LOWER(TRIM(local))=LOWER(TRIM({ph}))",
            (int(producto_id), (local or "").strip()),
        )
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _find_product_id_by_codigo(cur, local: str, codigo: str) -> int:
    try:
        if not codigo:
            return 0
        ph = "%s" if is_postgres() else "?"
        cur.execute(
            f"""
            SELECT id FROM productos
            WHERE LOWER(TRIM(local))=LOWER(TRIM({ph}))
              AND LOWER(TRIM(COALESCE(codigo,'')))=LOWER(TRIM({ph}))
            ORDER BY id LIMIT 1
            """,
            ((local or "").strip(), (codigo or "").strip()),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _find_product_id_by_attrs(cur, local: str, item: Dict) -> int:
    try:

        def _norm_txt(value: str) -> str:
            return (value or "").strip().lower()

        ph = "%s" if is_postgres() else "?"
        nombre = (item.get("producto_nombre") or item.get("nombre") or "").strip()
        categoria = (
            item.get("producto_categoria") or item.get("categoria") or ""
        ).strip()
        medida = (item.get("producto_medida") or item.get("medida") or "").strip()
        estado = (item.get("producto_estado") or item.get("estado") or "").strip()
        color = (item.get("producto_color") or item.get("color") or "").strip()
        fabricante = (
            item.get("producto_fabricante") or item.get("fabricante") or ""
        ).strip()
        material = (item.get("producto_material") or item.get("material") or "").strip()
        material_norm = _norm_txt(material)
        base_where = f"""
            LOWER(TRIM(local))=LOWER(TRIM({ph}))
            AND LOWER(TRIM(COALESCE(nombre,'')))=LOWER(TRIM({ph}))
            AND LOWER(TRIM(COALESCE(categoria,'')))=LOWER(TRIM({ph}))
            AND LOWER(TRIM(COALESCE(medida,'')))=LOWER(TRIM({ph}))
            AND LOWER(TRIM(COALESCE(estado,'')))=LOWER(TRIM({ph}))
            AND LOWER(TRIM(COALESCE(color,'')))=LOWER(TRIM({ph}))
            AND LOWER(TRIM(COALESCE(fabricante,'')))=LOWER(TRIM({ph}))
        """
        params = [
            (local or "").strip(),
            nombre,
            categoria,
            medida,
            estado,
            color,
            fabricante,
        ]

        if material:
            cur.execute(
                f"""
                SELECT id FROM productos
                WHERE {base_where}
                  AND LOWER(TRIM(COALESCE(material,'')))=LOWER(TRIM({ph}))
                ORDER BY cantidad DESC, id
                LIMIT 1
                """,
                tuple(params + [material]),
            )
            row = cur.fetchone()
            if row:
                return int(row[0])

        # Fallback: buscar por resto de atributos y resolver por unicidad o similitud de material
        cur.execute(
            f"""
            SELECT id, COALESCE(material,''), COALESCE(cantidad,0) FROM productos
            WHERE {base_where}
            ORDER BY cantidad DESC, id
            LIMIT 20
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
        if not rows:
            return 0
        if len(rows) == 1:
            return int(rows[0][0])

        # Preferir candidato con material similar (pino vs pino macizo)
        if material_norm:
            similar = []
            for rid, rmat, rqty in rows:
                rmat_norm = _norm_txt(rmat)
                if not rmat_norm:
                    continue
                if material_norm in rmat_norm or rmat_norm in material_norm:
                    similar.append((rid, rmat_norm, rqty))
            if len(similar) == 1:
                return int(similar[0][0])

        # Si hay varios, elegir solo si hay un único candidato con stock suficiente
        try:
            need_qty = int(item.get("cantidad") or 0)
        except Exception:
            need_qty = 0
        if need_qty > 0:
            with_stock = [r for r in rows if int(r[2] or 0) >= need_qty]
            if len(with_stock) == 1:
                return int(with_stock[0][0])
        return 0
    except Exception:
        return 0


def get_delivery_pid_for_local(item: Dict, local: str) -> int:
    """Devuelve el producto_id correcto en el local para entregar este item."""
    try:
        local = (local or "").strip()
        if not local:
            return 0
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        try:
            pid = int(item.get("producto_id") or 0)
        except Exception:
            pid = 0
        if pid > 0:
            try:
                cur.execute(
                    f"SELECT id FROM productos WHERE id={ph} AND LOWER(TRIM(local))=LOWER(TRIM({ph}))",
                    (pid, local),
                )
                row = cur.fetchone()
                if row:
                    return pid
            except Exception:
                pass
        codigo = (item.get("producto_codigo") or item.get("codigo") or "").strip()
        if codigo:
            pid_code = _find_product_id_by_codigo(cur, local, codigo)
            if pid_code > 0:
                return pid_code
        return _find_product_id_by_attrs(cur, local, item)
    except Exception:
        return 0
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def _validate_stock_for_entrega(expanded: List[Dict[str, Any]]) -> Tuple[bool, str]:
    if not expanded:
        return True, "OK"
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        insufficient = []
        for it in expanded:
            try:
                pid = int(it.get("producto_id") or 0)
                qty = int(it.get("cantidad") or 0)
            except Exception:
                pid = 0
                qty = 0
            local = (it.get("local") or "").strip()
            if pid <= 0 or qty <= 0:
                continue
            if not local:
                insufficient.append(f"ID {pid}: local vacio")
                continue
            available = _get_stock_qty(cur, pid, local)
            if available < qty:
                insufficient.append(
                    f"ID {pid} en {local}: disponible {available}, requerido {qty}"
                )
        if insufficient:
            msg = "Stock insuficiente para entregar:\n" + "\n".join(insufficient[:8])
            if len(insufficient) > 8:
                msg += f"\n... y {len(insufficient) - 8} mas"
            return False, msg
        return True, "OK"
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def get_stock_qty(producto_id: int, local: str) -> int:
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        return _get_stock_qty(cur, int(producto_id or 0), (local or "").strip())
    except Exception:
        return 0
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def get_available_qty_for_item_local(item: Dict, local: str) -> int:
    """Cantidad máxima del item que puede salir desde un local."""
    try:
        local = (local or "").strip()
        if not local:
            return 0
        if _is_combo_item(item):
            components = _combo_components_for_item_local(item, local)
            if not components:
                return 0
            available = None
            for comp in components:
                try:
                    pid = int(comp.get("producto_id") or 0)
                    comp_qty = int(comp.get("cantidad") or 0)
                except Exception:
                    pid = 0
                    comp_qty = 0
                if pid <= 0 or comp_qty <= 0:
                    return 0
                stock = get_stock_qty(pid, local)
                can_make = int(stock // comp_qty)
                available = can_make if available is None else min(available, can_make)
            return int(available or 0)
        pid = get_delivery_pid_for_local(item, local)
        if pid <= 0:
            return 0
        return get_stock_qty(pid, local)
    except Exception:
        return 0


def can_deliver_item_from_local(item: Dict, local: str) -> bool:
    try:
        local = (local or "").strip()
        if not local:
            return False
        try:
            item_qty = int(item.get("cantidad") or 0)
        except Exception:
            item_qty = 0
        if item_qty <= 0:
            return False
        return get_available_qty_for_item_local(item, local) >= item_qty
    except Exception:
        return False


def ensure_cash_withdrawals_schema() -> Tuple[bool, str]:
    global _CASH_SCHEMA_OK
    try:
        if _CASH_SCHEMA_OK is True:
            return True, "OK"
        conn = get_conn()
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cash_withdrawals (
                    id SERIAL PRIMARY KEY,
                    local TEXT,
                    amount NUMERIC NOT NULL,
                    usuario TEXT,
                    created_at TIMESTAMP
                )
            """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cash_withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local TEXT,
                    amount REAL NOT NULL,
                    usuario TEXT,
                    created_at TEXT
                )
            """
            )
        conn.commit()
        _CASH_SCHEMA_OK = True
        return True, "OK"
    except Exception as e:
        logger.exception("Error asegurando schema cash_withdrawals")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        _CASH_SCHEMA_OK = False
        return False, str(e)
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def ensure_app_settings_schema() -> Tuple[bool, str]:
    global _APP_SETTINGS_SCHEMA_OK
    try:
        if _APP_SETTINGS_SCHEMA_OK is True:
            return True, "OK"
        conn = get_conn()
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT,
                    updated_at TEXT
                )
                """
            )
        conn.commit()
        _APP_SETTINGS_SCHEMA_OK = True
        return True, "OK"
    except Exception as e:
        logger.exception("Error asegurando schema app_settings")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        _APP_SETTINGS_SCHEMA_OK = False
        return False, str(e)
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def set_cash_withdraw_password(password: str) -> Tuple[bool, str]:
    try:
        pwd = (password or "").strip()
        if not pwd:
            return False, "La contraseña no puede estar vacia"
        ok, msg = ensure_app_settings_schema()
        if not ok:
            return False, msg
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        now = _now_local()
        if is_postgres():
            cur.execute(
                f"""
                INSERT INTO app_settings (setting_key, setting_value, updated_at)
                VALUES ({ph}, {ph}, {ph})
                ON CONFLICT (setting_key)
                DO UPDATE SET setting_value=EXCLUDED.setting_value, updated_at=EXCLUDED.updated_at
                """,
                ("cash_withdraw_password", pwd, now),
            )
        else:
            cur.execute(
                f"""
                INSERT OR REPLACE INTO app_settings (setting_key, setting_value, updated_at)
                VALUES ({ph}, {ph}, {ph})
                """,
                ("cash_withdraw_password", pwd, now),
            )
        conn.commit()
        return True, "OK"
    except Exception as e:
        logger.exception("Error guardando password de retiros")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_cash_withdraw_password(default: str = "Manavella10") -> str:
    try:
        ok, _ = ensure_app_settings_schema()
        if ok:
            conn = get_conn()
            cur = conn.cursor()
            ph = "%s" if is_postgres() else "?"
            cur.execute(
                f"SELECT setting_value FROM app_settings WHERE setting_key={ph} LIMIT 1",
                ("cash_withdraw_password",),
            )
            row = cur.fetchone()
            if row:
                pwd = (row[0] or "").strip()
                if pwd:
                    return pwd
    except Exception:
        logger.exception("Error obteniendo password de retiros desde BD")
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass
    return default


def ensure_domicilio_pagos_schema() -> Tuple[bool, str]:
    global _DOMICILIO_SCHEMA_OK
    try:
        if _DOMICILIO_SCHEMA_OK is True:
            return True, "OK"
        conn = get_conn()
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS domicilio_pagos (
                    id SERIAL PRIMARY KEY,
                    venta_id INTEGER,
                    local TEXT,
                    usuario TEXT,
                    monto NUMERIC NOT NULL,
                    monto_productos NUMERIC,
                    created_at TIMESTAMP
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS domicilio_pagos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venta_id INTEGER,
                    local TEXT,
                    usuario TEXT,
                    monto REAL NOT NULL,
                    monto_productos REAL,
                    created_at TEXT
                )
                """
            )
        if is_postgres():
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'domicilio_pagos'
                """
            )
            cols = {str(r[0]) for r in (cur.fetchall() or [])}
            if "retirado" not in cols:
                cur.execute(
                    "ALTER TABLE domicilio_pagos ADD COLUMN retirado INTEGER DEFAULT 0"
                )
            if "retirado_at" not in cols:
                cur.execute(
                    "ALTER TABLE domicilio_pagos ADD COLUMN retirado_at TIMESTAMP"
                )
            if "retirado_por" not in cols:
                cur.execute("ALTER TABLE domicilio_pagos ADD COLUMN retirado_por TEXT")
            if "monto_productos" not in cols:
                cur.execute(
                    "ALTER TABLE domicilio_pagos ADD COLUMN monto_productos NUMERIC"
                )
        else:
            cur.execute("PRAGMA table_info(domicilio_pagos)")
            cols = {str(r[1]) for r in (cur.fetchall() or [])}
            if "retirado" not in cols:
                cur.execute(
                    "ALTER TABLE domicilio_pagos ADD COLUMN retirado INTEGER DEFAULT 0"
                )
            if "retirado_at" not in cols:
                cur.execute("ALTER TABLE domicilio_pagos ADD COLUMN retirado_at TEXT")
            if "retirado_por" not in cols:
                cur.execute("ALTER TABLE domicilio_pagos ADD COLUMN retirado_por TEXT")
            if "monto_productos" not in cols:
                cur.execute(
                    "ALTER TABLE domicilio_pagos ADD COLUMN monto_productos REAL"
                )
        conn.commit()
        _DOMICILIO_SCHEMA_OK = True
        return True, "OK"
    except Exception as e:
        logger.exception("Error asegurando schema domicilio_pagos")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        _DOMICILIO_SCHEMA_OK = False
        return False, str(e)
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def _get_envios_box_local() -> str:
    return "Longchamps"


def _get_domicilio_payment_net_amount(
    venta: Dict[str, Any], gross_amount: float
) -> float:
    try:
        net_amount = float(gross_amount or 0)
    except Exception:
        net_amount = 0.0
    try:
        incluye_envio = int(venta.get("incluye_envio") or 0)
    except Exception:
        incluye_envio = 0
    forma_envio = str(venta.get("forma_pago_envio") or "").strip().lower()
    if incluye_envio == 1 and forma_envio in ("domicilio", "en domicilio"):
        try:
            precio_envio = float(venta.get("precio_envio") or 0)
        except Exception:
            precio_envio = 0.0
        net_amount = max(0.0, net_amount - precio_envio)
    return float(net_amount)


def add_domicilio_pago(
    venta_id: int, local: str, usuario: str, monto: float
) -> Tuple[bool, str]:
    try:
        ok, msg = ensure_domicilio_pagos_schema()
        if not ok:
            return False, msg
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        cur.execute(
            f"""
            INSERT INTO domicilio_pagos (venta_id, local, usuario, monto, monto_productos, created_at)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph})
            """,
            (
                int(venta_id),
                (local or "").strip(),
                (usuario or "").strip(),
                float(monto or 0),
                float(monto or 0),
                _now_local(),
            ),
        )
        conn.commit()
        return True, "OK"
    except Exception as e:
        logger.exception("Error registrando pago en domicilio")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_domicilio_pagos(venta_id: int) -> List[Dict[str, Any]]:
    try:
        ok, _ = ensure_domicilio_pagos_schema()
        if not ok:
            return []
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        cur.execute(
            f"""
            SELECT created_at, monto, local, usuario
            FROM domicilio_pagos
            WHERE venta_id={ph}
            ORDER BY id
            """,
            (int(venta_id),),
        )
        rows = cur.fetchall() or []
        return [
            {"fecha": r[0], "monto": float(r[1] or 0), "local": r[2], "usuario": r[3]}
            for r in rows
        ]
    except Exception:
        logger.exception("Error obteniendo pagos en domicilio")
        return []
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_domicilio_pagos_local_total(
    local: str,
    filtro_fecha: str = "todo",
    fecha_dia: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
) -> float:
    try:
        ok, _ = ensure_domicilio_pagos_schema()
        if not ok:
            return 0.0
        local = (local or "").strip()
        if not local or local in ("Todos", "Todos los locales"):
            return 0.0

        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        where_parts = [f"LOWER(TRIM(local)) = LOWER(TRIM({ph}))"]
        params: List[Any] = [local]

        start_ts = end_ts = None
        now = datetime.now()
        if fecha_inicio and fecha_fin:
            try:
                si = datetime.strptime(fecha_inicio, "%Y-%m-%d")
                sf = datetime.strptime(fecha_fin, "%Y-%m-%d")
                start = si.replace(hour=0, minute=0, second=0, microsecond=0)
                end = sf.replace(hour=23, minute=59, second=59, microsecond=0)
                start_ts = start.strftime("%Y-%m-%d %H:%M:%S")
                end_ts = end.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                start_ts = end_ts = None
        if fecha_dia and not start_ts:
            d = None
            try:
                d = datetime.strptime(fecha_dia, "%Y-%m-%d")
            except Exception:
                try:
                    d = datetime.strptime(fecha_dia, "%d-%m-%Y")
                except Exception:
                    d = None
            if d:
                start = d.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                start_ts = start.strftime("%Y-%m-%d %H:%M:%S")
                end_ts = end.strftime("%Y-%m-%d %H:%M:%S")
        if not start_ts:
            if filtro_fecha in ("hoy", "dia", ""):
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                start_ts = start.strftime("%Y-%m-%d %H:%M:%S")
                end_ts = end.strftime("%Y-%m-%d %H:%M:%S")
            elif filtro_fecha == "semana":
                start = now.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) - timedelta(days=7)
                end = now.replace(hour=23, minute=59, second=59, microsecond=0)
                start_ts = start.strftime("%Y-%m-%d %H:%M:%S")
                end_ts = end.strftime("%Y-%m-%d %H:%M:%S")
            elif filtro_fecha == "mes":
                start = now.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) - timedelta(days=30)
                end = now.replace(hour=23, minute=59, second=59, microsecond=0)
                start_ts = start.strftime("%Y-%m-%d %H:%M:%S")
                end_ts = end.strftime("%Y-%m-%d %H:%M:%S")

        if start_ts and end_ts:
            where_parts.append(f"created_at >= {ph}")
            where_parts.append(f"created_at <= {ph}")
            params.extend([start_ts, end_ts])

        where_sql = " AND ".join(where_parts)
        cur.execute(
            f"SELECT COALESCE(SUM(monto), 0) FROM domicilio_pagos WHERE {where_sql}",
            tuple(params),
        )
        row = cur.fetchone()
        return float((row[0] if row else 0) or 0)
    except Exception:
        logger.exception("Error obteniendo total de envios por local")
        return 0.0
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_domicilio_pagos_pending(search: str = "") -> List[Dict[str, Any]]:
    try:
        ok, _ = ensure_domicilio_pagos_schema()
        if not ok:
            return []
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        query = f"""
            SELECT
                dp.id,
                dp.venta_id,
                dp.local,
                dp.usuario,
                dp.monto,
                dp.monto_productos,
                dp.created_at,
                COALESCE(dp.retirado, 0) AS retirado,
                v.numero_venta,
                v.cliente_nombre,
                v.cliente_calle,
                v.cliente_numero,
                v.cliente_localidad,
                v.entre_calles,
                v.local AS venta_local,
                v.fecha,
                COALESCE(v.remito_impreso, 0) AS remito_impreso,
                COALESCE(v.precio_envio, 0) AS precio_envio,
                COALESCE(v.forma_pago_envio, '') AS forma_pago_envio
            FROM domicilio_pagos dp
            LEFT JOIN ventas v ON v.id = dp.venta_id
            WHERE COALESCE(dp.retirado, 0) = 0
        """
        params: List[Any] = []
        search = (search or "").strip()
        if search:
            like = f"%{search}%"
            query += (
                f" AND (v.cliente_nombre LIKE {ph}"
                f" OR v.numero_venta LIKE {ph}"
                f" OR dp.local LIKE {ph})"
            )
            params.extend([like, like, like])
        query += " ORDER BY dp.created_at DESC, dp.id DESC"
        cur.execute(query, tuple(params))
        rows = cur.fetchall() or []
        out: List[Dict[str, Any]] = []
        columns = [d[0] for d in cur.description]
        for row in rows:
            rec = dict(zip(columns, row))
            calle = str(rec.get("cliente_calle") or "").strip()
            numero = str(rec.get("cliente_numero") or "").strip()
            localidad = str(rec.get("cliente_localidad") or "").strip()
            entre = str(rec.get("entre_calles") or "").strip()
            direccion = " ".join(p for p in [calle, numero] if p)
            if localidad:
                direccion = f"{direccion} - {localidad}" if direccion else localidad
            if entre:
                direccion = (
                    f"{direccion} (entre {entre})" if direccion else f"Entre {entre}"
                )
            rec["direccion"] = direccion
            try:
                rec["monto"] = float(rec.get("monto") or 0)
            except Exception:
                rec["monto"] = 0.0
            try:
                monto_productos = rec.get("monto_productos")
                if monto_productos is None:
                    forma_envio = str(rec.get("forma_pago_envio") or "").strip().lower()
                    precio_envio = float(rec.get("precio_envio") or 0)
                    bruto = float(rec.get("monto") or 0)
                    if precio_envio > 0 and forma_envio in (
                        "domicilio",
                        "en domicilio",
                    ):
                        monto_productos = max(0.0, bruto - precio_envio)
                    else:
                        monto_productos = bruto
                rec["monto_productos"] = float(monto_productos or 0)
            except Exception:
                rec["monto_productos"] = float(rec.get("monto") or 0)
            if float(rec.get("monto_productos") or 0) <= 0:
                continue
            out.append(rec)
        return out
    except Exception:
        logger.exception("Error obteniendo cobros pendientes en domicilio")
        return []
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def retirar_domicilio_pagos(
    pago_ids: List[int], usuario: str = ""
) -> Tuple[bool, str, float, int]:
    try:
        ok, msg = ensure_domicilio_pagos_schema()
        if not ok:
            return False, msg, 0.0, 0
        ids = [int(v) for v in (pago_ids or []) if int(v or 0) > 0]
        if not ids:
            return False, "No hay cobros seleccionados", 0.0, 0
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        placeholders = ", ".join([ph] * len(ids))
        pending_rows = get_domicilio_pagos_pending("")
        selected_rows = [r for r in pending_rows if int(r.get("id") or 0) in ids]
        total = 0.0
        count = len(selected_rows)
        for row in selected_rows:
            try:
                total += float(row.get("monto_productos") or 0)
            except Exception:
                pass
        if count <= 0 or total <= 0:
            return False, "Los cobros ya fueron retirados o no existen", 0.0, 0
        params: List[Any] = [_now_local(), (usuario or "").strip()]
        params.extend(ids)
        cur.execute(
            f"""
            UPDATE domicilio_pagos
            SET retirado=1, retirado_at={ph}, retirado_por={ph}
            WHERE id IN ({placeholders}) AND COALESCE(retirado,0)=0
            """,
            tuple(params),
        )
        conn.commit()
        return True, "OK", total, count
    except Exception as e:
        logger.exception("Error retirando cobros en domicilio")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e), 0.0, 0
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_domicilio_pagos_total(venta_ids: List[int], local: str = "") -> float:
    """Suma total cobrado en domicilio para una lista de ventas (opcionalmente por local)."""
    try:
        ok, _ = ensure_domicilio_pagos_schema()
        if not ok:
            return 0.0
        ids = [int(v) for v in (venta_ids or []) if int(v or 0) > 0]
        if not ids:
            return 0.0
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        placeholders = ", ".join([ph] * len(ids))
        params: List[Any] = list(ids)
        where = f"venta_id IN ({placeholders})"
        local = (local or "").strip()
        if local and local not in ("Todos", "Todos los locales"):
            where += f" AND LOWER(TRIM(local)) = LOWER(TRIM({ph}))"
            params.append(local)
        cur.execute(
            f"SELECT COALESCE(SUM(monto), 0) FROM domicilio_pagos WHERE {where}",
            tuple(params),
        )
        row = cur.fetchone()
        return float((row[0] if row else 0) or 0)
    except Exception:
        logger.exception("Error obteniendo total de cobros en domicilio")
        return 0.0
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def add_cash_withdrawal(local: str, amount: float, usuario: str) -> Tuple[bool, str]:
    try:
        ok, msg = ensure_cash_withdrawals_schema()
        if not ok:
            return False, msg
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        cur.execute(
            f"INSERT INTO cash_withdrawals (local, amount, usuario, created_at) VALUES ({ph},{ph},{ph},{ph})",
            (
                (local or "").strip(),
                float(amount or 0),
                (usuario or "").strip(),
                _now_local(),
            ),
        )
        conn.commit()
        return True, "OK"
    except Exception as e:
        logger.exception("Error registrando retiro de efectivo")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_cash_withdrawn_total(
    local: str,
    filtro_fecha: str = "hoy",
    fecha_dia: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
) -> float:
    try:
        ok, _ = ensure_cash_withdrawals_schema()
        if not ok:
            return 0.0
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        where = []
        params = []
        if local and local not in ("Todos", "Todos los locales"):
            where.append(f"local={ph}")
            params.append(local)
        if filtro_fecha == "dia" and fecha_dia:
            where.append(f"created_at >= {ph} AND created_at <= {ph}")
            params.append(f"{fecha_dia} 00:00:00")
            params.append(f"{fecha_dia} 23:59:59")
        elif filtro_fecha == "semana":
            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            where.append(f"created_at >= {ph}")
            params.append(cutoff)
        elif filtro_fecha == "mes":
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            where.append(f"created_at >= {ph}")
            params.append(cutoff)
        elif filtro_fecha == "todo" and fecha_inicio and fecha_fin:
            where.append(f"created_at >= {ph} AND created_at <= {ph}")
            params.append(f"{fecha_inicio} 00:00:00")
            params.append(f"{fecha_fin} 23:59:59")
        else:
            cutoff = (
                datetime.now()
                .replace(hour=0, minute=0, second=0)
                .strftime("%Y-%m-%d %H:%M:%S")
            )
            where.append(f"created_at >= {ph}")
            params.append(cutoff)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        cur.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM cash_withdrawals {where_sql}",
            tuple(params),
        )
        row = cur.fetchone()
        return float(row[0] or 0)
    except Exception:
        logger.exception("Error obteniendo retiros de efectivo")
        return 0.0
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def _enqueue_decrement_for_venta(
    venta: Dict, usuario: str, detalle: str, motivo: str, local_override: str = ""
) -> None:
    local = local_override or (venta.get("local") or "")
    vendedor = venta.get("vendedor") or ""
    expanded = _expand_items_for_stock(
        venta.get("items") or [],
        local,
        skip_delivered=True,
    )
    for it in expanded:
        pid = int(it.get("producto_id") or 0)
        qty = int(it.get("cantidad") or 0)
        if pid <= 0 or qty <= 0:
            continue
        payload = {
            "producto_id": pid,
            "delta": qty,
            "usuario": usuario or vendedor or "sistema",
            "local": (it.get("local") or local),
            "detalle": detalle,
            "motivo": motivo,
            "venta_id": int(venta.get("id") or 0),
            "precio_venta": 0,
        }
        try:
            qa.enqueue_op("decrement", payload)
        except Exception:
            logger.exception("Error encolando decremento por venta")


def completar_pago_sena(
    venta_id: int, forma_final: str = "Efectivo", usuario: str = ""
) -> Tuple[bool, str]:
    # Marca una venta con sena como pagada y registra el pago final.
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"

        cur.execute(
            f"""
            SELECT id, total, monto_pagado, monto_pendiente, tipo_pago,
                   incluye_envio, entrega_entregado, local
            FROM ventas WHERE id={ph}
            """,
            (venta_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Venta no encontrada"

        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))

        total = float(data.get("total") or 0)
        pendiente = float(data.get("monto_pendiente") or 0)
        tipo = (data.get("tipo_pago") or "").lower()
        incluye_envio = int(data.get("incluye_envio") or 0)

        if tipo != "sena":
            return False, "La venta no es una sena pendiente"
        if pendiente <= 0.0001:
            return False, "La venta ya esta saldada"

        # Actualizar venta: se cobra el resto hoy (usar hora local para evitar desfasajes por TZ del servidor)
        ahora = _now_local()
        update_sql = (
            f"UPDATE ventas "
            f"SET monto_pagado = {ph}, monto_pendiente = 0, tipo_pago = 'completo', "
            f"forma_pago = {ph}, pago_completado_fecha = {ph}, pago_completado_monto = {ph}, "
            f"pago_completado_forma = {ph}, pago_completado_tipo = 'sena' "
            f"WHERE id = {ph}"
        )
        params = (
            float(total),
            forma_final,
            ahora,
            float(pendiente),
            forma_final,
            venta_id,
        )
        cur.execute(update_sql, params)

        # Registrar pago final en venta_pagos si la tabla existe
        try:
            has_venta_pagos = False
            if is_postgres():
                cur.execute("SELECT to_regclass('public.venta_pagos')")
                r = cur.fetchone()
                has_venta_pagos = bool(r and r[0])
            else:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'"
                )
                has_venta_pagos = cur.fetchone() is not None

            if has_venta_pagos:
                cur.execute(
                    f"INSERT INTO venta_pagos (venta_id, forma, monto) VALUES ({ph},{ph},{ph})",
                    (venta_id, forma_final, float(pendiente)),
                )
        except Exception:
            logger.exception("No se pudo insertar venta_pagos (se continua igual).")

        if incluye_envio == 0:
            try:
                cur.execute(
                    f"""
                    UPDATE detalle_ventas
                    SET entrega_local=stock_local, entrega_local_entregado=1, entrega_local_fecha={ph}
                    WHERE venta_id={ph}
                      AND COALESCE(stock_local,'') <> ''
                      AND COALESCE(stock_local,'') <> COALESCE({ph},'')
                      AND COALESCE(entrega_local_entregado,0)=0
                    """,
                    (ahora, int(venta_id), data.get("local") or ""),
                )
            except Exception:
                logger.exception(
                    "No se pudo marcar detalle interlocal al completar seña"
                )
        conn.commit()

        if incluye_envio == 0:
            venta_det = get_venta_detalle(venta_id)
            if venta_det:
                _enqueue_decrement_for_venta(
                    venta_det,
                    usuario,
                    "Venta completada (seña)",
                    "venta",
                )
        return True, "Pago completado"
    except Exception:
        logger.exception("Error completando pago de sena")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, "Error completando pago"
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def actualizar_envio_venta(
    venta_id: int,
    incluye_envio: int,
    precio_envio: float,
    forma_pago_envio: Optional[str] = None,
    entrega_programada: Optional[str] = None,
) -> Tuple[bool, str]:
    """Actualiza datos de envio de una venta (incluye_envio, precio_envio, forma_pago_envio) y recalcula total."""
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"

        cur.execute(
            f"""
            SELECT subtotal_productos, descuento_aplicado, tarjeta_interes_monto,
                   precio_envio, monto_pagado, monto_pendiente, tipo_pago
            FROM ventas
            WHERE id={ph}
            """,
            (int(venta_id),),
        )
        row = cur.fetchone()
        if not row:
            return False, "Venta no encontrada"

        (
            subtotal_productos,
            descuento_aplicado,
            tarjeta_interes_monto,
            envio_actual,
            monto_pagado,
            monto_pendiente,
            tipo_pago,
        ) = row
        try:
            subtotal_productos = float(subtotal_productos or 0)
        except Exception:
            subtotal_productos = 0.0
        try:
            descuento_aplicado = float(descuento_aplicado or 0)
        except Exception:
            descuento_aplicado = 0.0
        try:
            tarjeta_interes_monto = float(tarjeta_interes_monto or 0)
        except Exception:
            tarjeta_interes_monto = 0.0
        try:
            envio_actual = float(envio_actual or 0)
        except Exception:
            envio_actual = 0.0
        try:
            monto_pagado = float(monto_pagado or 0)
        except Exception:
            monto_pagado = 0.0
        try:
            monto_pendiente = float(monto_pendiente or 0)
        except Exception:
            monto_pendiente = 0.0
        tipo = (tipo_pago or "").strip().lower()

        incluye_envio = int(incluye_envio or 0)
        nuevo_envio = float(precio_envio or 0) if incluye_envio == 1 else 0.0
        total_productos = max(
            0.0, subtotal_productos - descuento_aplicado + tarjeta_interes_monto
        )
        nuevo_total = float(total_productos)

        # Recalcular pendiente/pagado segun tipo de pago
        nuevo_pagado = monto_pagado
        nuevo_pendiente = monto_pendiente
        if tipo == "completo":
            nuevo_pagado = nuevo_total
            nuevo_pendiente = 0.0
        elif tipo in ("sena", "domicilio"):
            nuevo_pagado = monto_pagado
            nuevo_pendiente = max(0.0, nuevo_total - nuevo_pagado)
        elif tipo == "credito_personal":
            # no ajustar pagos automaticamente
            nuevo_pagado = monto_pagado
            nuevo_pendiente = monto_pendiente
        else:
            nuevo_pagado = monto_pagado
            nuevo_pendiente = max(0.0, nuevo_total - nuevo_pagado)

        forma_envio_norm = (forma_pago_envio or "").strip().lower()
        if forma_envio_norm in ("local", "en local"):
            forma_envio_db = "local"
        elif forma_envio_norm in ("domicilio", "en domicilio"):
            forma_envio_db = "domicilio"
        else:
            forma_envio_db = None

        set_parts = [
            f"incluye_envio={ph}",
            f"precio_envio={ph}",
            f"total={ph}",
            f"monto_pagado={ph}",
            f"monto_pendiente={ph}",
        ]
        params = [
            incluye_envio,
            float(nuevo_envio),
            float(nuevo_total),
            float(nuevo_pagado),
            float(nuevo_pendiente),
        ]
        if _has_envio_pago_column(cur):
            set_parts.append(f"forma_pago_envio={ph}")
            params.append(forma_envio_db)

        params.append(int(venta_id))
        cur.execute(
            f"UPDATE ventas SET {', '.join(set_parts)} WHERE id={ph}",
            tuple(params),
        )

        # Ajustar venta_pagos si existe y hay un solo pago (venta completa)
        try:
            has_venta_pagos = False
            if is_postgres():
                cur.execute("SELECT to_regclass('public.venta_pagos')")
                r = cur.fetchone()
                has_venta_pagos = bool(r and r[0])
            else:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'"
                )
                has_venta_pagos = cur.fetchone() is not None

            if has_venta_pagos and tipo == "completo":
                cur.execute(
                    f"SELECT id FROM venta_pagos WHERE venta_id={ph} ORDER BY id",
                    (int(venta_id),),
                )
                pagos_rows = cur.fetchall() or []
                if len(pagos_rows) == 1:
                    pago_id = pagos_rows[0][0]
                    cur.execute(
                        f"UPDATE venta_pagos SET monto={ph} WHERE id={ph}",
                        (float(nuevo_total), int(pago_id)),
                    )
        except Exception:
            logger.exception(
                "No se pudo actualizar venta_pagos al editar envio (se continua igual)."
            )

        conn.commit()
        return True, "OK"
    except Exception as e:
        logger.exception("Error actualizando envio de venta")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def _registrar_venta_online(
    local: str,
    vendedor: str,
    cliente_data: Dict,
    items: List[Dict],
    descuento_tipo: Optional[str] = None,
    descuento_valor: float = 0,
    forma_pago: str = "Efectivo",
    tipo_pago: str = "completo",
    monto_pagado: Optional[float] = None,
    monto_pendiente: Optional[float] = None,
    incluye_envio: int = 0,
    precio_envio: float = 0,
    forma_pago_envio: Optional[str] = None,
    entrega_programada: Optional[str] = None,
    notas: str = "",
    pagos: Optional[List[Dict]] = None,
    tarjeta_cuotas: int = 0,
    tarjeta_interes_pct: float = 0,
    tarjeta_interes_monto: float = 0,
) -> Tuple[bool, str, Optional[int]]:
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg, None
        if not items:
            return False, "No hay productos en la venta", None
        subtotal = sum(item["cantidad"] * item["precio_unitario"] for item in items)
        descuento_aplicado = 0
        if descuento_tipo == "porcentaje" and descuento_valor > 0:
            descuento_aplicado = subtotal * (descuento_valor / 100.0)
        elif descuento_tipo == "monto" and descuento_valor > 0:
            descuento_aplicado = min(descuento_valor, subtotal)
        # Normalizar a montos "enteros" para evitar centavos inconsistentes en UI/PDF
        total_productos_float = float(subtotal) - float(descuento_aplicado)
        total_productos_int = int(max(0.0, total_productos_float))
        # Ajustar descuento aplicado para que cierre con el total entero
        descuento_aplicado = max(0.0, float(subtotal) - float(total_productos_int))
        precio_envio_val = int(precio_envio or 0)
        interes_val = int(tarjeta_interes_monto or 0)
        total = float(total_productos_int + interes_val)

        tipo_pago_norm = (tipo_pago or "completo").strip().lower()
        if monto_pagado is None:
            # Por defecto, si no es sena/domicilio, consideramos pago completo.
            if tipo_pago_norm in ("sena", "domicilio"):
                monto_pagado = 0.0
            else:
                monto_pagado = float(total)
        if monto_pendiente is None:
            try:
                monto_pendiente = max(0.0, float(total) - float(monto_pagado))
            except Exception:
                monto_pendiente = 0.0

        incluye_envio = int(incluye_envio or 0)
        entre_calles = (cliente_data.get("entre_calles") or "").strip()
        notas = (notas or "").strip()
        envio_pago_norm = (forma_pago_envio or "").strip()
        entrega_programada_norm = (entrega_programada or "").strip()

        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        has_envio_pago_col = _has_envio_pago_column(cur)
        if envio_pago_norm and not has_envio_pago_col:
            extra = f"Envio pago: {envio_pago_norm}"
            notas = f"{notas} | {extra}" if notas else extra
        ahora = _now_local()
        numero_venta = _generate_numero_venta()

        cols = [
            "numero_venta",
            "local",
            "fecha",
            "fecha_venta_original",
            "vendedor",
            "cliente_nombre",
            "cliente_telefono",
            "cliente_calle",
            "cliente_numero",
            "cliente_localidad",
            "subtotal_productos",
            "precio_envio",
            "incluye_envio",
            "entre_calles",
        ]
        vals = [
            numero_venta,
            local,
            ahora,
            ahora,
            vendedor,
            cliente_data.get("nombre", ""),
            cliente_data.get("telefono", ""),
            cliente_data.get("calle", ""),
            cliente_data.get("numero", ""),
            cliente_data.get("localidad", ""),
            float(subtotal),
            float(precio_envio_val),
            int(incluye_envio),
            entre_calles,
        ]
        if has_envio_pago_col:
            cols.append("forma_pago_envio")
            vals.append(envio_pago_norm or None)
        if _has_entrega_programada_column(cur):
            cols.append("entrega_programada")
            vals.append(entrega_programada_norm or None)
        cols += [
            "tarjeta_cuotas",
            "tarjeta_interes_pct",
            "tarjeta_interes_monto",
        ]
        vals += [
            int(tarjeta_cuotas or 0),
            float(tarjeta_interes_pct or 0),
            float(tarjeta_interes_monto or 0),
        ]
        cols += [
            "descuento_tipo",
            "descuento_valor",
            "descuento_aplicado",
            "total",
            "forma_pago",
            "tipo_pago",
            "tipo_pago_origen",
            "forma_pago_origen",
            "monto_pagado",
            "monto_pendiente",
            "notas",
            "estado",
            "pdf_generado",
        ]
        vals += [
            descuento_tipo,
            float(descuento_valor or 0),
            float(descuento_aplicado),
            float(total),
            forma_pago,
            (tipo_pago or "completo"),
            (tipo_pago or "completo"),
            forma_pago,
            float(monto_pagado or 0),
            float(monto_pendiente or 0),
            notas,
            "completada",
            0,
        ]

        cols_sql = ", ".join(cols)
        placeholders = ", ".join([ph] * len(cols))
        sql = f"INSERT INTO ventas ({cols_sql}) VALUES ({placeholders})"
        if is_postgres():
            sql += " RETURNING id"
        cur.execute(sql, tuple(vals))
        if is_postgres():
            row = cur.fetchone()
            venta_id = int(row[0]) if row else None
        else:
            venta_id = cur.lastrowid

        hold_stock = (tipo_pago_norm == "sena") or (incluye_envio == 1)
        to_enqueue = []
        interlocal_items = []
        now_ts = _now_local()
        for item in items:
            stock_local = (item.get("stock_local") or "").strip() or local
            is_combo = _is_combo_item(item)
            combo_id = (
                int(item.get("combo_id") or item.get("producto_id") or 0)
                if is_combo
                else None
            )
            is_interlocal = stock_local != local
            hold_stock_item = hold_stock
            detail_entregado = 1 if (is_interlocal and not hold_stock_item) else 0
            detail_entrega_fecha = now_ts if detail_entregado == 1 else None
            cur.execute(
                f"INSERT INTO detalle_ventas ("
                f"venta_id, producto_id, producto_nombre, producto_material, producto_categoria, producto_fabricante, "
                f"producto_medida, producto_estado, producto_color, "
                f"cantidad, precio_unitario, subtotal, stock_local, entrega_local_entregado, entrega_local_fecha, "
                f"is_combo, combo_id"
                f") VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (
                    venta_id,
                    item["producto_id"],
                    item["nombre"],
                    item.get("material", ""),
                    item.get("categoria", ""),
                    item.get("fabricante", ""),
                    item.get("medida", ""),
                    item.get("estado", ""),
                    item.get("color", ""),
                    item["cantidad"],
                    item["precio_unitario"],
                    item["cantidad"] * item["precio_unitario"],
                    stock_local,
                    detail_entregado,
                    detail_entrega_fecha,
                    1 if is_combo else 0,
                    combo_id,
                ),
            )
            if is_combo:
                components = _combo_components_for_item(item)
                for comp in components:
                    comp_pid = int(comp.get("producto_id") or 0)
                    comp_qty_unit = int(comp.get("cantidad") or 0)
                    if comp_pid <= 0 or comp_qty_unit <= 0:
                        continue
                    total_qty = comp_qty_unit * int(item.get("cantidad") or 0)
                    if total_qty <= 0:
                        continue
                    if not hold_stock_item:
                        payload = {
                            "producto_id": comp_pid,
                            "delta": int(total_qty),
                            "usuario": vendedor,
                            "local": stock_local,
                            "detalle": f"Venta combo: {item.get('nombre')}",
                            "motivo": "venta",
                            "venta_id": int(venta_id) if venta_id else 0,
                            "precio_venta": 0,
                        }
                        to_enqueue.append(payload)
                    if is_interlocal:
                        interlocal_items.append(
                            {
                                "stock_local": stock_local,
                                "qty": int(total_qty),
                                "product": {
                                    "nombre": item.get("nombre"),
                                    "categoria": "Combo",
                                    "medida": "",
                                    "estado": item.get("estado"),
                                    "color": item.get("color"),
                                },
                            }
                        )
            else:
                if not hold_stock_item:
                    payload = {
                        "producto_id": int(item["producto_id"]),
                        "delta": int(item["cantidad"]),
                        "usuario": vendedor,
                        "local": stock_local,
                        "detalle": "Venta",
                        "motivo": "venta",
                        "venta_id": int(venta_id) if venta_id else 0,
                        "precio_venta": float(item.get("precio_unitario") or 0),
                    }
                    to_enqueue.append(payload)
                if is_interlocal:
                    interlocal_items.append(
                        {
                            "stock_local": stock_local,
                            "qty": int(item.get("cantidad") or 0),
                            "product": {
                                "nombre": item.get("nombre"),
                                "categoria": item.get("categoria"),
                                "medida": item.get("medida"),
                                "estado": item.get("estado"),
                                "color": item.get("color"),
                            },
                        }
                    )

        # Insertar desglose de pagos (si existe tabla venta_pagos)
        if pagos is None:
            mp = float(monto_pagado or 0)
            pagos_to_insert = [{"forma": forma_pago, "monto": mp}] if mp > 0 else []
        elif isinstance(pagos, list):
            pagos_to_insert = pagos
        else:
            pagos_to_insert = []
        try:
            has_venta_pagos = False
            if is_postgres():
                cur.execute("SELECT to_regclass('public.venta_pagos')")
                r = cur.fetchone()
                has_venta_pagos = bool(r and r[0])
            else:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'"
                )
                has_venta_pagos = cur.fetchone() is not None

            if has_venta_pagos and venta_id:
                for p in pagos_to_insert:
                    fp = (p.get("forma") or "").strip() or "Otros"
                    monto = float(p.get("monto") or 0)
                    if monto <= 0:
                        continue
                    cur.execute(
                        f"INSERT INTO venta_pagos (venta_id, forma, monto) VALUES ({ph},{ph},{ph})",
                        (venta_id, fp, monto),
                    )
        except Exception:
            logger.exception("No se pudo insertar venta_pagos (se continua igual).")

        conn.commit()
        put_conn(conn)

        # Notificar al local de cuyo stock se vendió (si aplica)
        try:
            for it in interlocal_items:
                if it["qty"] <= 0:
                    continue
                sm.notify_interlocal_sale(
                    target_local=it["stock_local"],
                    source_local=local,
                    source_user=vendedor,
                    product=it["product"],
                    qty=it["qty"],
                    venta_id=int(venta_id) if venta_id else 0,
                    include_envio=bool(incluye_envio),
                )
        except Exception:
            logger.exception("Error notificando venta de otro local")

        # --- DUAL WRITE: Firestore ---
        try:
            from models import firestore_db

            # Preparar datos para Firestore
            fs_venta_data = {
                "local": local,
                "vendedor": vendedor,
                "cliente": cliente_data,
                "subtotal": subtotal,
                "descuento_tipo": descuento_tipo,
                "descuento_valor": descuento_valor,
                "descuento_monto": descuento_aplicado,  # renombrado para claridad
                "total": total,
                "forma_pago": forma_pago,
                "numero_venta": numero_venta,
                "sql_id": venta_id,  # Referencia cruzada
                "fecha_emision": ahora,
            }
            # Preparar items
            fs_items = []
            for item in items:
                fs_items.append(
                    {
                        "producto_id": item["producto_id"],
                        "nombre": item["nombre"],
                        "cantidad": item["cantidad"],
                        "precio_unitario": item["precio_unitario"],
                        "subtotal": item["cantidad"] * item["precio_unitario"],
                        "material": item.get("material", ""),
                        "categoria": item.get("categoria", ""),
                        "medida": item.get("medida", ""),
                        "estado": item.get("estado", ""),
                    }
                )

            firestore_db.add_venta(fs_venta_data, fs_items)
            logger.info(f"Venta {numero_venta} replicada en Firestore")
        except Exception as e:
            logger.error(f"Error replicando venta en Firestore (Dual Write): {e}")
            # No fallamos la venta si falla Firestore, ya que SQL es la fuente de verdad "dura"
            # y el usuario dijo "PostgreSQL es totalmente necesario".

        # Encolar decrementos despues del commit para evitar "database is locked" en SQLite
        for payload in to_enqueue:
            try:
                enqueue_result = qa.enqueue_op("decrement", payload)
                logger.debug(
                    f"Decremento encolado para producto {payload.get('producto_id')}: ID queue = {enqueue_result}"
                )
            except Exception:
                logger.exception("Error encolando decremento post-commit")

        # NOTA: No procesamos la cola inmediatamente aqui; dejamos que el worker
        # o el test invoque el procesamiento explicitamente. Esto evita que
        # las pruebas que esperan items en estado pendiente (status=0) fallen.

        logger.info(f"Venta registrada: ID={venta_id}, Total={total:.2f}")
        return True, f"Venta registrada. Total: ${int(total)}", venta_id

    except Exception as e:
        logger.exception(f"Error: {e}")
        try:
            if "conn" in locals() and conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    put_conn(conn)
                except Exception:
                    pass
        except Exception:
            pass
        return False, str(e), None


def registrar_venta(
    local: str,
    vendedor: str,
    cliente_data: Dict,
    items: List[Dict],
    descuento_tipo: Optional[str] = None,
    descuento_valor: float = 0,
    forma_pago: str = "Efectivo",
    tipo_pago: str = "completo",
    monto_pagado: Optional[float] = None,
    monto_pendiente: Optional[float] = None,
    incluye_envio: int = 0,
    precio_envio: float = 0,
    forma_pago_envio: Optional[str] = None,
    entrega_programada: Optional[str] = None,
    notas: str = "",
    pagos: Optional[List[Dict]] = None,
    tarjeta_cuotas: int = 0,
    tarjeta_interes_pct: float = 0,
    tarjeta_interes_monto: float = 0,
) -> Tuple[bool, str, Optional[int]]:
    payload = dict(
        local=local,
        vendedor=vendedor,
        cliente_data=cliente_data,
        items=items,
        descuento_tipo=descuento_tipo,
        descuento_valor=descuento_valor,
        forma_pago=forma_pago,
        tipo_pago=tipo_pago,
        monto_pagado=monto_pagado,
        monto_pendiente=monto_pendiente,
        incluye_envio=incluye_envio,
        precio_envio=precio_envio,
        forma_pago_envio=forma_pago_envio,
        entrega_programada=entrega_programada,
        notas=notas,
        pagos=pagos,
        tarjeta_cuotas=tarjeta_cuotas,
        tarjeta_interes_pct=tarjeta_interes_pct,
        tarjeta_interes_monto=tarjeta_interes_monto,
    )

    # Si no hay conexion, guardar venta offline
    if not offline_store.is_online():
        offline_id = offline_store.enqueue_venta(payload)
        offline_store.apply_offline_sale_to_cache(items)
        return (
            True,
            "Venta registrada en modo offline. Se sincronizara al volver internet.",
            -offline_id,
        )

    try:
        return _registrar_venta_online(**payload)
    except Exception as e:
        if offline_store.is_offline_exception(e):
            offline_id = offline_store.enqueue_venta(payload)
            offline_store.apply_offline_sale_to_cache(items)
            return (
                True,
                "Venta registrada en modo offline. Se sincronizara al volver internet.",
                -offline_id,
            )
        return False, str(e), None


def get_ventas(
    local: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    filtro_periodo: str = "mes",
) -> List[Dict]:
    try:
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        query = "SELECT * FROM ventas WHERE 1=1"
        params = []
        if local:
            query += f" AND local={ph}"
            params.append(local)
        now_dt = datetime.now()
        if filtro_periodo == "mes":
            cutoff = (now_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            query += f" AND fecha >= {ph}"
            params.append(cutoff)
        query += " ORDER BY fecha DESC, id DESC"
        cur.execute(query, tuple(params))
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception:
        logger.exception("Error obteniendo ventas")
        return []
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def get_venta_detalle(venta_id: int) -> Optional[Dict]:
    try:
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        cur.execute(f"SELECT * FROM ventas WHERE id={ph}", (venta_id,))
        venta_row = cur.fetchone()
        if not venta_row:
            return None
        columns = [desc[0] for desc in cur.description]
        venta = dict(zip(columns, venta_row))
        cur.execute(
            f"SELECT * FROM detalle_ventas WHERE venta_id={ph} ORDER BY id", (venta_id,)
        )
        items_columns = [desc[0] for desc in cur.description]
        venta["items"] = [dict(zip(items_columns, row)) for row in cur.fetchall()]

        # Adjuntar pagos por forma si existe tabla venta_pagos
        try:
            has_venta_pagos = False
            if is_postgres():
                cur.execute("SELECT to_regclass('public.venta_pagos')")
                r = cur.fetchone()
                has_venta_pagos = bool(r and r[0])
            else:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'"
                )
                has_venta_pagos = cur.fetchone() is not None

            if has_venta_pagos:
                cur.execute(
                    f"SELECT forma, monto FROM venta_pagos WHERE venta_id={ph} ORDER BY id",
                    (venta_id,),
                )
                pagos_rows = cur.fetchall()
                pagos = []
                for pr in pagos_rows:
                    try:
                        pagos.append({"forma": pr[0], "monto": float(pr[1])})
                    except Exception:
                        pass
                venta["pagos"] = pagos
            else:
                venta["pagos"] = []
        except Exception:
            try:
                if is_postgres():
                    conn.rollback()
            except Exception:
                pass
            venta["pagos"] = []

        return venta
    except Exception:
        logger.exception("Error obteniendo detalle")
        return None
    finally:
        try:
            if "conn" in locals() and conn:
                put_conn(conn)
        except Exception:
            pass


def _fmt_money(value):
    try:
        return "{:,}".format(int(value)).replace(",", ".")
    except Exception:
        return str(value)


def _build_boleta_dict_from_venta(venta: Dict) -> Dict:
    def _to_float(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    return {
        "local": venta.get("local"),
        "numero_boleta": venta.get("numero_venta"),
        "fecha_emision": venta.get("fecha") or _now_local(),
        "fecha_venta_original": venta.get("fecha_venta_original")
        or venta.get("fecha")
        or _now_local(),
        "cliente": {
            "nombre": venta.get("cliente_nombre", ""),
            "telefono": venta.get("cliente_telefono", ""),
            "calle": venta.get("cliente_calle", ""),
            "numero": venta.get("cliente_numero", ""),
            "localidad": venta.get("cliente_localidad", ""),
            "entre_calles": venta.get("entre_calles", ""),
        },
        "detalle": venta.get("items", []) or [],
        "subtotal": _to_float(venta.get("subtotal_productos")),
        "precio_envio": _to_float(venta.get("precio_envio")),
        "incluye_envio": int(venta.get("incluye_envio") or 0),
        "forma_pago_envio": str(venta.get("forma_pago_envio") or "").strip(),
        "descuento_monto": _to_float(venta.get("descuento_aplicado")),
        "total": _to_float(venta.get("total")),
        "pago": {
            "tipo_abono": (venta.get("tipo_pago") or "completo"),
            "forma_pago": (venta.get("forma_pago") or ""),
            "formas": venta.get("pagos") or [],
            "monto_sena": _to_float(venta.get("monto_pagado")),
            "monto_restante": _to_float(venta.get("monto_pendiente")),
            "tarjeta_cuotas": venta.get("tarjeta_cuotas") or 0,
            "tarjeta_interes_pct": venta.get("tarjeta_interes_pct") or 0,
            "tarjeta_interes_monto": venta.get("tarjeta_interes_monto") or 0,
        },
    }


def generar_pdf_boleta(venta_id: int) -> Tuple[bool, str]:
    try:
        from models.boletas_model import generar_boleta_pdf_a4_duplicada

        if int(venta_id) < 0:
            offline_id = abs(int(venta_id))
            payload = offline_store.get_offline_venta(offline_id)
            if not payload:
                return False, "Venta offline no encontrada"
            boleta = _boleta_from_payload(payload)
            temp_dir = tempfile.mkdtemp(prefix="manarey_boleta_")
            fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(
                temp_dir, f"boleta_offline_{offline_id}_{fecha_str}.pdf"
            )
            ok, res = generar_boleta_pdf_a4_duplicada(boleta, filepath)
            if not ok:
                return False, str(res)
            return True, filepath

        venta = get_venta_detalle(venta_id)
        if not venta:
            return False, "Venta no encontrada"

        temp_dir = tempfile.mkdtemp(prefix="manarey_boleta_")
        fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(temp_dir, f"boleta_{venta_id}_{fecha_str}.pdf")
        boleta = _build_boleta_dict_from_venta(venta)

        ok, res = generar_boleta_pdf_a4_duplicada(boleta, filepath)
        if not ok:
            return False, str(res)

        # Marcar venta como con PDF generado (no es fatal si falla)
        try:
            conn = get_conn()
            cur = conn.cursor()
            ph = "%s" if is_postgres() else "?"
            cur.execute(f"UPDATE ventas SET pdf_generado=1 WHERE id={ph}", (venta_id,))
            conn.commit()
        except Exception:
            try:
                if "conn" in locals() and conn:
                    conn.rollback()
            except Exception:
                pass
        finally:
            try:
                if "conn" in locals() and conn:
                    put_conn(conn)
            except Exception:
                pass

        return True, filepath

    except ImportError as ie:
        logger.warning(f"Dependencia no instalada para PDF: {ie}")
        return False, "No se pudo generar el PDF (dependencias faltantes)"
    except Exception as e:
        logger.exception("Error PDF")
        return False, str(e)


def generar_pdf_completacion_pago(venta_id: int) -> Tuple[bool, str]:
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg
        from models.boletas_model import generar_boleta_pdf_a4_duplicada

        venta = get_venta_detalle(venta_id)
        if not venta:
            return False, "Venta no encontrada"

        try:
            monto_comp = float(venta.get("pago_completado_monto") or 0)
        except Exception:
            monto_comp = 0.0
        fecha_comp = venta.get("pago_completado_fecha")
        tipo_comp = (venta.get("pago_completado_tipo") or "").strip().lower()
        if (
            monto_comp <= 0.01
            or not fecha_comp
            or tipo_comp not in ("sena", "domicilio")
        ):
            return False, "La venta no tiene un comprobante de completado disponible"

        boleta = _build_boleta_dict_from_venta(venta)
        boleta["fecha_emision"] = fecha_comp
        boleta["completacion"] = {
            "tipo": tipo_comp,
            "fecha_original": venta.get("fecha_venta_original") or venta.get("fecha"),
            "fecha_completado": fecha_comp,
            "monto": monto_comp,
            "forma_pago": venta.get("pago_completado_forma")
            or venta.get("forma_pago")
            or "",
        }

        temp_dir = tempfile.mkdtemp(prefix="manarey_boleta_completacion_")
        fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(
            temp_dir, f"boleta_completacion_{venta_id}_{fecha_str}.pdf"
        )
        ok, res = generar_boleta_pdf_a4_duplicada(boleta, filepath)
        if not ok:
            return False, str(res)
        return True, filepath
    except Exception as e:
        logger.exception("Error PDF completacion")
        return False, str(e)


def _boleta_from_payload(payload: Dict) -> Dict:
    cliente = payload.get("cliente_data") or {}
    items = payload.get("items") or []
    subtotal = sum(
        float(it.get("cantidad") or 0) * float(it.get("precio_unitario") or 0)
        for it in items
    )
    desc_tipo = payload.get("descuento_tipo")
    desc_val = float(payload.get("descuento_valor") or 0)
    descuento_aplicado = 0.0
    if desc_tipo == "porcentaje" and desc_val > 0:
        descuento_aplicado = subtotal * (desc_val / 100.0)
    elif desc_tipo == "monto" and desc_val > 0:
        descuento_aplicado = min(desc_val, subtotal)
    total_productos_float = float(subtotal) - float(descuento_aplicado)
    total_productos_int = int(max(0.0, total_productos_float))
    descuento_aplicado = max(0.0, float(subtotal) - float(total_productos_int))
    precio_envio_val = int(payload.get("precio_envio") or 0)
    interes_val = int(payload.get("tarjeta_interes_monto") or 0)
    total = float(total_productos_int + interes_val)
    tipo_pago = payload.get("tipo_pago") or "completo"
    monto_pagado = payload.get("monto_pagado")
    if monto_pagado is None:
        if (tipo_pago or "").strip().lower() in ("sena", "domicilio"):
            monto_pagado = 0.0
        else:
            monto_pagado = float(total)
    monto_pendiente = payload.get("monto_pendiente")
    if monto_pendiente is None:
        monto_pendiente = max(0.0, float(total) - float(monto_pagado or 0))
    incluye_envio = int(payload.get("incluye_envio") or 0)
    envio_pago_metodo = str(payload.get("forma_pago_envio") or "").strip()
    precio_envio_boleta = float(precio_envio_val if incluye_envio else 0)
    total_boleta = float(total)

    return {
        "local": payload.get("local"),
        "numero_boleta": f"OFF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "fecha_emision": _now_local(),
        "cliente": {
            "nombre": cliente.get("nombre", ""),
            "telefono": cliente.get("telefono", ""),
            "calle": cliente.get("calle", ""),
            "numero": cliente.get("numero", ""),
            "localidad": cliente.get("localidad", ""),
            "entre_calles": cliente.get("entre_calles", ""),
        },
        "detalle": [
            {
                "producto_id": it.get("producto_id"),
                "producto_nombre": it.get("nombre"),
                "producto_material": it.get("material"),
                "producto_categoria": it.get("categoria"),
                "producto_medida": it.get("medida"),
                "producto_estado": it.get("estado"),
                "producto_color": it.get("color"),
                "producto_fabricante": it.get("fabricante"),
                "cantidad": it.get("cantidad"),
                "precio_unitario": it.get("precio_unitario"),
                "subtotal": float(it.get("cantidad") or 0)
                * float(it.get("precio_unitario") or 0),
                "stock_local": it.get("stock_local"),
            }
            for it in items
        ],
        "subtotal": float(subtotal),
        "precio_envio": float(precio_envio_boleta),
        "incluye_envio": int(incluye_envio or 0),
        "forma_pago_envio": envio_pago_metodo,
        "descuento_monto": float(descuento_aplicado),
        "total": float(total_boleta),
        "pago": {
            "tipo_abono": (tipo_pago or "completo"),
            "forma_pago": (payload.get("forma_pago") or ""),
            "formas": payload.get("pagos") or [],
            "monto_sena": float(monto_pagado or 0),
            "monto_restante": float(monto_pendiente or 0),
            "tarjeta_cuotas": payload.get("tarjeta_cuotas") or 0,
            "tarjeta_interes_pct": payload.get("tarjeta_interes_pct") or 0,
            "tarjeta_interes_monto": payload.get("tarjeta_interes_monto") or 0,
        },
    }


def generar_pdf_remito(venta_id: int) -> Tuple[bool, str]:
    try:
        from models.remitos_model import generar_remito_pdf

        venta = get_venta_detalle(venta_id)
        if not venta:
            return False, "Venta no encontrada"

        temp_dir = tempfile.mkdtemp(prefix="manarey_remito_")
        fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(temp_dir, f"remito_{venta_id}_{fecha_str}.pdf")

        ok, msg = generar_remito_pdf(venta, filepath)
        if not ok:
            return False, msg
        return True, filepath
    except Exception as e:
        logger.exception("Error generando remito")
        return False, str(e)


def generar_pdf_remitos(venta_ids: List[int]) -> Tuple[bool, str, List[str], List[int]]:
    try:
        from models.remitos_model import generar_remitos_pdf

        ventas = []
        errors: List[str] = []
        included_ids: List[int] = []
        for venta_id in venta_ids:
            venta = get_venta_detalle(venta_id)
            if not venta:
                errors.append(f"Venta {venta_id}: Venta no encontrada")
                continue
            ventas.append(venta)
            included_ids.append(int(venta_id))

        if not ventas:
            return False, "No hay ventas para imprimir", errors, []

        temp_dir = tempfile.mkdtemp(prefix="manarey_remitos_")
        fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(temp_dir, f"remitos_{fecha_str}.pdf")

        ok, msg = generar_remitos_pdf(ventas, filepath)
        if not ok:
            return False, msg, errors, []
        return True, filepath, errors, included_ids
    except Exception as e:
        logger.exception("Error generando remitos")
        return False, str(e), [], []


def marcar_remito_impreso(venta_id: int, impreso: bool = True) -> bool:
    conn = None
    try:
        ok, _ = ensure_envios_schema()
        if not ok:
            return False
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        if not _has_remito_impreso_column(cur):
            return False
        cur.execute(
            f"UPDATE ventas SET remito_impreso={ph} WHERE id={ph}",
            (1 if impreso else 0, venta_id),
        )
        conn.commit()
        return True
    except Exception:
        logger.exception("Error marcando remito impreso")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def marcar_entrega(
    venta_id: int,
    entregado: bool,
    motivo: str = "",
    usuario: str = "",
    local_entrega: str = "",
    local_por_item: Optional[List[Dict]] = None,
) -> Tuple[bool, str]:
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg
        try:
            ensure_domicilio_pagos_schema()
        except Exception:
            pass
        venta = get_venta_detalle(int(venta_id))
        if not venta:
            return False, "Venta no encontrada"
        incluye_envio = int(venta.get("incluye_envio") or 0)
        try:
            current_entregado = int(venta.get("entrega_entregado") or 0)
        except Exception:
            current_entregado = 0
        if int(entregado) == current_entregado:
            return True, "OK"
        expanded_for_decrement: List[Dict[str, Any]] = []
        if entregado and incluye_envio == 1 and current_entregado == 0:
            if local_por_item:
                local_by_detail = {}
                local_by_pid = {}
                local_by_detail_pid = {}
                local_by_pid_pid = {}
                for it in local_por_item:
                    try:
                        detail_id = int(it.get("detalle_id") or 0)
                        pid = int(it.get("producto_id") or 0)
                        entrega_pid = int(it.get("entrega_producto_id") or 0)
                    except Exception:
                        detail_id = 0
                        pid = 0
                        entrega_pid = 0
                    local_sel = (it.get("local") or "").strip()
                    if detail_id > 0:
                        local_by_detail[detail_id] = local_sel
                        if entrega_pid > 0:
                            local_by_detail_pid[detail_id] = entrega_pid
                    if pid > 0 and local_sel:
                        local_by_pid[pid] = local_sel
                        if entrega_pid > 0:
                            local_by_pid_pid[pid] = entrega_pid
                # Expandir por asignaciones (permite repartir cantidades entre locales)
                items_by_detail = {}
                items_by_pid = {}
                for it in venta.get("items") or []:
                    try:
                        did = int(it.get("id") or 0)
                        pid = int(it.get("producto_id") or 0)
                    except Exception:
                        did = 0
                        pid = 0
                    if did > 0:
                        items_by_detail[did] = it
                    if pid > 0:
                        items_by_pid[pid] = it

                expanded_for_decrement = []
                for alloc in local_por_item:
                    try:
                        detail_id = int(alloc.get("detalle_id") or 0)
                        pid = int(alloc.get("producto_id") or 0)
                        entrega_pid = int(alloc.get("entrega_producto_id") or 0)
                        qty = int(alloc.get("cantidad") or 0)
                    except Exception:
                        detail_id = 0
                        pid = 0
                        entrega_pid = 0
                        qty = 0
                    local_sel = (alloc.get("local") or "").strip()
                    if qty <= 0 or not local_sel:
                        continue
                    item = items_by_detail.get(detail_id) or items_by_pid.get(pid) or {}
                    if _is_combo_item(item):
                        comps = _combo_components_for_item_local(item, local_sel)
                        for comp in comps or []:
                            try:
                                cpid = int(comp.get("producto_id") or 0)
                                cqty = int(comp.get("cantidad") or 0)
                            except Exception:
                                cpid = 0
                                cqty = 0
                            if cpid <= 0 or cqty <= 0:
                                continue
                            expanded_for_decrement.append(
                                {
                                    "producto_id": cpid,
                                    "cantidad": int(cqty * qty),
                                    "local": local_sel,
                                    "nombre": comp.get("nombre") or "",
                                    "detalle_origen": f"Combo {item.get('producto_nombre') or item.get('nombre') or ''}".strip(),
                                }
                            )
                    else:
                        use_pid = (
                            entrega_pid or pid or int(item.get("producto_id") or 0)
                        )
                        if use_pid <= 0:
                            continue
                        expanded_for_decrement.append(
                            {
                                "producto_id": use_pid,
                                "cantidad": qty,
                                "local": local_sel,
                                "nombre": item.get("producto_nombre")
                                or item.get("nombre")
                                or "",
                                "detalle_origen": "",
                            }
                        )
            else:
                local_dec = (local_entrega or "").strip() or (venta.get("local") or "")
                expanded_for_decrement = _expand_items_for_stock(
                    venta.get("items") or [],
                    local_dec,
                    skip_delivered=True,
                )
            ok_stock, msg_stock = _validate_stock_for_entrega(expanded_for_decrement)
            if not ok_stock:
                return False, msg_stock
        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        if not _has_entrega_entregado_column(cur):
            return False, "Falta la columna entrega_entregado en ventas"

        entrega_fecha = _now_local() if entregado else None
        entrega_local_value = (local_entrega or "").strip() if entregado else None
        if local_por_item:
            try:
                locals_set = {
                    str(it.get("local") or "").strip()
                    for it in local_por_item
                    if it.get("local")
                }
                if len(locals_set) == 1:
                    entrega_local_value = list(locals_set)[0]
                elif len(locals_set) > 1:
                    entrega_local_value = "MULTI"
            except Exception:
                pass

        set_parts = [f"entrega_entregado={ph}"]
        params = [1 if entregado else 0]
        if _has_entrega_motivo_column(cur):
            set_parts.append(f"entrega_motivo={ph}")
            params.append((motivo or "").strip())
        if _has_entrega_fecha_column(cur):
            set_parts.append(f"entrega_fecha={ph}")
            params.append(entrega_fecha)
        if _has_entrega_local_column(cur):
            set_parts.append(f"entrega_local={ph}")
            params.append(entrega_local_value)
        params.append(int(venta_id))
        cur.execute(
            f"UPDATE ventas SET {', '.join(set_parts)} WHERE id={ph}",
            tuple(params),
        )

        if entregado and incluye_envio == 1 and current_entregado == 0:
            if local_por_item:
                locals_by_detail = {}
                locals_by_pid = {}
                for it in local_por_item:
                    try:
                        detail_id = int(it.get("detalle_id") or 0)
                        pid = int(it.get("producto_id") or 0)
                        qty = int(it.get("cantidad") or 0)
                        local_sel = (it.get("local") or "").strip()
                    except Exception:
                        detail_id = 0
                        pid = 0
                        qty = 0
                        local_sel = ""
                    if qty <= 0 or pid <= 0 or not local_sel:
                        continue
                    if detail_id > 0:
                        locals_by_detail.setdefault(detail_id, set()).add(local_sel)
                    elif pid > 0:
                        locals_by_pid.setdefault(pid, set()).add(local_sel)

                for detail_id, locals_set in locals_by_detail.items():
                    local_sel = list(locals_set)[0] if len(locals_set) == 1 else "MULTI"
                    try:
                        cur.execute(
                            f"""
                            UPDATE detalle_ventas
                            SET entrega_local={ph}, entrega_local_entregado=1, entrega_local_fecha={ph}
                            WHERE id={ph}
                            """,
                            (local_sel, entrega_fecha, detail_id),
                        )
                    except Exception:
                        pass

                for pid, locals_set in locals_by_pid.items():
                    local_sel = list(locals_set)[0] if len(locals_set) == 1 else "MULTI"
                    try:
                        cur.execute(
                            f"""
                            UPDATE detalle_ventas
                            SET entrega_local={ph}, entrega_local_entregado=1, entrega_local_fecha={ph}
                            WHERE venta_id={ph} AND producto_id={ph}
                            """,
                            (local_sel, entrega_fecha, int(venta_id), pid),
                        )
                    except Exception:
                        pass
                # Usar expansion para combos y locales correctos
                for it in expanded_for_decrement:
                    try:
                        pid = int(it.get("producto_id") or 0)
                        qty = int(it.get("cantidad") or 0)
                    except Exception:
                        pid = 0
                        qty = 0
                    local_sel = (it.get("local") or "").strip()
                    if pid <= 0 or qty <= 0 or not local_sel:
                        continue
                    payload = {
                        "producto_id": pid,
                        "delta": qty,
                        "usuario": usuario or (venta.get("vendedor") or "sistema"),
                        "local": local_sel,
                        "detalle": "Envio entregado",
                        "motivo": "envio",
                        "venta_id": int(venta_id),
                    }
                    try:
                        qa.enqueue_op("decrement", payload)
                    except Exception:
                        logger.exception(
                            "Error encolando decremento por entrega (expandido)"
                        )
            else:
                local_dec = (local_entrega or "").strip() or (venta.get("local") or "")
                try:
                    cur.execute(
                        f"""
                        UPDATE detalle_ventas
                        SET entrega_local={ph}, entrega_local_entregado=1, entrega_local_fecha={ph}
                        WHERE venta_id={ph}
                        """,
                        (local_dec, entrega_fecha, int(venta_id)),
                    )
                except Exception:
                    pass
                for it in expanded_for_decrement:
                    try:
                        pid = int(it.get("producto_id") or 0)
                        qty = int(it.get("cantidad") or 0)
                    except Exception:
                        pid = 0
                        qty = 0
                    local_sel = (it.get("local") or "").strip() or local_dec
                    if pid <= 0 or qty <= 0 or not local_sel:
                        continue
                    payload = {
                        "producto_id": pid,
                        "delta": qty,
                        "usuario": usuario or (venta.get("vendedor") or "sistema"),
                        "local": local_sel,
                        "detalle": "Envio entregado",
                        "motivo": "envio",
                        "venta_id": int(venta_id),
                    }
                    try:
                        qa.enqueue_op("decrement", payload)
                    except Exception:
                        logger.exception("Error encolando decremento por entrega")

        # Si hay monto pendiente en una venta con envio, al entregar se cobra automaticamente
        if entregado:
            try:
                pendiente = float(venta.get("monto_pendiente") or 0)
            except Exception:
                pendiente = 0.0
            tipo_pago = (venta.get("tipo_pago") or "").strip().lower()
            monto_cobrar = 0.0
            if pendiente > 0.01:
                monto_cobrar = pendiente
            elif tipo_pago == "domicilio":
                try:
                    monto_cobrar = float(venta.get("total") or 0)
                except Exception:
                    monto_cobrar = 0.0

            if monto_cobrar > 0.01:
                monto_cobrado_neto = _get_domicilio_payment_net_amount(
                    venta, monto_cobrar
                )
                try:
                    total = float(venta.get("total") or 0)
                except Exception:
                    total = 0.0
                try:
                    pagado_previo = float(venta.get("monto_pagado") or 0)
                except Exception:
                    pagado_previo = 0.0
                # Cobrar el resto en el momento de la entrega
                forma_final = "Domicilio"
                ahora = _now_local()
                try:
                    cur.execute(
                        f"""
                        UPDATE ventas
                        SET monto_pagado={ph}, monto_pendiente=0, tipo_pago='completo',
                            forma_pago={ph}, pago_completado_fecha={ph},
                            pago_completado_monto={ph}, pago_completado_forma={ph},
                            pago_completado_tipo='domicilio'
                        WHERE id={ph}
                        """,
                        (
                            float(max(total, pagado_previo + monto_cobrar)),
                            forma_final,
                            ahora,
                            float(monto_cobrado_neto),
                            forma_final,
                            int(venta_id),
                        ),
                    )
                except Exception:
                    logger.exception("Error actualizando pago pendiente en entrega")

                # Registrar pago final en venta_pagos si existe
                try:
                    has_venta_pagos = False
                    if is_postgres():
                        cur.execute("SELECT to_regclass('public.venta_pagos')")
                        r = cur.fetchone()
                        has_venta_pagos = bool(r and r[0])
                    else:
                        cur.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'"
                        )
                        has_venta_pagos = cur.fetchone() is not None

                    if has_venta_pagos:
                        cur.execute(
                            f"INSERT INTO venta_pagos (venta_id, forma, monto) VALUES ({ph},{ph},{ph})",
                            (int(venta_id), forma_final, float(monto_cobrado_neto)),
                        )
                except Exception:
                    logger.exception(
                        "No se pudo insertar venta_pagos en entrega (se continua igual)."
                    )

                # Registrar cobro en domicilio
                try:
                    local_cobro = (venta.get("local") or "").strip()
                    cur.execute(
                        f"""
                        INSERT INTO domicilio_pagos (venta_id, local, usuario, monto, monto_productos, created_at)
                        VALUES ({ph},{ph},{ph},{ph},{ph},{ph})
                        """,
                        (
                            int(venta_id),
                            local_cobro,
                            (usuario or "").strip(),
                            float(monto_cobrar),
                            float(monto_cobrado_neto),
                            ahora,
                        ),
                    )
                except Exception:
                    logger.exception(
                        "No se pudo registrar cobro en domicilio (se continua igual)."
                    )

        conn.commit()
        return True, "OK"
    except Exception as e:
        logger.exception("Error marcando entrega")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def anular_entrega_con_devolucion(
    venta_id: int, motivo: str, usuario: str
) -> Tuple[bool, str]:
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg
        try:
            ensure_domicilio_pagos_schema()
        except Exception:
            pass

        venta = get_venta_detalle(int(venta_id))
        if not venta:
            return False, "Venta no encontrada"

        estado = (venta.get("estado") or "").strip().lower()
        if estado == "cancelada":
            return False, "La venta ya esta cancelada"
        try:
            entrega_entregado = int(venta.get("entrega_entregado") or 0)
        except Exception:
            entrega_entregado = 0

        if entrega_entregado == 1:
            default_local = (venta.get("local") or "").strip()
            items = venta.get("items") or []
            expanded = _expand_items_for_stock(items, default_local)
            for it in expanded:
                pid = int(it.get("producto_id") or 0)
                qty = int(it.get("cantidad") or 0)
                if pid <= 0 or qty <= 0:
                    continue
                local_det = (it.get("local") or "").strip() or default_local
                payload = {
                    "producto_id": pid,
                    "delta": qty,
                    "usuario": usuario or "sistema",
                    "local": local_det,
                    "detalle": "Devolucion por envio anulado",
                    "motivo": (motivo or "").strip(),
                    "venta_id": int(venta_id),
                }
                try:
                    qa.enqueue_op("increment", payload)
                except Exception:
                    logger.exception("Error reponiendo stock por devolucion")

        # Si hubo cobros en domicilio, revertirlos a pendiente
        domicilio_cobrado = 0.0
        try:
            conn_tmp = get_conn()
            cur_tmp = conn_tmp.cursor()
            ph_tmp = "%s" if is_postgres() else "?"
            cur_tmp.execute(
                f"SELECT SUM(monto) FROM domicilio_pagos WHERE venta_id={ph_tmp}",
                (int(venta_id),),
            )
            row_sum = cur_tmp.fetchone()
            domicilio_cobrado = float(row_sum[0] or 0)
        except Exception:
            domicilio_cobrado = 0.0
        finally:
            try:
                if "conn_tmp" in locals() and conn_tmp:
                    put_conn(conn_tmp)
            except Exception:
                pass

        try:
            total_venta = float(venta.get("total") or 0)
        except Exception:
            total_venta = 0.0
        try:
            pagado_actual = float(venta.get("monto_pagado") or 0)
        except Exception:
            pagado_actual = 0.0
        try:
            pendiente_actual = float(venta.get("monto_pendiente") or 0)
        except Exception:
            pendiente_actual = 0.0
        if domicilio_cobrado > 0:
            pagado_nuevo = max(0.0, pagado_actual - domicilio_cobrado)
            pendiente_nuevo = max(0.0, pendiente_actual + domicilio_cobrado)
        else:
            pagado_nuevo = pagado_actual
            pendiente_nuevo = max(0.0, total_venta - pagado_nuevo)

        tipo_nuevo = (venta.get("tipo_pago") or "").strip().lower()
        if pendiente_nuevo > 0.01:
            if pagado_nuevo > 0.01:
                tipo_nuevo = "sena"
            else:
                tipo_nuevo = "domicilio"
        else:
            tipo_nuevo = "completo"

        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        try:
            cur.execute(
                f"DELETE FROM domicilio_pagos WHERE venta_id={ph}",
                (int(venta_id),),
            )
        except Exception:
            pass
        try:
            has_venta_pagos = False
            if is_postgres():
                cur.execute("SELECT to_regclass('public.venta_pagos')")
                r = cur.fetchone()
                has_venta_pagos = bool(r and r[0])
            else:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'"
                )
                has_venta_pagos = cur.fetchone() is not None
            if has_venta_pagos:
                cur.execute(
                    f"DELETE FROM venta_pagos WHERE venta_id={ph} AND LOWER(TRIM(forma))=LOWER(TRIM({ph}))",
                    (int(venta_id), "Domicilio"),
                )
        except Exception:
            pass
        set_parts = [f"entrega_entregado={ph}"]
        params = [0]
        if _has_entrega_motivo_column(cur):
            set_parts.append(f"entrega_motivo={ph}")
            params.append((motivo or "").strip())
        if _has_entrega_fecha_column(cur):
            set_parts.append(f"entrega_fecha={ph}")
            params.append(None)
        if _has_entrega_local_column(cur):
            set_parts.append(f"entrega_local={ph}")
            params.append(None)
        # Revertir pago a pendiente si corresponde
        set_parts.append(f"monto_pagado={ph}")
        params.append(float(pagado_nuevo))
        set_parts.append(f"monto_pendiente={ph}")
        params.append(float(pendiente_nuevo))
        set_parts.append(f"tipo_pago={ph}")
        params.append(tipo_nuevo)
        params.append(int(venta_id))
        cur.execute(
            f"UPDATE ventas SET {', '.join(set_parts)} WHERE id={ph}",
            tuple(params),
        )
        try:
            cur.execute(
                f"""
                UPDATE detalle_ventas
                SET entrega_local_entregado=0, entrega_local_fecha={ph}, entrega_local={ph}
                WHERE venta_id={ph}
                """,
                (None, None, int(venta_id)),
            )
        except Exception:
            pass
        conn.commit()
        return True, "Entrega anulada"
    except Exception as e:
        logger.exception("Error anulando entrega con devolucion")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def marcar_entrega_interlocal(
    venta_id: int,
    producto_id: int,
    local_stock: str,
    usuario: str = "",
) -> Tuple[bool, str]:
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg
        local_stock = (local_stock or "").strip()
        if not local_stock:
            return False, "Local de stock inválido"

        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"

        # Obtener cantidad pendiente
        cur.execute(
            f"""
            SELECT cantidad, COALESCE(entrega_local_entregado,0)
            FROM detalle_ventas
            WHERE venta_id={ph} AND producto_id={ph} AND stock_local={ph}
            """,
            (int(venta_id), int(producto_id), local_stock),
        )
        row = cur.fetchone()
        if not row:
            return False, "Detalle de venta no encontrado"
        qty = int(row[0] or 0)
        delivered = int(row[1] or 0)
        if delivered == 1:
            return True, "OK"

        venta = get_venta_detalle(int(venta_id))
        matching_items: List[Dict[str, Any]] = []
        if venta:
            for item in venta.get("items") or []:
                try:
                    item_pid = int(item.get("producto_id") or 0)
                except Exception:
                    item_pid = 0
                item_local = (item.get("stock_local") or "").strip()
                try:
                    item_delivered = int(item.get("entrega_local_entregado") or 0)
                except Exception:
                    item_delivered = 0
                if (
                    item_pid == int(producto_id)
                    and item_local == local_stock
                    and item_delivered == 0
                ):
                    matching_items.append(dict(item))
        expanded = (
            _expand_items_for_stock(matching_items, local_stock)
            if matching_items
            else []
        )

        entrega_fecha = _now_local()
        cur.execute(
            f"""
            UPDATE detalle_ventas
            SET entrega_local={ph}, entrega_local_entregado=1, entrega_local_fecha={ph}
            WHERE venta_id={ph} AND producto_id={ph} AND stock_local={ph}
            """,
            (local_stock, entrega_fecha, int(venta_id), int(producto_id), local_stock),
        )
        conn.commit()

        if expanded:
            for item in expanded:
                try:
                    payload_pid = int(item.get("producto_id") or 0)
                    payload_qty = int(item.get("cantidad") or 0)
                except Exception:
                    payload_pid = 0
                    payload_qty = 0
                payload_local = (item.get("local") or "").strip() or local_stock
                if payload_pid <= 0 or payload_qty <= 0 or not payload_local:
                    continue
                payload = {
                    "producto_id": payload_pid,
                    "delta": payload_qty,
                    "usuario": usuario or "sistema",
                    "local": payload_local,
                    "detalle": "Entrega interlocal",
                    "motivo": "interlocal",
                    "venta_id": int(venta_id),
                }
                try:
                    qa.enqueue_op("decrement", payload)
                except Exception:
                    logger.exception("Error encolando decremento interlocal")
        elif qty > 0:
            payload = {
                "producto_id": int(producto_id),
                "delta": qty,
                "usuario": usuario or "sistema",
                "local": local_stock,
                "detalle": "Entrega interlocal",
                "motivo": "interlocal",
                "venta_id": int(venta_id),
            }
            try:
                qa.enqueue_op("decrement", payload)
            except Exception:
                logger.exception("Error encolando decremento interlocal")

        return True, "OK"
    except Exception as e:
        logger.exception("Error marcando entrega interlocal")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def reconciliar_entregas_interlocales_pendientes(
    usuario: str = "sistema",
) -> Tuple[int, int]:
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            logger.warning(f"No se pudo reconciliar interlocales: {msg}")
            return 0, 0
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT dv.venta_id, dv.producto_id, COALESCE(dv.stock_local,'')
            FROM detalle_ventas dv
            JOIN ventas v ON v.id = dv.venta_id
            WHERE v.estado = 'completada'
              AND COALESCE(dv.stock_local,'') <> ''
              AND COALESCE(v.local,'') <> COALESCE(dv.stock_local,'')
              AND COALESCE(dv.entrega_local_entregado,0) = 0
            """
        )
        rows = cur.fetchall() or []
        processed = 0
        fixed = 0
        for venta_id_val, producto_id_val, local_stock_val in rows:
            processed += 1
            ok_mark, _ = marcar_entrega_interlocal(
                int(venta_id_val or 0),
                int(producto_id_val or 0),
                str(local_stock_val or ""),
                usuario=usuario,
            )
            if ok_mark:
                fixed += 1
        return processed, fixed
    except Exception:
        logger.exception("Error reconciliando entregas interlocales pendientes")
        return 0, 0
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def cancelar_venta_con_devolucion(
    venta_id: int,
    motivo: str,
    usuario: str,
    devolucion_items: Optional[List[Dict]] = None,
) -> Tuple[bool, str]:
    conn = None
    try:
        ok, msg = ensure_envios_schema()
        if not ok:
            return False, msg

        venta = get_venta_detalle(int(venta_id))
        if not venta:
            return False, "Venta no encontrada"

        estado = (venta.get("estado") or "").strip().lower()
        if estado == "cancelada":
            return False, "La venta ya esta cancelada"

        incluye_envio = int(venta.get("incluye_envio") or 0)
        try:
            entrega_entregado = int(venta.get("entrega_entregado") or 0)
        except Exception:
            entrega_entregado = 0
        tipo_pago = (venta.get("tipo_pago") or "").strip().lower()
        try:
            monto_pendiente = float(venta.get("monto_pendiente") or 0)
        except Exception:
            monto_pendiente = 0.0

        hold_stock = False
        if incluye_envio == 1 and entrega_entregado == 0:
            hold_stock = True
        elif incluye_envio == 0 and tipo_pago == "sena" and monto_pendiente > 0:
            hold_stock = True
        should_restore_stock = not hold_stock
        # Si el stock estaba retenido (envío pendiente o seña), no se debe reponer
        if hold_stock and devolucion_items is not None:
            devolucion_items = None

        if should_restore_stock:
            default_local = (venta.get("local") or "").strip()
            items = venta.get("items") or []
            if devolucion_items:
                target = {}
                for d in devolucion_items:
                    try:
                        pid = int(d.get("producto_id") or 0)
                        qty = int(d.get("cantidad") or 0)
                    except Exception:
                        pid = 0
                        qty = 0
                    if pid > 0 and qty > 0:
                        target[pid] = qty
                filtered = []
                for it in items:
                    try:
                        pid = int(it.get("producto_id") or 0)
                        qty = int(it.get("cantidad") or 0)
                    except Exception:
                        pid = 0
                        qty = 0
                    if pid <= 0:
                        continue
                    if pid in target:
                        it_copy = dict(it)
                        it_copy["cantidad"] = min(qty, int(target[pid]))
                        filtered.append(it_copy)
                items = filtered
            expanded = _expand_items_for_stock(items, default_local)
            for it in expanded:
                pid = int(it.get("producto_id") or 0)
                qty = int(it.get("cantidad") or 0)
                if pid <= 0 or qty <= 0:
                    continue
                detalle_motivo = (motivo or "").strip()
                prod_name = (it.get("nombre") or "").strip()
                detalle_txt = "Venta cancelada"
                if detalle_motivo:
                    detalle_txt = f"Venta cancelada: {detalle_motivo}"
                if prod_name:
                    detalle_txt = f"{detalle_txt} | Devuelto: {prod_name} x{qty}"
                else:
                    detalle_txt = f"{detalle_txt} | Devuelto x{qty}"
                if it.get("detalle_origen"):
                    detalle_txt = f"{detalle_txt} ({it.get('detalle_origen')})"
                local_det = (it.get("local") or "").strip() or default_local
                payload = {
                    "producto_id": pid,
                    "delta": qty,
                    "usuario": usuario or "sistema",
                    "local": local_det,
                    "detalle": detalle_txt,
                    "motivo": "venta cancelada",
                    "venta_id": int(venta_id),
                }
                try:
                    qa.enqueue_op("increment", payload)
                except Exception:
                    logger.exception("Error reponiendo stock por cancelacion")

        conn = get_conn()
        cur = conn.cursor()
        ph = "%s" if is_postgres() else "?"
        set_parts = ["estado='cancelada'"]
        params = []
        if _has_entrega_entregado_column(cur):
            set_parts.append(f"entrega_entregado={ph}")
            params.append(0)
        if _has_entrega_motivo_column(cur):
            set_parts.append(f"entrega_motivo={ph}")
            params.append((motivo or "").strip())
        if _has_entrega_fecha_column(cur):
            set_parts.append(f"entrega_fecha={ph}")
            params.append(None)
        if _has_entrega_local_column(cur):
            set_parts.append(f"entrega_local={ph}")
            params.append(None)
        params.append(int(venta_id))
        cur.execute(
            f"UPDATE ventas SET {', '.join(set_parts)} WHERE id={ph}",
            tuple(params),
        )
        try:
            cur.execute(
                f"""
                UPDATE detalle_ventas
                SET entrega_local_entregado=0, entrega_local_fecha={ph}, entrega_local={ph}
                WHERE venta_id={ph}
                """,
                (None, None, int(venta_id)),
            )
        except Exception:
            pass
        conn.commit()
        return True, "Venta cancelada"
    except Exception as e:
        logger.exception("Error cancelando venta con devolucion")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def devolver_productos_parcial(
    venta_id: int,
    motivo: str,
    usuario: str,
    devolucion_items: List[Dict],
) -> Tuple[bool, str]:
    conn = None
    try:
        if not devolucion_items:
            return False, "No hay productos para devolver"
        venta = get_venta_detalle(int(venta_id))
        if not venta:
            return False, "Venta no encontrada"

        default_local = (venta.get("local") or "").strip()
        items = venta.get("items") or []
        target = {}
        for d in devolucion_items:
            try:
                pid = int(d.get("producto_id") or 0)
                qty = int(d.get("cantidad") or 0)
            except Exception:
                pid = 0
                qty = 0
            if pid > 0 and qty > 0:
                target[pid] = qty

        filtered = []
        for it in items:
            try:
                pid = int(it.get("producto_id") or 0)
                qty = int(it.get("cantidad") or 0)
            except Exception:
                pid = 0
                qty = 0
            if pid <= 0 or qty <= 0 or pid not in target:
                continue
            it_copy = dict(it)
            it_copy["cantidad"] = min(qty, int(target[pid]))
            if int(it_copy["cantidad"] or 0) > 0:
                filtered.append(it_copy)

        expanded = _expand_items_for_stock(filtered, default_local)
        for it in expanded:
            pid = int(it.get("producto_id") or 0)
            qty = int(it.get("cantidad") or 0)
            if pid <= 0 or qty <= 0:
                continue
            detalle_motivo = (motivo or "").strip()
            prod_name = (it.get("nombre") or "").strip()
            detalle_txt = "Devolucion parcial"
            if detalle_motivo:
                detalle_txt = f"{detalle_txt}: {detalle_motivo}"
            if prod_name:
                detalle_txt = f"{detalle_txt} | Devuelto: {prod_name} x{qty}"
            else:
                detalle_txt = f"{detalle_txt} | Devuelto x{qty}"
            if it.get("detalle_origen"):
                detalle_txt = f"{detalle_txt} ({it.get('detalle_origen')})"
            local_det = (it.get("local") or "").strip() or default_local
            payload = {
                "producto_id": pid,
                "delta": qty,
                "usuario": usuario or "sistema",
                "local": local_det,
                "detalle": detalle_txt,
                "motivo": "devolucion parcial",
                "venta_id": int(venta_id),
            }
            try:
                qa.enqueue_op("increment", payload)
            except Exception:
                logger.exception("Error reponiendo stock por devolucion parcial")

        # Agregar nota a la venta (sin cancelar)
        try:
            conn = get_conn()
            cur = conn.cursor()
            ph = "%s" if is_postgres() else "?"
            cur.execute(f"SELECT notas FROM ventas WHERE id={ph}", (int(venta_id),))
            row = cur.fetchone()
            notas_prev = row[0] if row else ""
            detalle_motivo = (motivo or "").strip()
            notas_add = (
                f"Devolucion parcial: {detalle_motivo}"
                if detalle_motivo
                else "Devolucion parcial"
            )
            if notas_prev:
                notas_final = f"{notas_prev} | {notas_add}"
            else:
                notas_final = notas_add
            cur.execute(
                f"UPDATE ventas SET notas={ph} WHERE id={ph}",
                (notas_final, int(venta_id)),
            )
            conn.commit()
        except Exception:
            try:
                if conn and is_postgres():
                    conn.rollback()
            except Exception:
                pass

        return True, "Devolucion parcial registrada"
    except Exception as e:
        logger.exception("Error devolviendo productos parcialmente")
        try:
            if conn and is_postgres():
                conn.rollback()
        except Exception:
            pass
        return False, str(e)
    finally:
        try:
            if conn:
                put_conn(conn)
        except Exception:
            pass


def get_estadisticas_ventas(local: Optional[str] = None, periodo: str = "mes") -> Dict:
    try:
        ventas = get_ventas(local=local, filtro_periodo=periodo)
        total = len(ventas)
        ingresos = sum(v.get("total", 0) for v in ventas)
        descuentos = sum(v.get("descuento_aplicado", 0) or 0 for v in ventas)
        return {
            "total_ventas": total,
            "total_ingresos": ingresos,
            "total_descuentos": descuentos,
            "promedio_venta": ingresos / total if total > 0 else 0,
        }
    except Exception:
        return {
            "total_ventas": 0,
            "total_ingresos": 0,
            "total_descuentos": 0,
            "promedio_venta": 0,
        }
