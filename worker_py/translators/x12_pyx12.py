"""pyx12-backed translator for HIPAA X12 healthcare files."""
from __future__ import annotations

import importlib
import io
import logging
import os
import shutil
import uuid
from datetime import datetime
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from xml.etree import ElementTree

import pkg_resources

from . import AckRecord, TranslationOutcome, Translator, register
from ._ack_helpers import generate_simple_277ca, generate_simple_999

logger = logging.getLogger(__name__)


_DEFAULT_MAP_DIR = Path(__file__).resolve().parents[2] / "x12_maps"


@dataclass(slots=True)
class _PyX12Support:
    params_mod: object
    x12file_mod: object
    map_if_mod: object
    x12n_document_mod: object

    def _new_params(self):
        params_cls = getattr(self.params_mod, "params", None)
        if params_cls is None:
            raise RuntimeError("pyx12.params.params not available")
        params = params_cls()
        raw_override = os.getenv("PYX12_MAP_PATH")
        if raw_override:
            try:
                map_dir = Path(raw_override).expanduser()
            except Exception:
                map_dir = None
            if map_dir and map_dir.exists():
                setter = getattr(params, "set", None)
                if callable(setter):
                    try:
                        setter("map_path", str(map_dir))
                    except Exception:
                        pass
                else:
                    try:
                        setattr(params, "map_path", str(map_dir))
                    except Exception:
                        pass
        return params

    def iter_segments(self, text: str):
        reader_cls = getattr(self.x12file_mod, "X12Reader", None)
        if reader_cls is None:
            reader_cls = getattr(self.x12file_mod, "x12file", None)
        if reader_cls is None:
            raise RuntimeError("pyx12.x12file does not expose X12Reader/x12file")
        stream = io.StringIO(text)
        reader = reader_cls(stream)
        get_seg = getattr(reader, "__iter__", None)
        if get_seg is None:
            get_seg = getattr(reader, "iter_segments", None)
        if get_seg is None:
            raise RuntimeError("pyx12 reader lacks iterator")
        for seg in get_seg():
            yield seg

    def _synthetic_acknowledgements(
        self,
        *,
        job_uuid: uuid.UUID,
        trading_partner_id: str | None,
        claim_count: int,
        isa_control: str | None,
        gs_functional_code: str | None,
        st_code: str | None,
    ) -> list[AckRecord]:
        records: list[AckRecord] = []
        try:
            ack_999 = generate_simple_999(
                job_uuid=job_uuid,
                trading_partner_id=trading_partner_id,
                total_claims=claim_count,
                isa_control=isa_control,
                gs_functional_code=gs_functional_code,
                st_code=st_code,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.debug("synthetic 999 generation failed: %s", exc)
        else:
            if ack_999:
                records.append(AckRecord("999", ack_999))
        if claim_count:
            try:
                ack_277 = generate_simple_277ca(
                    job_uuid=job_uuid,
                    trading_partner_id=trading_partner_id,
                    total_claims=claim_count,
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.debug("synthetic 277CA generation failed: %s", exc)
            else:
                if ack_277:
                    records.append(AckRecord("277CA", ack_277))
        return records

    def _map_not_found_fallback(
        self,
        text: str,
        *,
        job_uuid: uuid.UUID,
        trading_partner_id: str | None,
        claim_count: int,
        isa_control: str | None,
        gs_functional_code: str | None,
        st_code: str | None,
        error_message: str,
    ) -> list[AckRecord]:
        """Produce acknowledgements when pyx12 reports a missing map."""

        details: dict[str, str] = {}
        for raw_part in error_message.split(","):
            if "=" not in raw_part:
                continue
            key, value = raw_part.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key and value:
                details[key] = value

        vriic = details.get("vriic")
        fic = details.get("fic")
        icvn = details.get("icvn")
        st_clean = (st_code or "").strip()
        gs_clean = (gs_functional_code or "").strip()
        fic_upper = (fic or "").strip().upper()
        vriic_upper = (vriic or "").strip().upper()

        is_eligibility = False
        if st_clean == "270" or gs_clean == "HS":
            is_eligibility = True
        elif fic_upper == "HS":
            is_eligibility = True
        elif "X279" in vriic_upper or "270" in vriic_upper:
            is_eligibility = True

        if "005010X224A2" in vriic_upper:
            fallback_837 = self._generate_837d_ack(
                text,
                job_uuid=job_uuid,
                trading_partner_id=trading_partner_id,
            )
            if fallback_837:
                logger.info(
                    "pyx12 map %s unavailable; generating synthetic 837D acknowledgement for job %s",
                    vriic or "005010X224A2",
                    job_uuid,
                )
                notice_text = (
                    f"pyx12 map {vriic or '005010X224A2'} not found; generated synthetic 999 acknowledgement without schema validation"
                )
                return [AckRecord("999", fallback_837), AckRecord("NOTICE", notice_text)]

        if is_eligibility:
            context = "eligibility (270/271)"
            logger.info(
                "pyx12 eligibility map unavailable; generating synthetic acknowledgements for job %s",
                job_uuid,
            )
        elif st_clean:
            context = f"{st_clean} transaction"
            logger.info(
                "pyx12 map unavailable for %s; generating synthetic acknowledgements for job %s",
                context,
                job_uuid,
            )
        elif vriic:
            context = vriic
            logger.info(
                "pyx12 map unavailable for %s; generating synthetic acknowledgements for job %s",
                context,
                job_uuid,
            )
        elif fic_upper:
            context = f"functional code {fic_upper}"
            logger.info(
                "pyx12 map unavailable for %s; generating synthetic acknowledgements for job %s",
                context,
                job_uuid,
            )
        elif icvn:
            context = f"version {icvn}"
            logger.info(
                "pyx12 map unavailable for %s; generating synthetic acknowledgements for job %s",
                context,
                job_uuid,
            )
        else:
            context = "transaction"
            logger.info(
                "pyx12 map unavailable; generating synthetic acknowledgements for job %s",
                job_uuid,
            )

        fallback_records = self._synthetic_acknowledgements(
            job_uuid=job_uuid,
            trading_partner_id=trading_partner_id,
            claim_count=claim_count,
            isa_control=isa_control,
            gs_functional_code=gs_functional_code,
            st_code=st_code,
        )
        trimmed_error = " ".join(error_message.split())
        notice_text = (
            f"pyx12 map not found for {context}; generated synthetic acknowledgements without schema validation (pyx12: {trimmed_error})"
        )
        if fallback_records:
            fallback_records.append(AckRecord("NOTICE", notice_text))
            return fallback_records
        return [AckRecord("NOTICE", notice_text)]

    def generate_acks(
        self,
        text: str,
        *,
        job_uuid: uuid.UUID,
        trading_partner_id: str | None,
        claim_count: int = 0,
        isa_control: str | None = None,
        gs_functional_code: str | None = None,
        st_code: str | None = None,
    ) -> list[AckRecord]:
        ack_records: list[AckRecord] = []
        ack_buffer = io.StringIO()
        html_buffer = io.StringIO()
        param = self._new_params()
        map_path = None
        param_get = getattr(param, "get", None)
        if callable(param_get):
            try:
                candidate = param_get("map_path")
            except Exception:
                candidate = None
            if candidate:
                try:
                    if Path(candidate).exists():
                        map_path = candidate
                except Exception:
                    map_path = None
        try:
            ok = self.x12n_document_mod.x12n_document(
                param=param,
                src_file=io.StringIO(text),
                fd_997=ack_buffer,
                fd_html=html_buffer,
                map_path=map_path,
            )
        except Exception as exc:
            message = str(exc)
            if "Map not found" in message:
                fallback = self._map_not_found_fallback(
                    text,
                    job_uuid=job_uuid,
                    trading_partner_id=trading_partner_id,
                    claim_count=claim_count,
                    isa_control=isa_control,
                    gs_functional_code=gs_functional_code,
                    st_code=st_code,
                    error_message=message,
                )
                if fallback:
                    return fallback
            diagnostic = f"pyx12 validation failed: {exc}"
            logger.warning("pyx12 x12n_document raised while processing job %s: %s", job_uuid, exc)
            fallback = self._synthetic_acknowledgements(
                job_uuid=job_uuid,
                trading_partner_id=trading_partner_id,
                claim_count=claim_count,
                isa_control=isa_control,
                gs_functional_code=gs_functional_code,
                st_code=st_code,
            )
            if fallback:
                notice = (
                    f"pyx12 validation failed ({exc}); generated synthetic acknowledgements without schema validation"
                )
                fallback.append(AckRecord("NOTICE", notice))
                return fallback
            return [AckRecord("ERROR", diagnostic)]

        ack_text = ack_buffer.getvalue().strip()
        html_text = html_buffer.getvalue().strip()

        if ack_text:
            upper_text = ack_text.upper()
            if "ST*999" in upper_text:
                ack_type = "999"
            elif "ST*277" in upper_text:
                ack_type = "277CA"
            elif "ST*997" in upper_text:
                ack_type = "997"
            else:
                ack_type = "ACK"
            ack_records.append(AckRecord(ack_type, ack_text))

        if not ack_records and html_text:
            ack_records.append(AckRecord("ERROR" if not ok else "NOTICE", html_text))

        if not ack_records:
            fallback_text = (
                "pyx12 validation succeeded but no acknowledgement content was produced"
                if ok
                else "pyx12 validation failed without acknowledgement content"
            )
            ack_records.append(AckRecord("ERROR" if not ok else "NOTICE", fallback_text))

        return ack_records

    def _generate_837d_ack(
        self,
        text: str,
        *,
        job_uuid: uuid.UUID,
        trading_partner_id: str | None,
    ) -> str | None:
        """Build a minimal 999 acknowledgement for 837D claims when pyx12 lacks maps."""

        def _to_list(segment) -> list[str]:
            if isinstance(segment, (tuple, list)) and segment:
                tag = str(segment[0])
                rest = ["" if part is None else str(part) for part in segment[1:]]
                return [tag] + rest
            get_seg_id = getattr(segment, "get_seg_id", None)
            tag = ""
            if callable(get_seg_id):
                try:
                    tag = str(get_seg_id())
                except Exception:
                    tag = ""
            if not tag:
                raw_tag = getattr(segment, "tag", None)
                if raw_tag is not None:
                    tag = str(raw_tag)
            elements = ["" if part is None else str(part) for part in getattr(segment, "elements", [])]
            return ([tag] if tag else []) + elements

        isa_sender = isa_receiver = isa_ctrl = None
        gs_function = gs_sender = gs_receiver = gs_ctrl = gs_version = None
        st_code = st_ctrl = st_version = None

        try:
            for raw in self.iter_segments(text):
                parts = _to_list(raw)
                if not parts:
                    continue
                seg_id = parts[0].strip().upper()
                if seg_id == "ISA" and len(parts) >= 16:
                    isa_sender = parts[6].strip()
                    isa_receiver = parts[8].strip()
                    isa_ctrl = parts[13].strip()
                elif seg_id == "GS" and len(parts) >= 9:
                    gs_function = parts[1].strip() or "HC"
                    gs_sender = parts[2].strip()
                    gs_receiver = parts[3].strip()
                    gs_date = parts[4].strip()
                    gs_time = parts[5].strip()
                    gs_ctrl = parts[6].strip()
                    gs_version = parts[8].strip()
                elif seg_id == "ST" and len(parts) >= 3:
                    st_code = parts[1].strip() or "837"
                    st_ctrl = parts[2].strip()
                    if len(parts) >= 4:
                        st_version = parts[3].strip() or None
                if isa_sender and gs_sender and st_ctrl:
                    break
        except Exception as exc:
            logger.warning("unable to extract dental interchange metadata: %s", exc)
            return None

        if not isa_sender or not isa_receiver:
            return None

        now = datetime.utcnow()
        ack_sender = (isa_receiver or "HEDIRECEIVER")[:15].ljust(15)
        ack_receiver = (isa_sender or "HEDISENDER")[:15].ljust(15)
        ack_isa_ctrl = f"{abs(hash((job_uuid, isa_ctrl))) % 1_000_000_000:09d}"
        ack_gs_ctrl = f"{abs(hash((job_uuid, gs_ctrl))) % 1_000_000 + 1:06d}".lstrip("0") or "1"
        ack_st_ctrl = f"{abs(hash((job_uuid, st_ctrl))) % 10_000:04d}" or "0001"

        gs_sender_out = (gs_receiver or ack_sender.strip() or "HEDIACK").strip() or "HEDIACK"
        gs_receiver_out = (gs_sender or ack_receiver.strip() or "HEDICLIENT").strip() or "HEDICLIENT"

        ack_lines = [
            "ISA*00*          *00*          *ZZ*{}*ZZ*{}*{}*{}*^*00501*{}*0*T*:~".format(
                ack_sender,
                ack_receiver,
                now.strftime("%y%m%d"),
                now.strftime("%H%M"),
                ack_isa_ctrl,
            ),
            "GS*FA*{}*{}*{}*{}*{}*X*005010X231A1~".format(
                gs_sender_out[:15],
                gs_receiver_out[:15],
                now.strftime("%Y%m%d"),
                now.strftime("%H%M"),
                ack_gs_ctrl,
            ),
            f"ST*999*{ack_st_ctrl}*005010X231A1~",
            f"AK1*{gs_function or 'HC'}*{gs_ctrl or ack_gs_ctrl}~",
            f"AK2*{st_code or '837'}*{st_ctrl or ack_st_ctrl}*{st_version or '005010X224A2'}~",
            "IK5*A~",
            "AK9*A*1*1*1~",
            f"SE*6*{ack_st_ctrl}~",
            f"GE*1*{ack_gs_ctrl}~",
            f"IEA*1*{ack_isa_ctrl}~",
        ]

        return "\n".join(ack_lines)

_SUPPORT_ERROR: str | None = None
_MODULE_ROOT = Path(__file__).resolve().parent
_CUSTOM_MAP_DIRS = (
    _MODULE_ROOT.parent / "pyx12_maps",
    _DEFAULT_MAP_DIR,
)


@dataclass(slots=True)
class _CustomMapDefinition:
    icvn: str
    vriic: str
    fic: str
    tspc: str | None
    filename: str
    abbr: str | None
    path: Path


_CUSTOM_MAP_DEFS: list[_CustomMapDefinition] = []


def _ensure_pyx12_ak2_patch() -> None:
    """Patch pyx12's 999 generator to tolerate missing ST03 values.

    Older or non-standard X12 files sometimes omit the ST03 element even when
    the functional group supplies the implementation convention reference.  In
    those cases pyx12 raises ``EngineError('Cannot create AK2: err_st.vriic was
    not set')`` while building the 999 acknowledgement.  We still want to return
    an acknowledgement, so we monkey-patch the visitor to fall back to the
    parent's VRIIC (GS08) when ST03 is not present.
    """

    try:
        from pyx12.error_999 import error_999_visitor  # type: ignore
    except Exception:
        return

    visit_st_pre = getattr(error_999_visitor, "visit_st_pre", None)
    if visit_st_pre is None:
        return

    if getattr(visit_st_pre, "_hedi_patched", False):
        return

    @wraps(visit_st_pre)
    def patched_visit_st_pre(self, err_st):  # type: ignore[override]
        current_vriic = getattr(err_st, "vriic", None) if err_st is not None else None
        if err_st is not None and (current_vriic is None or str(current_vriic).strip() == ""):
            fallback = getattr(err_st.parent, "vriic", None)
            if fallback is not None and str(fallback).strip() == "":
                fallback = None
            if fallback is None:
                fallback = getattr(self, "vriic", None)
                if fallback is not None and str(fallback).strip() == "":
                    fallback = None
            if fallback is None:
                fallback = "005010X231"
            try:
                err_st.vriic = fallback
            except Exception:
                pass
        return visit_st_pre(self, err_st)

    patched_visit_st_pre._hedi_patched = True  # type: ignore[attr-defined]
    error_999_visitor.visit_st_pre = patched_visit_st_pre


def _load_custom_map_definitions() -> list[_CustomMapDefinition]:
    """Load custom map metadata from local directories."""

    definitions: list[_CustomMapDefinition] = []
    seen: set[Path] = set()
    fallback_maps = (
        ("00501", "005010X224A2", "HC", None, "837.5010.X224.A2.xml", "837D"),
    )

    for directory in _CUSTOM_MAP_DIRS:
        if not directory.exists():
            continue

        index_path = directory / "map_index.xml"
        if index_path.exists():
            try:
                root = ElementTree.parse(index_path).getroot()
            except Exception as exc:
                logger.warning("failed to parse custom pyx12 map index %s: %s", index_path, exc)
            else:
                for version_elem in root.findall("version"):
                    icvn = (version_elem.get("icvn") or "").strip()
                    if not icvn:
                        continue
                    for map_elem in version_elem.findall("map"):
                        map_file = (map_elem.text or "").strip()
                        if not map_file:
                            continue
                        path = (directory / map_file).resolve()
                        if not path.exists():
                            logger.warning(
                                "pyx12 custom map %s referenced in %s but missing", path, index_path
                            )
                            continue
                        if path in seen:
                            continue
                        vriic = (map_elem.get("vriic") or "").strip()
                        fic = (map_elem.get("fic") or "").strip()
                        if not vriic or not fic:
                            logger.warning(
                                "skipping custom map %s: missing required attributes (vriic=%s fic=%s)",
                                map_file,
                                vriic,
                                fic,
                            )
                            continue
                        tspc = map_elem.get("tspc")
                        if tspc is not None:
                            tspc = tspc.strip() or None
                        abbr = map_elem.get("abbr")
                        if abbr is not None:
                            abbr = abbr.strip() or None
                        definitions.append(
                            _CustomMapDefinition(
                                icvn=icvn,
                                vriic=vriic,
                                fic=fic,
                                tspc=tspc,
                                filename=map_file,
                                abbr=abbr,
                                path=path,
                            )
                        )
                        seen.add(path)

        for icvn, vriic, fic, tspc, filename, abbr in fallback_maps:
            candidate_path = (directory / filename).resolve()
            if not candidate_path.exists() or candidate_path in seen:
                continue
            definitions.append(
                _CustomMapDefinition(
                    icvn=icvn,
                    vriic=vriic,
                    fic=fic,
                    tspc=tspc,
                    filename=filename,
                    abbr=abbr,
                    path=candidate_path,
                )
            )
            seen.add(candidate_path)

    return definitions


def _copy_custom_maps_into_package(definitions: list[_CustomMapDefinition]) -> None:
    try:
        package_map_dir = Path(pkg_resources.resource_filename("pyx12", "map"))
    except Exception:
        package_map_dir = None

    if not package_map_dir:
        return

    for definition in definitions:
        if not definition.path.exists():
            continue
        dest = package_map_dir / definition.filename
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(definition.path, dest)
        except Exception as exc:
            logger.warning(
                "failed to copy pyx12 map %s into package at %s: %s",
                definition.path,
                dest,
                exc,
            )


def _ensure_custom_maps(map_index_mod: object) -> None:
    """Expose HEDI-supplied pyx12 maps and register them with the map index."""

    map_index_cls = getattr(map_index_mod, "map_index", None)
    if map_index_cls is None:
        return

    custom_maps = _load_custom_map_definitions()
    if not custom_maps:
        return

    global _CUSTOM_MAP_DEFS
    _CUSTOM_MAP_DEFS = custom_maps

    _copy_custom_maps_into_package(_CUSTOM_MAP_DEFS)

    if not getattr(pkg_resources, "_hedi_custom_map_stream", False):
        original_resource_stream = pkg_resources.resource_stream

        def patched_resource_stream(package_or_requirement, resource_name):  # type: ignore[override]
            normalized = str(resource_name).replace("\\", "/")
            package_name = str(package_or_requirement)
            if package_name == "pyx12" or package_name.startswith("pyx12"):
                for definition in _CUSTOM_MAP_DEFS:
                    if normalized == f"map/{definition.filename}" and definition.path.exists():
                        return open(definition.path, "rb")
            return original_resource_stream(package_or_requirement, resource_name)

        pkg_resources.resource_stream = patched_resource_stream  # type: ignore[assignment]
        pkg_resources._hedi_custom_map_stream = True  # type: ignore[attr-defined]

    if getattr(map_index_cls, "_hedi_custom_maps", False):
        return

    original_init = map_index_cls.__init__

    def patched_init(self, base_path=None):  # type: ignore[override]
        original_init(self, base_path)
        for definition in _CUSTOM_MAP_DEFS:
            try:
                existing = self.get_filename(definition.icvn, definition.vriic, definition.fic, definition.tspc)
            except Exception:
                existing = None
            if existing:
                continue
            abbr = definition.abbr or definition.filename
            try:
                self.add_map(
                    definition.icvn,
                    definition.vriic,
                    definition.fic,
                    definition.tspc,
                    definition.filename,
                    abbr,
                )
            except Exception as exc:
                logger.warning("failed to register pyx12 custom map %s: %s", definition.filename, exc)

    map_index_cls.__init__ = patched_init  # type: ignore[assignment]
    map_index_cls._hedi_custom_maps = True  # type: ignore[attr-defined]

def _load_support() -> _PyX12Support | None:
    global _SUPPORT_ERROR
    try:
        params_mod = importlib.import_module("pyx12.params")
        x12file_mod = importlib.import_module("pyx12.x12file")
        map_if_mod = importlib.import_module("pyx12.map_if")
        x12n_document_mod = importlib.import_module("pyx12.x12n_document")
        map_index_mod = importlib.import_module("pyx12.map_index")

        _ensure_pyx12_ak2_patch()
        _ensure_custom_maps(map_index_mod)
        return _PyX12Support(
            params_mod=params_mod,
            x12file_mod=x12file_mod,
            map_if_mod=map_if_mod,
            x12n_document_mod=x12n_document_mod,
        )
    except Exception as exc:
        _SUPPORT_ERROR = f"{type(exc).__name__}: {exc}"
        logger.info(
            "pyx12 translator disabled; ensure pyx12 and its maps are installed (%s)",
            _SUPPORT_ERROR,
        )
        return None

class PyX12Translator:
    name = "pyx12-x12"

    def __init__(self) -> None:
        self._support = _load_support()
        self._last_error = _SUPPORT_ERROR

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return self._support is not None and detected_type.startswith("X12")

    def _extract_claims(self, text: str) -> tuple[list[dict], str | None, str | None, str | None]:
        support = self._support
        if support is None:
            raise RuntimeError("pyx12 support not loaded")
        claims: list[dict] = []
        isa_ctrl: str | None = None
        gs_functional: str | None = None
        st_code: str | None = None
        try:
            for seg in support.iter_segments(text):
                tag = None
                elements = None
                if isinstance(seg, (tuple, list)) and seg:
                    tag = str(seg[0]).strip().upper()
                    elements = list(seg)
                else:
                    get_seg_id = getattr(seg, "get_seg_id", None)
                    if callable(get_seg_id):
                        try:
                            tag = str(get_seg_id()).strip().upper()
                        except Exception:
                            tag = None
                    if not tag:
                        tag = str(getattr(seg, "tag", "")).strip().upper()
                    elements = [None] + list(getattr(seg, "elements", []))
                if not tag:
                    continue
                if tag == "ISA" and len(elements) >= 14:
                    isa_ctrl = str(elements[13])
                elif tag == "GS" and len(elements) >= 2 and not gs_functional:
                    gs_functional = str(elements[1])
                elif tag == "ST" and len(elements) >= 2 and not st_code:
                    st_code = str(elements[1])
                if tag == "CLM":
                    claim_id = str(elements[1]) if len(elements) > 1 else None
                    amount = None
                    if len(elements) > 2:
                        try:
                            amount = float(str(elements[2]))
                        except Exception:
                            amount = None
                    claims.append({
                        "claim_id": claim_id,
                        "amount": amount,
                        "raw": "*".join(str(el) for el in elements if el is not None),
                    })
        except Exception as exc:
            logger.warning("pyx12 parsing failed; no claims extracted: %s", exc)
        return claims, isa_ctrl, gs_functional, st_code

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        support = self._support
        if support is None:
            raise RuntimeError("pyx12 support unavailable")
        claims, isa_ctrl, gs_functional, st_code = self._extract_claims(text)
        acknowledgements = support.generate_acks(
            text,
            job_uuid=job_uuid,
            trading_partner_id=trading_partner_id,
            claim_count=len(claims),
            isa_control=isa_ctrl,
            gs_functional_code=gs_functional,
            st_code=st_code,
        )
        self._last_error = None
        for ack in acknowledgements:
            if ack.ack_type in {"ERROR", "NOTICE"} and ack.content:
                if ack.content.startswith("pyx12 validation failed"):
                    self._last_error = ack.content
                    break
                if ack.ack_type == "ERROR":
                    self._last_error = ack.content
                    break
        if not acknowledgements:
            logger.info("pyx12 did not return acknowledgements; falling back to notice")
            acknowledgements = [AckRecord("NOTICE", "pyx12 failed to produce ack")]
        return TranslationOutcome(
            file_type="X12_837_or_other",
            claims=claims,
            acknowledgements=acknowledgements,
        )

    def diagnostics(self) -> dict[str, object]:
        return {
            "name": self.name,
            "available": self._support is not None,
            "error": self._last_error,
        }


register(PyX12Translator())
