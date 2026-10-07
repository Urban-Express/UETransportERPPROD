`urban_express_logo.png` is the static branding asset extracted without alteration
from `word/media/image1.png` in the user-supplied Sample_Invoice_For_Contract_A.docx.
It contains branding only. Customer, issuer, bank, TRN and signatory data are read
from the invoice's server-owned identity snapshot, never from the image/template.

The DejaVu fonts are bundled to avoid relying on deployment OS fonts.
See `DejaVu-LICENSE.txt`. PDF rendering uses the pinned ReportLab package and
requires no LibreOffice, Chromium or system PDF executable. Local PDF readers
used for tests/visual inspection are development tools only.
