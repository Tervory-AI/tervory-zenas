"""One adapter per source. Refused sources have no parser that copies text."""

from __future__ import annotations

from agents.zenas.pool.adapters.cases import CapAdapter, CourtListenerAdapter
from agents.zenas.pool.adapters.ecfr import EcfrAdapter
from agents.zenas.pool.adapters.eur_lex import EurLexAdapter
from agents.zenas.pool.adapters.federal_register import FederalRegisterAdapter
from agents.zenas.pool.adapters.govinfo import FrapAdapter, FrcpAdapter, FreAdapter, UsCodeAdapter
from agents.zenas.pool.adapters.htsus import HtsusAdapter
from agents.zenas.pool.adapters.states import DelawareAdapter, NewYorkAdapter, TexasAdapter
from agents.zenas.pool.adapters.uncitral import CisgAdapter, ModelLawAdapter
from agents.zenas.pool.adapters.za_akn import laws_africa_adapters
from agents.zenas.pool.adapters.za_cac import CacAdapter
from agents.zenas.pool.adapters.za_comptrib import ComptribAdapter
from agents.zenas.pool.adapters.za_concourt_oai import ConcourtAdapter
from agents.zenas.pool.adapters.za_labour import LabourAppealAdapter, LabourCourtAdapter
from agents.zenas.pool.adapters.za_landcourt import LandCourtAdapter
from agents.zenas.pool.adapters.za_ocj_highcourt import OcjHighCourtAdapter
from agents.zenas.pool.adapters.za_gazette import GovzaRegulationsAdapter
from agents.zenas.pool.adapters.za_govza import GovzaActsAdapter
from agents.zenas.pool.adapters.za_official import GazetteAdapter, MagistratesRulesAdapter, UniformRulesAdapter
from agents.zenas.pool.adapters.za_saflii import SafliiAdapter
from agents.zenas.pool.adapters.za_sars_taxcourt import SarsTaxCourtAdapter
from agents.zenas.pool.adapters.za_sca_jdownloads import ScaAdapter
from agents.zenas.pool.adapters.za_special_tribunal import SpecialTribunalAdapter
from agents.zenas.pool.licence import FetchNotConfigured, LicenceError
from agents.zenas.pool.model import LegalDocument, SourceSpec

_ADAPTERS = {
    adapter.source_id: adapter
    for adapter in (
        UsCodeAdapter(),
        EcfrAdapter(),
        FederalRegisterAdapter(),
        EurLexAdapter(),
        HtsusAdapter(),
        CourtListenerAdapter(),
        CapAdapter(),
        FrcpAdapter(),
        FreAdapter(),
        FrapAdapter(),
        DelawareAdapter(),
        NewYorkAdapter(),
        TexasAdapter(),
        CisgAdapter(),
        ModelLawAdapter(),
        *laws_africa_adapters(),
        SafliiAdapter(),
        GazetteAdapter(),
        GovzaActsAdapter(),
        GovzaRegulationsAdapter(),
        UniformRulesAdapter(),
        MagistratesRulesAdapter(),
        ConcourtAdapter(),
        ScaAdapter(),
        CacAdapter(),
        ComptribAdapter(),
        SarsTaxCourtAdapter(),
        LandCourtAdapter(),
        OcjHighCourtAdapter(),
        LabourCourtAdapter(),
        LabourAppealAdapter(),
        SpecialTribunalAdapter(),
    )
}


class RefusedAdapter:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    async def fetch(self, http_get) -> list[bytes]:
        raise LicenceError(f"{self.source_id} is link-only")

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        raise LicenceError(f"{self.source_id} is link-only")


def get_adapter(source: SourceSpec):
    if source.adapter == "refused":
        return RefusedAdapter(source.source_id)
    try:
        return _ADAPTERS[source.adapter]
    except KeyError as exc:
        raise FetchNotConfigured(f"no adapter for {source.source_id}") from exc
