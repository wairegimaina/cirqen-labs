"""CalSoft.pdf_generators package (split from the former single pdf_generators.py)."""

from .watermark import (
    _LogoWatermarkCanvas,
)
from .signatures import (
    SignatureImageLoader,
    verify_user_signature_before_pdf,
    diagnose_signature_issue,
    diagnose_signature,
)
from .certificate import (
    BtwelveHospitalCertificateGenerator,
    generate_btwelve_certificate,
)
from .verification import (
    verify_certificate_qr_with_results,
    generate_verification_report,
    verify_certificate_qr,
    generate_verification_url,
)

__all__ = [
    "_LogoWatermarkCanvas",
    "SignatureImageLoader",
    "verify_user_signature_before_pdf",
    "diagnose_signature_issue",
    "diagnose_signature",
    "BtwelveHospitalCertificateGenerator",
    "generate_btwelve_certificate",
    "verify_certificate_qr_with_results",
    "generate_verification_report",
    "verify_certificate_qr",
    "generate_verification_url",
]
