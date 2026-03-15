# -*- coding: utf-8 -*-
import re
from odoo import api, models, _
from odoo.exceptions import ValidationError

def _iban_is_valid(iban: str) -> bool:
    iban = (iban or "").replace(" ", "").upper()
    if not iban or len(iban) < 15 or len(iban) > 34:
        return False
    if not re.match(r"^[A-Z0-9]+$", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = ""
    for ch in rearranged:
        digits += ch if ch.isdigit() else str(ord(ch) - 55)  # A=10..Z=35
    mod = 0
    for c in digits:
        mod = (mod * 10 + int(c)) % 97
    return mod == 1

class PartnerComplianceRules(models.AbstractModel):
    _name = "partner.compliance.rules"
    _description = "Partner Compliance Rules"

    @api.model
    def validate_inquiry_for_approval(self, inquiry):
        """Raise ValidationError if inquiry is not admissible for approval."""
        inquiry.ensure_one()

        if not inquiry.applicant_name:
            raise ValidationError(_("Full Name is required."))

        if not inquiry.email:
            raise ValidationError(_("Email is required for approval (portal login)."))

        # Company/commercial roles: require at least one identifier
        if inquiry.partner_role in ("lead", "sales_agent", "sales_partner"):
            if not (inquiry.vat or inquiry.coc):
                raise ValidationError(_("For this role, provide VAT/Tax ID or CoC number."))

        # Sales roles: require valid IBAN + docs
        if inquiry.partner_role in ("sales_agent", "sales_partner"):
            if not inquiry.iban:
                raise ValidationError(_("IBAN is required for this role."))
            if not _iban_is_valid(inquiry.iban):
                raise ValidationError(_("Invalid IBAN (checksum failed)."))
            if not inquiry.attachment_ids:
                raise ValidationError(_("Documents are required before approval."))

        # Optional sanity for IRS field (light)
        if inquiry.irs and len(inquiry.irs.strip()) < 3:
            raise ValidationError(_("IRS/Tax Ref looks too short."))

        return True
