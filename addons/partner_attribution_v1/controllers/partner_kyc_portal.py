# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
from odoo.exceptions import UserError

class PartnerKYCPortal(http.Controller):

    @http.route("/partners/portal/kyc", type="http", auth="user", website=True)
    def portal_kyc_page(self, **kw):
        partner = request.env.user.partner_id.commercial_partner_id
        banks = request.env["res.partner.bank"].sudo().search([("partner_id", "=", partner.id)], limit=1)
        return request.render("partner_attribution_v1.portal_partner_kyc", {
            "partner": partner,
            "bank": banks,
        })

    @http.route("/partners/portal/kyc/save", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def portal_kyc_save(self, **post):
        partner = request.env.user.partner_id.commercial_partner_id.sudo()

        # Basic fields (safe)
        vals = {}
        name = (post.get("name") or "").strip()
        phone = (post.get("phone") or "").strip()
        vat = (post.get("vat") or "").strip()
        coc = (post.get("coc_number") or "").strip()
        irs = (post.get("irs_tax_ref") or "").strip()

        if name:
            vals["name"] = name
        vals["phone"] = phone or False

        # Only set VAT if field exists (community/enterprise safe)
        if "vat" in partner._fields:
            vals["vat"] = vat or False

        # Your custom fields from requirement #3
        if "coc_number" in partner._fields:
            vals["coc_number"] = coc or False
        if "irs_tax_ref" in partner._fields:
            vals["irs_tax_ref"] = irs or False

        if vals:
            partner.write(vals)

        # IBAN → res.partner.bank
        iban = (post.get("iban") or "").replace(" ", "").upper().strip()
        if iban:
            Bank = request.env["res.partner.bank"].sudo()
            bank = Bank.search([("partner_id", "=", partner.id)], limit=1)
            if bank:
                bank.write({"acc_number": iban})
            else:
                Bank.create({"partner_id": partner.id, "acc_number": iban})

        # If user clicked "Submit for review"
        if post.get("submit_kyc") == "1":
            if "kyc_status" in partner._fields:
                partner.write({"kyc_status": "pending"})

        return request.redirect("/partners/portal/kyc")

    @http.route("/partners/portal/kyc/upload", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def portal_kyc_upload(self, **post):
        partner = request.env.user.partner_id.commercial_partner_id.sudo()
        
        upload = post.get("document")
        
        if not upload or not getattr(upload, 'filename', False):
            return request.redirect("/partners/portal/kyc?error=no_file")

        content = upload.read()
        if len(content) > 10 * 1024 * 1024:
            return request.redirect("/partners/portal/kyc?error=file_too_large")
            
        allowed_mimetypes = ['application/pdf', 'image/jpeg', 'image/png']
        if upload.mimetype not in allowed_mimetypes:
            return request.redirect("/partners/portal/kyc?error=invalid_type")

        filename = upload.filename
        try:
            from .partner_portal import _safe_filename
            filename = _safe_filename(filename)
        except Exception:
            pass
        
        import base64
        
        # 2-Step Attachment Bypass for KYC
        attachment = request.env["ir.attachment"].sudo().create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(content),
            "mimetype": upload.mimetype,
        })
        
        attachment.sudo().write({
            "res_model": "res.partner",
            "res_id": partner.id,
        })

        return request.redirect("/partners/portal/kyc")
