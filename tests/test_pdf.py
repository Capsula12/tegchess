from tegchess import pdf
from tegchess.config import Config


def _cfg(tmp_path):
    return Config.model_validate({"me": "Capsula", "opponents": ["RusticoyRomantico", "Maxivo"],
                                  "data_dir": str(tmp_path / "data")})


def test_pdf_filename_uses_configured_name(tmp_path):
    cfg = _cfg(tmp_path)
    assert pdf.pdf_filename(cfg, "rusticoyromantico") == "informe-RusticoyRomantico.pdf"
    assert pdf.pdf_filename(cfg, "otro/../x") == "informe-otrox.pdf"


def test_export_pdfs_without_report(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "find_browser", lambda: "navegador")
    steps = []
    result = pdf.export_pdfs(_cfg(tmp_path), ["maxivo"], "http://127.0.0.1:1",
                             lambda desc, total: (lambda: steps.append(desc)), out_dir=tmp_path / "informes")
    assert result == {"maxivo": {"ok": False, "error": "no hay informe generado"}}
    assert steps == ["Generando PDF"]
